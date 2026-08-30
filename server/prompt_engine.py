"""Visual Composer、Prompt IR、模型调用与最终 Prompt Compiler。"""
import json
import re

from fastapi import HTTPException

from server.knowledge import (
    _character_names,
    _normalize_character_candidate,
    lookup_character,
    match_characters,
    match_dict_words,
)
from server.lora import (
    _coerce_llm_lora_choices,
    _lora_multi_relation_names,
    _lora_tag_key,
    apply_lora_intent_hints,
    build_lora_context,
    compile_lora_bindings,
    get_lora_registry,
    lora_selection_aliases,
    resolve_lora_selections,
)
from server.runtime import CLIENT
from server.settings import (
    CFG,
    DEFAULT_COMPLETION_LEVEL,
    MAX_CONCEPT_CHARS,
    MAX_PROMPT_EN_CHARS,
    MAX_USER_PROMPT_CHARS,
    _normalize_completion_level,
)


_CHARACTER_APPEARANCE_LOCKS_PREFIX = "USER-LOCKED CHARACTER APPEARANCE OVERRIDES:"

_CHARACTER_COLOR_SPECS = (
    ("black", ("black",), ("黑", "墨黑")),
    ("white", ("white",), ("白",)),
    ("silver", ("silver",), ("银",)),
    ("blonde", ("blonde", "blond", "golden"), ("金", "金黄")),
    ("pink", ("pink",), ("粉", "粉红")),
    ("red", ("red",), ("红",)),
    ("blue", ("blue",), ("蓝", "蔚蓝", "湛蓝")),
    ("green", ("green",), ("绿", "翠绿")),
    ("purple", ("purple", "violet"), ("紫",)),
    ("brown", ("brown",), ("棕", "褐")),
    ("gray", ("gray", "grey"), ("灰",)),
    ("orange", ("orange",), ("橙",)),
    ("aqua", ("aqua", "cyan", "teal"), ("青", "水蓝")),
)

_HAIR_ACCESSORY_AFTER_RE = (
    r"(?:ornament|ribbon|clip|pin|band|accessory|flower|bow|scrunchie)"
)

def _extract_character_color_appearance(text: str) -> set[str]:
    """提取少量可确定的发色/瞳色语义，供角色 LoRA 身份边界校验。

    这里只识别颜色与 hair/eyes 的明确绑定；不会把 Profile 的 black/white、
    黑色服装或环境配色误当成角色发色。
    """
    if not isinstance(text, str) or not text.strip():
        return set()
    found: set[str] = set()
    clauses = [part.strip() for part in re.split(r"[,，、;；。.!?！？\n]+", text)
               if part.strip()]
    for clause in clauses:
        normalized = _lora_tag_key(clause)
        for canonical, english_aliases, chinese_aliases in _CHARACTER_COLOR_SPECS:
            english = "|".join(re.escape(alias) for alias in english_aliases)
            hair_modifier = (
                r"(?:very|absurdly|extremely|long|short|medium|wavy|curly|straight|"
                r"messy|flowing|silky|gradient|two tone|multicolored)"
            )
            eye_modifier = r"(?:large|small|bright|glowing|half closed|narrow|sharp|soft)"
            hair_patterns = (
                rf"\b(?:{english})\b(?:\s+{hair_modifier}){{0,3}}\s+hair\b(?!\s+{_HAIR_ACCESSORY_AFTER_RE})",
                rf"\bhair\b\s+(?:dyed|colored|is|turned)\s+(?:{english})\b",
                rf"\b(?:{english})[- ]haired\b",
            )
            eye_patterns = (
                rf"\b(?:{english})\b(?:\s+{eye_modifier}){{0,2}}\s+eyes?\b(?!\s+(?:shadow|makeup))",
                rf"\beyes?\b\s+(?:colored|are|turned)\s+(?:{english})\b",
            )
            if any(re.search(pattern, normalized) for pattern in hair_patterns):
                found.add(f"{canonical} hair")
            if any(re.search(pattern, normalized) for pattern in eye_patterns):
                found.add(("gold" if canonical == "blonde" else canonical) + " eyes")

            chinese = "|".join(re.escape(alias) for alias in chinese_aliases)
            hair_style = r"(?:长|短|中长|卷|直|波浪|蓬松|双马尾|单马尾|马尾|辫子|渐变|挑染)*"
            hair_target = r"(?:头发|发色|发(?!饰|夹|带|花|簪|箍|绳|冠)|双马尾|单马尾|马尾|辫子)"
            eye_style = r"(?:大|小|明亮|发光|半闭|细长|锐利|柔和)*"
            eye_target = r"(?:眼睛|眼眸|瞳孔|瞳色|眼(?!影|妆|线|罩|镜)|瞳)"
            if (re.search(rf"(?:{chinese})(?:色)?(?:的)?{hair_style}{hair_target}", clause)
                    or re.search(rf"{hair_target}(?:是|为|改成|变成|染成|呈现为)(?:{chinese})(?:色)?", clause)):
                found.add(f"{canonical} hair")
            if (re.search(rf"(?:{chinese})(?:色)?(?:的)?{eye_style}{eye_target}", clause)
                    or re.search(rf"{eye_target}(?:是|为|改成|变成|呈现为)(?:{chinese})(?:色)?", clause)):
                found.add(("gold" if canonical == "blonde" else canonical) + " eyes")
    return found

def _explicit_character_appearance_locks(text: str) -> set[str]:
    """从用户权威文本提取角色发色/瞳色锁定，不改变普通文本的词典路由。"""
    locks = _extract_character_color_appearance(text)
    dict_hits, _ = match_dict_words(text)
    for tag in dict_hits:
        locks.update(_extract_character_color_appearance(tag))
    return locks

def _character_appearance_locks_from_context(context: str) -> set[str] | None:
    """读取 translate 写入的内部 JSON 标记；None 表示没有角色 LoRA 护栏。"""
    for line in context.splitlines():
        if not line.startswith(_CHARACTER_APPEARANCE_LOCKS_PREFIX):
            continue
        try:
            raw = json.loads(line[len(_CHARACTER_APPEARANCE_LOCKS_PREFIX):].strip())
        except json.JSONDecodeError:
            return set()
        if not isinstance(raw, list):
            return set()
        return {str(item).strip().lower() for item in raw if str(item).strip()}
    return None

def _composer_character_lora_appearance_issue(prompt_ir: dict, prompt_line: str,
                                               allowed: set[str]) -> str | None:
    """角色 LoRA 已负责身份时，拒绝模型自行新增发色/瞳色。"""
    observed: set[str] = set()
    for item in prompt_ir.get("appearance", []):
        observed.update(_extract_character_color_appearance(str(item)))
    observed.update(_extract_character_color_appearance(prompt_line))
    unauthorized = sorted(observed - allowed)
    if not unauthorized:
        return None
    return ("Composer LoRA 身份冲突：角色 LoRA 启用时自行补充了用户未锁定的外观属性 "
            + ", ".join(unauthorized)
            + "；请从 IR.appearance 与 PROMPT 删除这些属性，让角色 LoRA 提供身份外观")

def _composer_multi_prompt_shape_issue(prompt_ir: dict, prompt_line: str,
                                       context: str) -> str | None:
    """Reject only reproduced multi-character wording that encourages split/crop failures."""
    source = context.lower()
    subjects = " ".join(str(item).lower() for item in prompt_ir.get("subject", []))
    explicit_multi = bool(
        re.search(r"\b[2-9]\s*(?:girls?|boys?|others?|characters?|people|persons?)\b", source)
        or re.search(r"\b[2-9](?:girls|boys|others)\b", subjects)
        or re.search(r"双人(?!床|房)|两人|二人", context)
    )
    if not explicit_multi:
        return None

    low = prompt_line.lower()
    split_meta_patterns = (
        r"\bclear separation\b",
        r"\bdistinct silhouettes?\b",
        r"\bcolor separation\b",
        r"\bclear depth\b",
        r"\bforeground[- ]background layering\b",
        r"\bclear front[- ]back layering\b",
        r"\bkeep (?:the )?(?:figures|characters) separated\b",
        r"\bkeep (?:both |the )?(?:figures|characters|faces) readable\b",
    )
    matched = next(
        (pattern for pattern in split_meta_patterns if re.search(pattern, low)),
        None,
    )
    if matched:
        return (
            "多人 PROMPT 使用了抽象分割/防失败措辞；删除 clear separation, distinct silhouettes, "
            "color separation, clear depth, foreground-background layering 等词，只用具名角色短句"
            "表达同一画面中的位置、接触和道具归属"
        )

    semantic_text = " ".join(
        [low]
        + [
            str(item).lower()
            for field in ("action", "pose", "interaction", "composition", "constraints")
            for item in prompt_ir.get(field, [])
        ]
    )
    lower_body_visible = any(
        term in semantic_text
        for term in (
            "spread legs", "legs spread", "thigh", "pelvis", "hips", "pussy",
            "genitals", "visible feet", "full body", "entire figure",
        )
    )
    if lower_body_visible and re.search(r"\bclose[- ]?up\b|\bupper body\b", low):
        return (
            "多人 PROMPT 要求下半身/全身可见却使用 close-up 或 upper body；改为 cowboy shot "
            "或 three-quarter view，并保留用户要求的身体接触和可见细节"
        )

    dense_contact = any(
        term in semantic_text
        for term in (
            "body contact", "after sex", "after vaginal", "front-to-back",
            "behind another", "cuddling", "embrace", "pressed close", "sex",
        )
    )
    _tag_backbone, separator, prose_tail = prompt_line.partition(". ")
    if dense_contact and separator:
        prose_words = re.findall(r"[a-z0-9']+", prose_tail.lower())
        if len(prose_words) > 12 or ";" in prose_tail or prose_tail.count(".") > 1:
            return (
                "多人贴身互动被写成了逐部位关系段；把可用的动作、接触、前后关系和可见细节"
                "改成 Anima 熟悉的 tags。只有身份归属确实无法由 tags 表达时，才允许在末尾保留"
                "不超过 12 个英文词的一条位置绑定，禁止分号和第二句"
            )
    return None

_TRANSLATE_CACHE: dict[str, tuple] = {}

_TRANSLATE_CACHE_MAX = 500

_MULTI_RELATION_NAMES_PREFIX = "REQUIRED ENGLISH SUBJECT NAMES FOR RELATION SENTENCES:"

MULTI_CHARACTER_SYSTEM_PROMPT = """You compile a Chinese multi-character image idea into one English positive prompt for Anima, an anime model trained on Danbooru/Gelbooru tags and natural-language captions.

Preserve every explicit subject, identity, clothing state, visible body detail, action, interaction, prop, camera intention, mood, and content detail. Do not censor, euphemize, cover, crop away, or invent a conflicting fact.

COMPLETION LEVEL controls only unspecified details:
- FAITHFUL: add no new premise; only make the requested picture executable.
- AUTO: preserve all locks and fill a few important missing visual decisions.
- FREE: preserve all locks and freely design the remaining picture.
If CONCEPT OVERRIDE is present, follow it as authoritative and copy it unchanged after CONCEPT:.

Unless the user explicitly asks for a lineup, character sheet, reference display, or formal symmetrical presentation, compose the characters around one shared visual moment instead of two independent poses. Use one existing scene event, shared prop, gaze exchange, body-direction response, or environmental force as the focal link. Let compatible expression, hair or fabric motion, light direction, and depth support that same moment. Do not default to both characters standing straight, equally spaced left and right, and facing the viewer.

Under FAITHFUL, do not invent a new premise merely to add motion; only reveal a compatible body angle, gaze, overlap, or environmental response already supported by the user's scene. Under AUTO or FREE, when the request is sparse, complete one concrete shared focal event before choosing tags. Keep one primary interaction and one primary composition idea rather than assigning an independent checklist of limb actions to each subject.

For two selected female Profiles, use `2girls` in IR.subject and as the first PROMPT item. Never use `1girl`, `solo`, or `solo focus`.

Write a tag-first prompt. ACTIVE LORA profile identities, appearances, and outfits are injected by the backend directly after the count, so do not copy them into the shared tag backbone. Start with the exact count, then add familiar action, pose, interaction, object, framing, scene, lighting, mood, and style tags. Prefer canonical visible tags over caption-like substitutes.

Natural language is optional, not mandatory. If familiar tags already express a close interaction, body contact, pose, and visible details, output one comma-separated tag list and stop. This tag-dominant form is preferred for dense intimate or overlapping scenes; use tags such as `yuri`, `after sex`, `after vaginal`, `body contact`, `front-to-back`, `behind another`, `breast grab`, `sitting`, `spread legs`, `stuffed toy`, and other applicable familiar concepts instead of narrating anatomy in prose.

Append one compact named relation sentence only when the user locks an asymmetric position, an unusual spatial arrangement, or prop/action ownership that tags cannot bind reliably. Use names from REQUIRED ENGLISH SUBJECT NAMES FOR RELATION SENTENCES. Bind only the unresolved identity relationship; do not turn the sentence into a complete pose specification for both bodies. For dense body contact, any named tail must contain at most 12 English words and bind only the unresolved identity positions, for example `Denia in front, Sigrika close behind.` Never restate body parts or visible details in that tail. For other scenes use at most one sentence. Never output `Character A`, `Character B`, or other placeholders. A named sentence may repeat one distinguishing supplied outfit or accessory when needed for ownership, but never the complete backend identity cluster.

Do not write generic anti-failure instructions such as clear separation, distinct silhouettes, color separation, clear depth, foreground-background layering, keep separated, or keep readable. Do not write `same overlapping two-shot`, multi-sentence anatomy narration, or `foreground/background` prose when `front-to-back` and `behind another` tags express the relation. Put `no split screen` and `no third person` only in IR.constraints, never PROMPT. If pelvis, thighs, full body, or spread legs must remain visible, use cowboy shot, three-quarter view, or full body instead of close-up.

Dense-contact shape example; replace placeholders with the required English names:
`2girls, after sex, after vaginal, nude, sitting, spread legs, pussy, cum in pussy, cumdrip, yuri, body contact, breast grab, front-to-back, behind another, stuffed toy, cowboy shot. <NAME_1> in front, <NAME_2> close behind.`

ACTIVE LORA CONTEXT supplies exact backend triggers and identity clusters. Do not output filenames, weights, trigger syntax, or a second unowned appearance list. Do not infer appearance from profile IDs or names. Echo the exact locked Profiles in the mandatory LORA JSON line.

CHAR is also mandatory. Use `CHAR: none` when every named character is supplied by ACTIVE LORA CONTEXT. If USER IDEA explicitly names an additional fictional character not supplied by ACTIVE LORA, use `CHAR: <exact USER IDEA name span> => <lowercase canonical Danbooru tag candidate>`; never use a whole sentence or generic phrase.

CONCEPT is concise Chinese. 用户锁定 contains only user facts and selected LoRA concepts. 模型补全 lists only material additions, or 无.
IR is one compact JSON object with exactly these array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints.
Do not output quality/score tags, rating labels, negative tags, generation settings, XML, filenames, weights, or explanations.

Output exactly five non-empty lines:
CONCEPT: 用户锁定：<explicit facts>｜模型补全：<major additions or 无>
IR: <one-line JSON with all 12 fields>
CHAR: none
LORA: <JSON using only supplied key/profile/optional IDs>
PROMPT: <English positive prompt only>"""

PAINTER_SYSTEM_PROMPT = """You are an expert prompt composer for Anima, an anime image model trained to understand Danbooru-style concepts, ordinary English image phrases, natural-language captions, and mixtures of them.

Turn the user's complete Chinese image idea into one production-ready English positive prompt for Anima. This is not a literal translation task. When the input is sparse, complete it into one coherent anime illustration with a clear central appeal. When the input is already detailed, preserve it and add only what is needed to make the requested picture drawable.

The user message declares one COMPLETION LEVEL:
- AUTO: judge semantic coverage rather than sentence or character count. Preserve every supplied visual decision, then creatively fill only the important decisions that remain open.
- FAITHFUL: stay close to the supplied intent. Add only small drawable details needed for a coherent image; do not invent a new theme, location, major prop, outfit concept, or narrative event.
- FREE: preserve explicit locks, then freely design one complete anime illustration premise for all unspecified decisions.

The Chinese CONCEPT line is a pre-generation control surface, not praise or analysis. It must distinguish what came from the user from what you invented:
- 用户锁定: only concrete visual requirements explicitly present in USER IDEA. An explicitly selected ACTIVE LORA CONTEXT is also a user lock; mention every supplied identity, outfit, or style capability concisely, without exposing trigger strings, filenames, weights, or internal IDs.
- Requests such as 画得好看, 更漂亮, 高质量, 有氛围, 有情景, or 有张力 are aesthetic goals, not concrete visual locks. Realize them through visible decisions under 模型补全.
- 模型补全: a concise natural Chinese summary of every major invented decision that materially changes the picture. Include the chosen theme, outfit/prop/action, setting/composition, and light/palette when you supplied them. Say 无 when nothing material was added.
- If CONCEPT OVERRIDE is present, it is the user's edited authoritative blueprint. Follow it when compiling PROMPT and copy it unchanged after CONCEPT:.

SPARSE-INPUT RULE: A subject plus a request such as "make it beautiful" requires an actual illustration premise, not a neutral stock portrait or a bundle of loud genre clichés. Silently consider several genuinely different premises, discard the interchangeable ones, and output only the most visually coherent choice.

When the user provides no theme, use this anime-illustration appeal prior:
- choose a designed, motif-driven outfit rather than a school uniform, plain blouse, or generic everyday clothing;
- give the hands and body a graceful interaction with one non-weapon prop, garment element, creature, plant, magical object, or environmental feature;
- use asymmetry, foreground overlap, foreshortening, flowing shapes, or another purposeful composition hook;
- build a controlled palette with one accent color and a clear light source;
- echo at least one shape, color, or material between the character design and surrounding motif.

Do not default to bedrooms, neon alleys, cyberpunk scenery, school uniforms, combat poses, swords, katanas, or other weapons when the user did not ask for them. Bokeh, glow, petals, particles, and dramatic lighting may support an established motif but cannot be the motif by themselves.

Compose the picture around one dominant visual motif. Make character design, expression, gaze, pose, hand interaction, framing, setting structure, lighting direction, palette, depth, and effects support that motif instead of becoming an inventory.

ANATOMY-READABILITY DEFAULT: When the user has not locked a difficult pose or camera angle, use at most one major anatomy challenge. Give each visible hand one simple readable purpose and clear contact with its prop or garment. Keep leg silhouettes and joints distinguishable in full-body views. Create motion through hair, sleeves, ribbons, fabric, plants, weather, or light before forcing the body into an extreme pose. Do not output generic claims such as perfect anatomy or perfect hands.

RENDERABILITY PASS: Before finalizing, treat the frame as a finite budget rather than a wish list.
- For decisions you add under 模型补全, choose one primary body pose and at most one primary hand interaction. The other hand should support the pose, rest naturally, or remain out of frame. Do not invent simultaneous top-adjusting, hem-lifting, prop-holding, and an independent leg pose.
- Choose either a close/upper-body character crop or a wider environment-and-body composition. A close-up or upper-body shot cannot also promise crossed legs, visible feet, a full chaise silhouette, and clearly visible pool water. If the environment is a major part of the premise, use a medium or three-quarter-body view and name the environment anchor that remains visible.
- If the main interaction touches a skirt hem, hips, or thighs, use a cowboy shot or three-quarter-body view and keep the interacting hands, elbows, and garment area inside the frame. Do not pair that interaction with close-up or upper-body focus. If you choose an upper-body crop, move the interaction into that crop or remove it.
- Every major pose, hand action, camera decision, prop, and visible scene anchor in PROMPT must already appear in CONCEPT and IR. Do not quietly add a new body action only in PROMPT.
- Remove model-added details that the chosen framing cannot visibly show. A smaller executable plan is better than a richer contradictory one.

Write in Anima-native prompt language. Use familiar anime/Danbooru concepts for common attributes, clothing, poses, objects, framing, lighting, and effects. Use short English clauses or complete sentences when they express relationships, continuous actions, unusual composition, or designed interaction more clearly than isolated tags. The final PROMPT may be tag-only, clause-heavy, or freely mixed. Do not create TAGS or NL sections and do not force prose into lowercase fragments.

Use useful Anima count tags. For one unnamed female character, normally begin with 1girl, solo; use the count and subject actually requested for other cases. Meaningful reinforcement is allowed when a clause binds a few important tags into a relationship or composition. Never mechanically paraphrase or repeat the whole prompt.

For an explicitly multi-subject request, put the matching count in both IR.subject and PROMPT. Two selected or named female character profiles shown together require 2girls, never 1girl, solo, or solo focus. Treat different forms of the same identity as separate visible subjects when the user selected and placed both forms.

MULTI-CHARACTER PROMPT SHAPE: the backend injects each selected LoRA Profile as an adjacent identity-tag cluster after the count. Unless the user explicitly requests a lineup, character sheet, reference display, or formal symmetry, organize the characters around one shared visual moment rather than two independent poses. Do not default to both subjects standing straight, equally spaced left and right, and facing the viewer. Under AUTO/FREE, fill one concrete shared focal event; under FAITHFUL, preserve the premise and use only compatible gaze, body direction, overlap, motion, light, or environmental response. Keep one primary interaction and one primary composition idea. Build a tag-first mixed prompt: begin with the exact count and familiar shared action, interaction, object, framing, and scene tags. Natural language is optional. Use at most one compact named sentence only for identity relationship, ownership, or unusual spatial arrangement that tags cannot bind, and do not specify every limb action. For dense touching or overlapping scenes, prefer familiar relation tags and allow only a position-binding tail of at most 12 English words. Put negative constraints such as no split screen or no third person in IR.constraints only, not PROMPT. Do not write abstract anti-failure phrases such as clear separation, distinct silhouettes, color separation, clear depth, foreground-background layering, keep separated, or keep readable. If the pelvis, thighs, or spread legs must remain visible, use cowboy shot or three-quarter view rather than close-up.

Preserve every explicit user fact and constraint. Do not change a named character, subject count, requested clothing state, action, location, camera instruction, or core mood. Do not invent another main character, named IP, incompatible outfit, weapon, sex act, or unrelated spectacle. BACKEND-KNOWN is a mechanical definition: a named fictional character is backend-known only when its canonical tag appears in KNOWN CANONICAL TAGS or its identity is supplied by ACTIVE LORA CONTEXT. Your own recognition or world knowledge does not make it backend-known. If KNOWN CANONICAL TAGS is absent, no dictionary character was recognized. Emit a CHAR candidate for every explicit backend-unknown character name as specified below. Copy the name span exactly from USER IDEA; never use a whole sentence, pronoun, generic phrase, style name, or inferred identity as the name. Do not add age labels, safety wording, policy language, or content classifications.

Treat every user-stated clothing state, exposure level, visible body detail, act, and framing requirement as a hard visual lock. Carry it into CONCEPT, IR, and PROMPT in direct, drawable Anima language; do not euphemize, conceal, crop out, or replace it.
Details the user left unspecified remain completion slots. Do not infer either nudity or coverage from a sexual act alone, and ensure every invented clothing or framing decision remains compatible with the locked visible content.
An explicit user clothing or exposure override wins over an active LoRA outfit only for the affected area; otherwise preserve the LoRA-provided concept.

For erotic input, use the same illustration principles. Sensuality may come from clothing design and exposure, pose, gaze, expression, body line, framing, fabric/skin contrast, lighting, or interaction. Do not automatically turn erotic intent into nudity, and do not suppress nudity or explicit content when the user actually requests it.

The backend supplies the standard quality prefix, rating control, negative prompt, exact known character tags, and all LoRA filenames, weights, and required triggers. Do not output or guess any of those. Plan around KNOWN CANONICAL TAGS without repeating them in PROMPT. Treat every ACTIVE LORA `Already provides` capability as present and never conflict with its character identity, outfit, or style. For one active character, do not redundantly re-author its supplied identity appearance. For multiple visible Profiles, rely on the backend-injected adjacent identity clusters and use the compact named relationship sentences above to preserve ownership; do not flatten new unowned appearance tags into a shared list. Only use hair/eye color overrides listed under USER-LOCKED CHARACTER APPEARANCE OVERRIDES. An empty list means do not invent hair or eye colors in IR or PROMPT; verified Registry appearance remains in the backend-injected cluster. Never infer appearance from a profile ID/name, an outfit color, or a form label such as black/white/swim. When a style LoRA is active, do not add a vague replacement style phrase.

IR is a compact semantic inventory for backend inspection. Output a valid one-line JSON object with exactly these 12 array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints.

FINAL CHECK: remove quality/score tokens, rating labels, negative tags, generation metadata, XML wrappers, filenames, weights, and explanations. Remove contradictory framing. In particular, full body or entire figure visible cannot coexist with mid-shot, medium shot, upper body, close-up, cropped, or out of frame. There is no target tag count, sentence count, word count, or character count: use as much concrete visible information as the picture benefits from, then stop.

Always output exactly four non-empty lines, with no markdown or other text:
CONCEPT: 用户锁定：<explicit Chinese locks>｜模型补全：<major Chinese additions or 无>
IR: <one-line compact JSON with all 12 required array fields>
CHAR: none
PROMPT: <one English positive prompt ready for Anima>

The CHAR line is mandatory. Replace none with <exact USER IDEA name span> => <lowercase canonical Danbooru tag candidate> whenever USER IDEA contains one or more BACKEND-UNKNOWN named fictional characters, even when you personally recognize them. Use semicolons between multiple name => candidate pairs. CHAR is the only authority for backend unknown-character lookup; an IR.subject guess alone never triggers lookup.
Protocol examples: USER IDEA `雪之下雪乃坐在教室里` with no KNOWN CANONICAL TAGS requires `CHAR: 雪之下雪乃 => yukinoshita_yukino`; USER IDEA `黑发少女坐在教室里` requires `CHAR: none`.

If ACTIVE LORA CONTEXT is present, insert exactly one LORA JSON line after the mandatory CHAR line and before PROMPT. Use only supplied key/profile/optional IDs, echo locked explicit profiles unchanged, and never put trigger strings, filenames, or weights in LORA or PROMPT."""

_STRUCTURED_FIELDS = ("scene", "composition", "mood", "lighting", "style")

_IR_FIELDS = (
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
)

def _prompt_ir_meta(mode: str, reroll: bool = False, prompt_ir: dict | None = None,
                    char_tags: list[str] | None = None,
                    attribute_tags: list[str] | None = None,
                    character_lookup: list[dict] | None = None,
                    completion_level: str = DEFAULT_COMPLETION_LEVEL,
                    concept: str | None = None,
                    concept_override_applied: bool = False,
                    repetition_collapsed: bool = False) -> dict:
    """为 API 增加来源/补全元数据，不污染 12 字段 Prompt IR 结构。"""
    expansion = mode in {"painter_expansion", "visual_composer"}
    return {
        "mode": mode,
        "source": {
            "user_intent": "remaining_input",
            "character_tags": "dictionary" if char_tags else None,
            "attribute_tags": "dictionary" if attribute_tags else None,
            "default_completion": "visual_composer" if mode == "visual_composer" else (
                "painter" if expansion else None),
        },
        "expansion_applied": expansion,
        "completion_level": completion_level,
        "concept": concept,
        "concept_override_applied": bool(concept_override_applied),
        "repetition_collapsed": bool(repetition_collapsed),
        "reroll": bool(reroll),
        "reroll_strategy": ("new_visual_concept" if mode == "visual_composer" and reroll else
                            "new_painter_plan" if expansion and reroll else None),
        "prompt_ir_available": prompt_ir is not None,
        "character_lookup": character_lookup or [],
    }

VISION_SYSTEM_PROMPT = (
    "You are a professional image tagger using the Danbooru tag taxonomy. "
    "You receive a REFERENCE IMAGE and a user instruction (text). "
    "The user's instruction tells you what to preserve from the image and what to change.\n\n"
    "Extraction strategy -- follow the user's instruction:\n"
    "- If the user says 'same vibe/atmosphere' (同氛围) -> extract ONLY mood, lighting, color, scene setting.\n"
    "- If the user says 'keep pose/composition' (保持姿势/构图) -> extract composition, framing, pose, camera angle.\n"
    "- If the user says 'copy everything' (照着画/完全保持) -> extract subject + vibe + composition (full description).\n"
    "- If the user says 'change X but keep Y' -> extract Y from image, apply X from text.\n"
    "- If the instruction is unclear or empty -> extract everything (full description).\n"
    "The user's text may specify a NEW subject (character, count, attributes) that should REPLACE the image's subject where applicable.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <place/setting tags>\n"
    "composition: <framing / camera angle / pose tags>\n"
    "mood: <emotion -> atmosphere tags>\n"
    "lighting: <light tags>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. Follow the user's instruction to decide what to extract from the image vs. what to take from the text.\n"
    "2. Do NOT repeat tags already listed in Known character tags.\n"
    "3. Put a count tag (1girl/1boy/solo) FIRST in TAGS if a person is implied.\n"
    "4. Do NOT output quality/score/rating tags (masterpiece, best quality, score_*, safe, sensitive, questionable, explicit, absurdres). Rating tags are controlled manually by the user.\n"
    "5. Use lowercase danbooru tags; spaces preferred over underscores. "
    "Do NOT add realistic/photoreal/3d/render tags (the target model is anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
    "7. If ACTIVE LORA CONTEXT is present, add a LORA JSON line immediately before TAGS using only supplied key/profile/optional IDs. "
    "Do not output trigger strings, filenames, weights, or visual details that conflict with the active LoRA.\n"
)

VISION_ITERATE_SYSTEM_PROMPT = (
    "You are a prompt engineer for the Anima anime image model. You receive a GENERATED IMAGE the user likes and wants to "
    "re-draw as a VARIATION (same subject + same vibe), plus optional adjustment text. Describe the image fully as danbooru "
    "tags (subject + scene + mood + lighting + composition + style) so it can be re-drawn, KEEPING the same subject and vibe. "
    "Apply any adjustment from the text on top.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <concrete place + setting tags from the image>\n"
    "composition: <framing / camera angle / pose tags from the image>\n"
    "mood: <emotion -> atmosphere tags from the image>\n"
    "lighting: <light tags from the image>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. Keep the image's SUBJECT (count, hair, clothing, accessories) and VIBE (mood/lighting/color/scene) - this is a "
    "variation of the same image, not a new concept.\n"
    "2. If the text gives an adjustment (e.g. 白天, 更亮, 换姿势), apply it on top of the image's base.\n"
    "3. Put a count tag (1girl/1boy/solo) FIRST in TAGS.\n"
    "4. Do NOT output quality/score/rating tags (masterpiece, best quality, score_*, safe, sensitive, questionable, explicit, absurdres). Rating tags are controlled manually by the user.\n"
    "5. Use lowercase danbooru tags; spaces over underscores. Do NOT add realistic/photoreal/3d/render tags (anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
    "7. If ACTIVE LORA CONTEXT is present, add a LORA JSON line immediately before TAGS using only supplied key/profile/optional IDs. "
    "Keep the active binding locked and do not output trigger strings, filenames, or weights.\n"
)

def _validate_prompt_ir(value) -> dict | None:
    """校验并清洗 LLM 的 12 字段 Prompt IR, 不让坏 JSON 影响最终 tag 输出."""
    if not isinstance(value, dict):
        return None
    ir = {}
    for field in _IR_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list):
            return None
        ir[field] = [str(item).strip() for item in items if str(item).strip()]
    return ir

def _parse_prompt_ir(payload: str) -> dict | None:
    """解析 IR 行中的紧凑 JSON; 允许模型意外包一层 markdown fence."""
    payload = payload.strip().strip("`").strip()
    candidates = [payload]
    start, end = payload.find("{"), payload.rfind("}")
    if start >= 0 and end > start:
        candidates.append(payload[start:end + 1])
    for candidate in candidates:
        try:
            return _validate_prompt_ir(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
    return None

def _breakdown_from_ir(prompt_ir: dict) -> dict:
    """把 IR 中面向人的 5 个维度映射回既有 breakdown API 形状."""
    return {field: ", ".join(prompt_ir.get(field, [])) for field in _STRUCTURED_FIELDS}

def _parse_character_hints(out: str) -> list[dict]:
    """解析画师协议的 CHAR 行: 用户名 => LLM 提议的 Danbooru 候选 tag."""
    hints = []
    for line in out.splitlines():
        line = line.strip()
        if not line.lower().startswith("char:"):
            continue
        payload = line.split(":", 1)[1].strip()
        if not payload or payload.lower() in {"none", "null", "empty", "无"}:
            continue
        for item in payload.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=>" in item:
                name, candidate = (part.strip() for part in item.split("=>", 1))
            else:
                name, candidate = item, ""
            if (name and candidate and
                    name.lower() not in {"none", "null", "无"}):
                hints.append({"name": name, "candidate_tag": candidate})
    return hints

def _character_hint_issue(hints: list[dict], user_idea: str) -> str | None:
    """CHAR 只能引用用户原文中的显式名字，候选必须是 canonical tag 形状。"""
    non_name_markers = (
        "这个人物", "这个角色", "画一个", "生成一个", "提示词", "图片",
        "坐在", "站在", "躺在", "穿着", "看起来", "一脸", "露出", "暴露",
        "展示", "尽情发挥", "完整保留", "仅作", "我想看", "要有",
    )
    seen_names = set()
    for hint in hints:
        name = str(hint.get("name") or "").strip()
        candidate = str(hint.get("candidate_tag") or "").strip()
        if not name or not candidate:
            return "Composer CHAR 缺少角色名或 canonical tag 候选"
        if name not in user_idea:
            return "Composer CHAR 角色名必须原样出现在 USER IDEA 中"
        if len(name) > 32 or any(marker in name for marker in non_name_markers):
            return "Composer CHAR 角色名包含句子/指令片段，不能用于自动缓存"
        if name in seen_names:
            return "Composer CHAR 含重复角色名"
        if not re.fullmatch(r"[a-z0-9_():+'\-]+", candidate):
            return "Composer CHAR 候选不是小写 Danbooru canonical tag"
        seen_names.add(name)
    return None

def _user_idea_from_composer_context(context: str) -> str:
    """从生产 Composer context 取回原始 USER IDEA，只用于校验 CHAR 名字边界。"""
    marker = "USER IDEA:\n"
    if marker not in context:
        return ""
    idea = context.split(marker, 1)[1]
    for section in (
        "\nKNOWN CANONICAL TAGS:",
        "\nCONCEPT OVERRIDE ",
        "\nACTIVE LORA CONTEXT",
        "\nRegistry revision:",
    ):
        idea = idea.split(section, 1)[0]
    return idea.rstrip()

def _parse_lora_choices(out: str) -> dict[str, dict]:
    """解析可选 LORA JSON 行；只保留 ID 形状，合法性由 resolver 对 registry 校验。"""
    for line in out.splitlines():
        line = line.strip()
        if not line.lower().startswith("lora:"):
            continue
        payload = line.split(":", 1)[1].strip().strip("`")
        try:
            return _coerce_llm_lora_choices(json.loads(payload))
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}

def _parse_structured_output(out: str) -> tuple[str, dict | None, str, dict | None]:
    """解析生产 IR + PROMPT 或旧 IR + TAGS + NL 协议.
    返回 (tags, breakdown, nl, prompt_ir); PROMPT 是单一最终画师 Prompt，编译时视作 tags body。"""
    breakdown: dict = {}
    tags = ""
    painter_prompt = ""
    nl = ""
    prompt_ir = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("ir:"):
            prompt_ir = _parse_prompt_ir(line.split(":", 1)[1])
            continue
        if low.startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
            continue
        if low.startswith("prompt:"):
            painter_prompt = line.split(":", 1)[1].strip()
            continue
        if low.startswith("nl:"):
            nl = line.split(":", 1)[1].strip()
            continue
        for f in _STRUCTURED_FIELDS:
            if low.startswith(f + ":"):
                breakdown[f] = line.split(":", 1)[1].strip()
                break
    # 兼容模型把单行 JSON 错误地格式化成多行的情况; 失败仍走旧协议或 None.
    if prompt_ir is None:
        match = re.search(
            r"^\s*ir:\s*(\{.*?\})(?=\s*(?:\n\s*(?:tags|prompt|nl):|\Z))",
            out,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if match:
            prompt_ir = _parse_prompt_ir(match.group(1))
    if painter_prompt:
        tags = painter_prompt
        nl = ""
    if not tags:
        return out, None, "", None
    if prompt_ir is not None:
        breakdown = _breakdown_from_ir(prompt_ir)
    return tags, breakdown or None, nl, prompt_ir

def _canonicalize_concept(value: str | None) -> str | None:
    """归一化可编辑中文构思；只有同时包含用户锁定/模型补全才算有效。"""
    if not isinstance(value, str):
        return None
    concept = value.strip()
    if concept.lower().startswith("concept:"):
        concept = concept.split(":", 1)[1].strip()
    concept = re.sub(r"\s+", " ", concept)
    match = re.fullmatch(
        r"用户锁定\s*[：:]\s*(.+?)\s*[｜|]\s*模型补全\s*[：:]\s*(.+)",
        concept,
    )
    if not match:
        return None
    locked, added = (part.strip() for part in match.groups())
    if not locked or not added:
        return None
    return f"用户锁定：{locked}｜模型补全：{added}"

_COMPOSER_CLOSE_CROP_TERMS = (
    "close-up", "close up", "upper body", "bust shot", "portrait crop",
)

_COMPOSER_EXTENDED_BODY_TERMS = (
    "full body", "full-body", "full length", "full-length", "entire figure",
    "head to toe", "from head to toe", "legs crossed", "crossed legs",
    "visible feet", "feet visible",
)

_COMPOSER_LOWER_FRAME_ACTION_TERMS = (
    "hem lifted", "lifting skirt", "lifted skirt", "raising skirt",
    "pulling up skirt", "holding up skirt", "tugging skirt hem",
    "gripping skirt hem", "hand on thigh", "hands on thighs",
)

_COMPOSER_ADDED_CLOSE_CROP_TERMS = (
    "近景", "近身裁切", "上半身", "半身特写", "胸像",
    *_COMPOSER_CLOSE_CROP_TERMS,
)

_COMPOSER_LOWER_MANUAL_TARGET_TERMS = (
    "裙", "裙摆", "下摆", "衣摆", "髋", "臀", "大腿",
)

_COMPOSER_MANUAL_TARGET_TERMS = (
    "手", "上衣", "衣服", "肩带", "裙", "下摆", "衣摆", "布料", "道具",
    "头发", "发梢", "发丝",
)

_COMPOSER_MANUAL_ACTION_TERMS = (
    "拿", "持", "握", "抓", "扶", "托", "按", "扯", "拉", "掀", "提", "调整",
    "捏", "抚", "摸", "拨", "梳",
)

def _composer_feasibility_issue(prompt_ir: dict, concept: str,
                                prompt_line: str) -> str | None:
    """拒绝少量可确定的画面容量冲突，让第二次模型调用重新规划。

    这里只检查跨 checkpoint 都明显不可同时呈现的组合；不尝试用代码决定审美、
    姿态细节或最佳镜头。
    """
    ir_text = " ".join(
        str(item).lower()
        for field in ("action", "pose", "composition", "scene")
        for item in prompt_ir.get(field, [])
    )
    render_text = f"{ir_text} {prompt_line.lower()}"
    close_crop = any(term in render_text for term in _COMPOSER_CLOSE_CROP_TERMS)
    extended_body = any(term in render_text for term in _COMPOSER_EXTENDED_BODY_TERMS)
    if close_crop and extended_body:
        return ("Composer 可画性冲突：近景/上半身构图同时要求完整下肢或全身信息；"
                "请改为中景/四分之三身，或删除画面外动作")

    added = concept.split("｜模型补全：", 1)[1] if "｜模型补全：" in concept else ""
    added_clauses = [part.strip() for part in re.split(r"[，,；;。]+", added)
                     if part.strip()]
    lower_manual_clauses = [
        clause for clause in added_clauses
        if any(target in clause for target in _COMPOSER_LOWER_MANUAL_TARGET_TERMS)
        and any(action in clause for action in _COMPOSER_MANUAL_ACTION_TERMS)
    ]
    lower_frame_action = any(
        term in render_text for term in _COMPOSER_LOWER_FRAME_ACTION_TERMS
    )
    added_close_crop = any(
        term in added.lower() for term in _COMPOSER_ADDED_CLOSE_CROP_TERMS
    )
    if (close_crop and (lower_frame_action or lower_manual_clauses)
            and (added_close_crop or lower_manual_clauses)):
        return ("Composer 可画性冲突：近景/上半身构图同时把裙摆、髋部或大腿交互设为重点；"
                "请改为牛仔镜头/四分之三身并让交互区域完整入镜，或删除画面外动作")

    manual_clauses = [
        clause for clause in added_clauses
        if any(target in clause for target in _COMPOSER_MANUAL_TARGET_TERMS)
        and any(action in clause for action in _COMPOSER_MANUAL_ACTION_TERMS)
    ]
    if len(manual_clauses) > 1:
        return ("Composer 可画性冲突：模型补全同时发明了多个手部/服装操作；"
                "只保留一个主要交互，让另一只手支撑姿态或自然放置")
    return None

def _normalize_optional_concept(value, field_name: str = "concept") -> str | None:
    """校验来自 API 的 concept/concept_override，返回统一可追踪形式。"""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise HTTPException(400, f"{field_name} 必须是字符串")
    if len(value) > MAX_CONCEPT_CHARS:
        raise HTTPException(400, f"{field_name} 过长(>{MAX_CONCEPT_CHARS})")
    concept = _canonicalize_concept(value)
    if concept is None:
        raise HTTPException(400, f"{field_name} 必须包含‘用户锁定：…｜模型补全：…’")
    return concept

def _parse_composer_output(out: str, active_lora: bool = False,
                           require_character_line: bool = False) -> tuple:
    """严格解析 Visual Composer 的 CONCEPT + IR + [CHAR] + [LORA] + PROMPT 协议。"""
    text = out.strip().strip("`").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if (len(lines) < 3 or not lines[0].lower().startswith("concept:") or
            not lines[1].lower().startswith("ir:")):
        raise RuntimeError("Composer 输出未遵守 CONCEPT + IR + [CHAR] + [LORA] + PROMPT 行协议")

    cursor = 2
    character_hints = []
    has_character_line = cursor < len(lines) and lines[cursor].lower().startswith("char:")
    if require_character_line and not has_character_line:
        raise RuntimeError("Composer 生产协议缺少必填 CHAR 行")
    if has_character_line:
        char_payload = lines[cursor].split(":", 1)[1].strip().lower()
        character_hints = _parse_character_hints(lines[cursor])
        if not character_hints and char_payload not in {"none", "null", "empty", "无"}:
            raise RuntimeError("Composer CHAR 行缺少有效的 name => canonical tag")
        cursor += 1
    if active_lora:
        if cursor >= len(lines) or not lines[cursor].lower().startswith("lora:"):
            raise RuntimeError("Composer 缺少有效 LORA 选择")
        cursor += 1
    if (cursor != len(lines) - 1 or
            not lines[cursor].lower().startswith("prompt:")):
        raise RuntimeError("Composer 输出未遵守 CONCEPT + IR + [CHAR] + [LORA] + PROMPT 行协议")

    concept = _canonicalize_concept(lines[0])
    try:
        raw_ir = json.loads(lines[1].split(":", 1)[1].strip())
    except (json.JSONDecodeError, TypeError):
        raw_ir = None
    tags, breakdown, nl, prompt_ir = _parse_structured_output(text)
    prompt_line = lines[-1].split(":", 1)[1].strip()
    if concept is None:
        raise RuntimeError("Composer CONCEPT 未区分用户锁定与模型补全")
    if len(concept) > MAX_CONCEPT_CHARS:
        raise RuntimeError(f"Composer CONCEPT 过长(>{MAX_CONCEPT_CHARS})")
    if (prompt_ir is None or not isinstance(raw_ir, dict) or
            set(raw_ir) != set(_IR_FIELDS)):
        raise RuntimeError("Composer IR 缺失、字段不全或不是有效 JSON")
    if not prompt_line or tags != prompt_line:
        raise RuntimeError("Composer PROMPT 缺失或无法解析")
    lora_choices = _parse_lora_choices(text)
    if active_lora and not lora_choices:
        raise RuntimeError("Composer 缺少有效 LORA 选择")
    prompt_line, repetition_collapsed = collapse_exact_prompt_repetition(prompt_line)
    if len(prompt_line) > MAX_PROMPT_EN_CHARS:
        raise RuntimeError(f"Composer PROMPT 过长(>{MAX_PROMPT_EN_CHARS})")
    feasibility_issue = _composer_feasibility_issue(prompt_ir, concept, prompt_line)
    if feasibility_issue:
        raise RuntimeError(feasibility_issue)
    return (prompt_line, breakdown, nl, prompt_ir, character_hints,
            lora_choices, concept, repetition_collapsed)

async def siliconflow_translate(context: str, reroll: bool = False,
                                completion_level: str | None = None) -> tuple:
    """走 Reasoning Model 生成 Visual Composer 协议。

    返回 (prompt, breakdown, nl, prompt_ir, character_hints, lora_choices,
    concept, repetition_collapsed)。失败会修复一次，再把协议错误交给上层。
    """
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")

    # thinking 默认关 (D2: 思考慢 30s+ 且易复读); 结构化字段已是强制表态机制, 不依赖 CoT.
    # 隐喻/场景仍弱时 config 翻 translate_enable_thinking: true 重测, 不动代码 (见 D18).
    thinking = bool(CFG.get("translate_enable_thinking", False))

    if completion_level is None:
        level_match = re.search(r"^COMPLETION LEVEL:\s*(auto|faithful|free)\s*$",
                                context, flags=re.IGNORECASE | re.MULTILINE)
        completion_level = level_match.group(1).lower() if level_match else DEFAULT_COMPLETION_LEVEL
    completion_level = _normalize_completion_level(completion_level)

    # 补全程度控制创意幅度，不再用输入长度推断。reroll 只改变同一程度下的方案。
    temperature = (float(CFG.get("reroll_temperature", 0.9)) if reroll else {
        "faithful": 0.35,
        "auto": 0.7,
        "free": 0.8,
    }[completion_level])
    nudge = ("Generate a different coherent illustration premise within the same COMPLETION LEVEL. "
             "Keep every explicit lock and vary only decisions that remain open. "
             "Still follow the exact output protocol.\n\n") if reroll else ""
    user_content = ("/no_think " if not thinking else "") + nudge + context
    active_lora = "ACTIVE LORA CONTEXT" in context
    multi_character_protocol = _MULTI_RELATION_NAMES_PREFIX in context
    character_appearance_locks = _character_appearance_locks_from_context(context)

    parsed = None
    last_protocol_error = None
    for attempt in range(2):
        repair = ""
        if attempt:
            previous_issue = str(last_protocol_error or "输出协议不合法")[:300]
            repair = (
                "\nREPAIR REQUEST: Your previous response was rejected for this reason: "
                + previous_issue
                + ". Re-plan model-added decisions when the issue is semantic; preserve every USER lock. "
                "Return CONCEPT first, then one compact valid IR JSON line with all 12 array fields, "
                "then the mandatory CHAR line: use none, or proper character names copied exactly from "
                "USER IDEA whenever the backend did not supply them through KNOWN CANONICAL TAGS or active "
                "LoRA identities; your own recognition does not count as backend knowledge, "
                + ("then the mandatory LORA JSON line using only supplied IDs, " if active_lora else "")
                + "then PROMPT. Use exactly one non-empty line for each required field and no other text.\n"
            )
        r = await CLIENT.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": (
                        MULTI_CHARACTER_SYSTEM_PROMPT
                        if multi_character_protocol else PAINTER_SYSTEM_PROMPT
                    )},
                    # /no_think: Qwen3 软开关, 强制不进思考模式 (思考会慢到 30s+ 且易复读). thinking 开则不前置.
                    {"role": "user", "content": user_content + repair},
                ],
                "temperature": temperature,
                "max_tokens": 1800,
                # ★ 关键: enable_thinking 必须放顶层, 放 extra_body 里硅基流动不认 -> 思考没关掉. (见 D2)
                "enable_thinking": thinking,
            },
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"翻译服务返回 {r.status_code}: {r.text[:200]}")
        data = r.json()
        out = data["choices"][0]["message"]["content"].strip()
        # 极端情况下模型可能仍带 <think>, 清一下
        if "</think>" in out:
            out = out.split("</think>", 1)[1].strip()
        if not out:
            raise RuntimeError("翻译服务返回空内容")

        try:
            parsed = _parse_composer_output(
                out, active_lora=active_lora, require_character_line=True
            )
            character_hint_issue = _character_hint_issue(
                parsed[4], _user_idea_from_composer_context(context)
            )
            if character_hint_issue:
                raise RuntimeError(character_hint_issue)
            if character_appearance_locks is not None:
                identity_issue = _composer_character_lora_appearance_issue(
                    parsed[3], parsed[0], character_appearance_locks
                )
                if identity_issue:
                    raise RuntimeError(identity_issue)
            multi_shape_issue = _composer_multi_prompt_shape_issue(
                parsed[3], parsed[0], context
            )
            if multi_shape_issue:
                raise RuntimeError(multi_shape_issue)
            break
        except RuntimeError as exc:
            last_protocol_error = exc
            parsed = None

    if parsed is None:
        raise RuntimeError(f"Composer 协议修复失败: {last_protocol_error}")
    return parsed

async def siliconflow_vision_translate(image_b64: str, context: str, reroll: bool = False, mode: str = "reference") -> tuple[str, dict | None, str, dict | None, dict]:
    """③ 参考图理解: 走硅基流动 Qwen3-VL, 从参考图提取氛围/配色/构图/场景/光影 -> 结构化 breakdown + TAGS.
    image_b64: data URI (data:image/...;base64,...) 或纯 base64. context 同文本 LLM (Known tags + Remaining).
    返回 (tags, breakdown), 复用 _parse_structured_output. 失败抛异常 (上层转 HTTPException). 见 D23.
    mode: "reference"=③ vibe-only (用户参考图); "iterate"=⑤ 保氛围再画一版 (锁住主体+氛围全量提取, D25).
    注意: Qwen3-VL-Instruct 不接受 enable_thinking 参数 (会 400), 故不带; 它是非 thinking 模型, 默认不思考."""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_vision_model", "Qwen/Qwen3-VL-8B-Instruct")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")
    if not image_b64.startswith("data:"):
        image_b64 = "data:image/jpeg;base64," + image_b64

    temperature = float(CFG.get("reroll_temperature", 0.9)) if reroll else 0.4
    nudge = ("Give a DIFFERENT, more creative read of the image's mood and scene. "
             "Still follow the output format and the known-tags rule.\n\n") if reroll else ""

    r = await CLIENT.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": VISION_ITERATE_SYSTEM_PROMPT if mode == "iterate" else VISION_SYSTEM_PROMPT},
                # VL 模型 user content 是数组: 文本 + image_url (OpenAI 视觉格式, 硅基流动兼容, 见 D23)
                {"role": "user", "content": [
                    {"type": "text", "text": nudge + context},
                    {"type": "image_url", "image_url": {"url": image_b64, "detail": "high"}},
                ]},
            ],
            "temperature": temperature,
            "max_tokens": 500,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"视觉服务返回 {r.status_code}: {r.text[:200]}")
    data = r.json()
    out = data["choices"][0]["message"]["content"].strip()
    if "</think>" in out:
        out = out.split("</think>", 1)[1].strip()
    if not out:
        raise RuntimeError("视觉服务返回空内容")

    tags, breakdown, nl, prompt_ir = _parse_structured_output(out)
    # 重复 tag 兜底 (同文本 LLM)
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"视觉输出异常(重复tag: {dupes[0]}), 请重试")
    return tags, breakdown, nl, prompt_ir, _parse_lora_choices(out)

_COUNT_TAG_RE = re.compile(
    r"^(solo|solo focus|"
    r"\d+(girl|boy|other)s?|"      # 1girl, 2girls, 1boy, 1other
    r"\d+\+(girls|boys|others)|"   # 6+girls
    r"multiple (girls|boys|others))$"
)

_EXPLICIT_COUNT_TAG_RE = re.compile(
    r"(?<![a-z0-9_])([2-9]\d*)\s*(girls?|boys?|others?)(?![a-z0-9_])",
    re.IGNORECASE,
)

_EXPLICIT_TWO_PERSON_RE = re.compile(r"双人(?!床|房)|两人|二人|2\s*人")

def collapse_exact_prompt_repetition(prompt: str) -> tuple[str, bool]:
    """只折叠整段完全相同的 comma-segment 序列，不删除有意义的局部强化。"""
    segments = [segment.strip() for segment in prompt.strip().rstrip(".").split(",")
                if segment.strip()]
    count = len(segments)
    for unit_size in range(1, count // 2 + 1):
        if count % unit_size:
            continue
        unit = segments[:unit_size]
        if all(segments[index:index + unit_size] == unit
               for index in range(0, count, unit_size)):
            return ", ".join(unit), True
    return prompt.strip(), False

_FULL_BODY_LOCK_TERMS = (
    "全身", "完整可见", "从头到脚", "full body", "entire figure visible",
)

_FULL_BODY_CONFLICT_TERMS = (
    "mid-shot", "mid shot", "medium shot", "upper body", "close-up", "close up",
    "cropped", "out of frame",
)

def _lock_explicit_multi_subject_count(tags: list[str], prompt_ir: dict | None,
                                       original_text: str) -> list[str]:
    """Preserve an explicit multi-subject count and remove contradictory solo tags.

    The Composer remains responsible for deciding gender/type. Code only locks an exact
    user count, or expands the Composer's own singular gender tag when Chinese says two
    people. This avoids guessing gender from a LoRA profile name.
    """
    source = original_text.lower()
    exact = _EXPLICIT_COUNT_TAG_RE.search(source)
    count = int(exact.group(1)) if exact else (2 if _EXPLICIT_TWO_PERSON_RE.search(source) else 0)
    if count < 2:
        return tags

    target = None
    if exact:
        noun = exact.group(2).lower()
        if noun.startswith("girl"):
            target = f"{count}girls"
        elif noun.startswith("boy"):
            target = f"{count}boys"
        else:
            target = f"{count}others"
    else:
        evidence = " ".join(
            [tag.lower() for tag in tags]
            + [str(item).lower() for item in (prompt_ir or {}).get("subject", [])]
        )
        has_girl = bool(re.search(r"\b(?:\d+\s*)?(?:girls?|women|woman|female)\b", evidence))
        has_boy = bool(re.search(r"\b(?:\d+\s*)?(?:boys?|men|man|male)\b", evidence))
        has_other = bool(re.search(r"\b(?:\d+\s*)?others?\b", evidence))
        if has_girl and not has_boy:
            target = f"{count}girls"
        elif has_boy and not has_girl:
            target = f"{count}boys"
        elif has_other and not has_girl and not has_boy:
            target = f"{count}others"

    cleaned = []
    for tag in tags:
        value = tag.strip()
        low = value.lower()
        if low in {"solo", "solo focus"}:
            continue
        if low.startswith("solo focus "):
            value = re.sub(r"^solo focus\b", "focus", value, flags=re.IGNORECASE)
            low = value.lower()
        if target and _COUNT_TAG_RE.match(low):
            continue
        cleaned.append(value)
    if target:
        cleaned.insert(0, target)
    if isinstance(prompt_ir, dict):
        ir_subjects = []
        for item in prompt_ir.get("subject", []):
            value = str(item).strip()
            low = value.lower()
            if low in {"solo", "solo focus"}:
                continue
            if target and _COUNT_TAG_RE.match(low):
                continue
            ir_subjects.append(value)
        if target:
            ir_subjects.insert(0, target)
        prompt_ir["subject"] = ir_subjects
    return cleaned

def _prepare_composer_tags(tags: list[str], prompt_ir: dict | None,
                           original_text: str, char_tags: list[str]) -> list[str]:
    """Visual Composer 的最小代码护栏：主体计数 + 显式全身构图一致性。

    不继承旧 Painter 的 nude、默认镜头、剪影或风格删改启发式；这些视觉决定
    由 Composer 根据用户构思负责。
    """
    result = [tag.strip() for tag in tags if tag and tag.strip()]
    source = original_text.lower()
    result = _lock_explicit_multi_subject_count(result, prompt_ir, original_text)
    full_body_locked = any(term in source for term in _FULL_BODY_LOCK_TERMS)
    if full_body_locked:
        result = [tag for tag in result
                  if not any(term in tag.lower() for term in _FULL_BODY_CONFLICT_TERMS)]
        if not any(term in tag.lower() for term in ("full body", "entire figure")
                   for tag in result):
            result.append("full body")

    if any(_COUNT_TAG_RE.match(tag.lower()) for tag in result):
        return result
    subject = " ".join(str(item).lower() for item in (prompt_ir or {}).get("subject", []))
    subject_words = set(re.findall(r"[a-z]+", subject))
    if subject_words & {"boy", "boys", "male", "man", "men"} or any(
            term in source for term in ("男孩", "男性", "男人")):
        result.insert(0, "1boy")
    elif char_tags or subject_words & {"girl", "girls", "woman", "women", "female", "person"} or any(
            term in source for term in ("女孩", "少女", "女性", "女人", "女生", "巫女")):
        result.insert(0, "1girl")
    return result

def normalize_tag_order(char_tags: list[str], other_tags: list[str]) -> str:
    """按 Anima 规范序拼接: count -> character -> general. 只重排不增删; 去重(保留首次出现, 见 D23)."""
    count, general = [], []
    for t in other_tags:
        (count if _COUNT_TAG_RE.match(t.strip()) else general).append(t)
    seen, out = set(), []
    for t in count + char_tags + general:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return ", ".join(out)

def _strip_char_bare_names(new_list: list[str], char_tags: list[str]) -> list[str]:
    """删除 LLM 输出里"已知精确角色 tag 的裸名变体".
    例: 已知 char_tag=ganyu_(genshin_impact), 则删 new_list 里的裸名 ganyu (会触发原神 logo, 见 D29).
    裸名 = 精确 tag 去掉 '_(series)' 后缀的前缀部分. 不依赖 LLM 听话, 代码层兜底."""
    bare = set()
    for ct in char_tags:
        for sep in ("_(", " ("):
            if sep in ct:
                name = ct.split(sep, 1)[0].strip().lower()
                bare.update({name, name.replace("_", " "), name.replace(" ", "_")})
                break
        else:
            # 无系列后缀的精确 tag（如 yukinoshita_yukino）：把空格/下划线变体视作同义去重
            tag = ct.strip().lower()
            bare.add(tag.replace("_", " "))
            bare.add(tag.replace(" ", "_"))
    if not bare:
        return new_list
    return [t for t in new_list if t.strip().lower() not in bare]

_RELATION_HINTS = (
    "each other", "facing each other", "duel", "dueling", "kiss", "kissing",
    "hug", "hugging", "embrace", "embracing", "left", "right", "beside",
    "guiding", "opposing", "spatial", "interaction",
)

def infer_render_profile(prompt_ir: dict | None) -> str:
    """根据已解析 IR 选择最小渲染策略，不改变 IR 或 LLM 输出协议.

    Phase 2 只把明确成人单主体的 tag-first 证据收进生产；普通 SFW
    内容继续保留 NL，避免把 P01 的 legacy 胜出泛化成全局删 NL。
    """
    if not prompt_ir:
        return "tag_first"
    subject = [str(item).lower() for item in prompt_ir.get("subject", [])]
    action = [str(item).lower() for item in prompt_ir.get("action", [])]
    pose = [str(item).lower() for item in prompt_ir.get("pose", [])]
    interaction = [str(item).lower() for item in prompt_ir.get("interaction", [])]
    all_terms = " ".join(
        str(item).lower()
        for field in ("subject", "appearance", "clothing", "action", "pose", "interaction", "constraints")
        for item in prompt_ir.get(field, [])
    )
    explicit_terms = ("nude", "naked", "explicit", "nipples", "sex", "lingerie", "breasts")
    adult_nsfw = any(term in all_terms for term in explicit_terms)
    multiple_subjects = len(subject) > 1 or any(
        re.match(r"^(?:[2-9]|[1-9]\d)\+?(?:girls?|boys?|others?)$", item)
        or item.startswith("multiple ")
        for item in subject
    )
    relation = any(any(hint in item for hint in _RELATION_HINTS) for item in interaction)
    complex_motion = len(action) + len(pose) > 2
    if adult_nsfw and not multiple_subjects and not relation and not complex_motion:
        return "tag_first"
    return "relation_hybrid"

def compile_prompt(char_tags: list[str], other_tags: list[str], nl: str = "",
                   profile: str = "relation_hybrid") -> str:
    """把已知 tag、候选 tag 和可选 NL 编译成模型语义 prompt body.

    quality prefix、LoRA trigger 和 workflow 注入仍由 build_prompt 负责；rating tag 由用户手动控制。
    """
    cleaned_tags = [tag.strip() for tag in other_tags if tag and tag.strip()]
    cleaned_tags = _strip_char_bare_names(cleaned_tags, char_tags)
    result = normalize_tag_order(char_tags, cleaned_tags)
    nl = (nl or "").strip() if profile == "relation_hybrid" else ""
    if result and nl:
        return result + ". " + nl
    return result or nl

async def translate(text: str, reroll: bool = False, image_b64: str | None = None,
                    lora_selections=None, include_meta: bool = False,
                    completion_level: str = DEFAULT_COMPLETION_LEVEL,
                    concept_override: str | None = None) -> tuple:
    """中文构思 -> Anima Prompt。角色 canonical knowledge 与 Visual Composer 分工。

    参考图/非 Reasoning Model 降级路径继续保留历史字典行为；SiliconFlow 普通文本
    把完整剩余意图交给 Composer，不再让 ordinary dict 的全命中绕过构思。
    返回 (prompt_en, breakdown, prompt_ir): breakdown 是既有 5 维展示结构,
    prompt_ir 是 12 字段语义计划; 快速路径或旧视觉协议时二者按实际情况为 None.
    include_meta=True 时追加第四项 prompt_ir_meta，供 API additive 返回，不影响旧内部调用。
    reroll=True: 只对 LLM 路径生效, 高温重出一版不同补全方案, 跳过缓存(探索性, 不污染正常缓存).
    image_b64: ③ 参考图 (data URI 或 base64). 有图走视觉 LLM 提氛围.
    lora_selections: Active LoRA Asset/Profile；存在时所有路径都使用同一 binding context.
    completion_level: auto/faithful/free；concept_override 是用户编辑后的构思控制面。"""
    if not isinstance(text, str):
        raise HTTPException(400, "提示词必须是字符串")
    if len(text) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"提示词过长(>{MAX_USER_PROMPT_CHARS})")
    completion_level = _normalize_completion_level(completion_level)
    concept_override = _normalize_optional_concept(concept_override, "concept_override")
    backend = CFG.get("translate", "none")
    lora_selections = apply_lora_intent_hints(text, lora_selections)
    lora_context, normalized_loras, lora_revision = build_lora_context(lora_selections)
    has_lora = bool(normalized_loras)
    registry = get_lora_registry()
    has_character_lora = any(
        (registry.get(selection["key"]) or {}).get("type") == "character"
        for selection in normalized_loras
    )
    multi_relation_names = _lora_multi_relation_names(normalized_loras, registry)
    appearance_source = text + (("\n" + concept_override) if concept_override else "")
    character_appearance_locks = (
        sorted(_explicit_character_appearance_locks(appearance_source))
        if has_character_lora else []
    )

    def finish(prompt_en: str, breakdown: dict | None, prompt_ir: dict | None,
               meta: dict, bindings: list[dict] | None = None,
               lora_warnings: list[str] | None = None):
        meta = dict(meta)
        meta.update({
            "lora_aware": has_lora,
            "lora_bindings": bindings or [],
            "lora_warnings": lora_warnings or [],
            "registry_revision": lora_revision if has_lora else None,
        })
        result = (prompt_en, breakdown, prompt_ir)
        return result + (meta,) if include_meta else result

    # Layer 0: 角色子串匹配 (移除角色名, 得到剩余文本)
    char_tags, char_remaining = match_characters(text)

    # 普通文本 Composer 直接读取完整剩余意图；参考图/降级后端继续沿用历史属性词典。
    if backend == "siliconflow" and not image_b64:
        hits, remaining = [], char_remaining
    else:
        hits, remaining = match_dict_words(char_remaining)
    misses = [p.strip() for p in re.split(r"[,，、;；\n]+", remaining) if p.strip()]

    # ③ 参考图: 有图走视觉 LLM 提取氛围 (图 + 文本上下文), 不走下面的文本 LLM/快速路径.
    # 图是氛围参考, 文本(若有)给主体; 角色词典仍预匹配(可靠). 不缓存(图探索性, key 含图复杂). 见 D23.
    if image_b64:
        ctx_lines = []
        if char_tags:
            ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
        if hits:
            ctx_lines.append(f"Known attribute tags: {', '.join(hits)}")
        ctx_lines.append(f"User instruction: {', '.join(misses) if misses else '(no specific instruction - extract everything from the image)'}")
        if lora_context:
            ctx_lines.append(lora_context)
            ctx_lines.append(f"Registry revision: {lora_revision}")
        context = "\n".join(ctx_lines)
        try:
            vision_result = await siliconflow_vision_translate(image_b64, context, reroll=reroll)
            new_tags, breakdown, nl, prompt_ir = vision_result[:4]
            lora_choices = vision_result[4] if len(vision_result) > 4 else {}
        except Exception as e:
            raise HTTPException(502, f"参考图理解失败, 请稍后重试 ({e})")
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        result = compile_prompt(char_tags, hits + new_list, nl, infer_render_profile(prompt_ir))
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras, lora_choices)
        result = compile_lora_bindings(result, bindings)
        return finish(
            result, breakdown, prompt_ir,
            _prompt_ir_meta("vision_reference", reroll, prompt_ir, char_tags, hits,
                            completion_level=completion_level),
            bindings, lora_warnings,
        )

    char_fragments = [p.strip() for p in re.split(r"[,，、;；\n]+", char_remaining)
                      if p.strip()]
    # 纯角色名仍走确定性 canonical 快路，避免一句角色名被自动编造新场景；同时补齐构思控制面。
    if (backend == "siliconflow" and char_tags and not char_fragments and
            not has_lora and concept_override is None):
        result = compile_prompt(char_tags, ["1girl", "solo"], profile="tag_first")
        concept = f"用户锁定：{text.strip()}｜模型补全：无"
        return finish(
            result, None, None,
            _prompt_ir_meta("canonical", reroll, char_tags=char_tags,
                            completion_level=completion_level, concept=concept),
        )

    # 参考图/非 Reasoning Model 的旧路径保留 ordinary dict 全命中快路。
    if backend != "siliconflow" and not misses and not has_lora:
        if char_tags and not hits:
            result = compile_prompt(char_tags, ["1girl", "solo"], profile="tag_first")
            concept = f"用户锁定：{text.strip()}｜模型补全：无"
            return finish(result, None, None,
                          _prompt_ir_meta("canonical", reroll, char_tags=char_tags,
                                          completion_level=completion_level,
                                          concept=concept))
        all_tags = char_tags + hits
        if all_tags:
            result = compile_prompt(char_tags, hits, profile="tag_first")
            return finish(result, None, None,
                          _prompt_ir_meta("dictionary", reroll,
                                          char_tags=char_tags, attribute_tags=hits,
                                          completion_level=completion_level))
        raise HTTPException(400, "提示词为空")

    # Layer 2: 有未命中 -> 后端处理
    if backend == "none":
        # 未翻译部分原样保留 (混输英文 tag 时合适)
        result = compile_prompt(char_tags, hits + misses, profile="tag_first")
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras)
        if has_lora:
            lora_warnings.append("Reasoning Model 未启用：已注入确定性 LoRA binding，但未执行语义冲突检查")
        result = compile_lora_bindings(result, bindings)
        return finish(result, None, None,
                      _prompt_ir_meta("faithful", reroll,
                                      char_tags=char_tags, attribute_tags=hits,
                                      completion_level=completion_level),
                      bindings, lora_warnings)

    if backend == "siliconflow":
        # 普通文本 Composer 接收完整剩余意图；ordinary dict 不再抢先裁掉词语或触发全命中。
        hits = []
        misses = char_fragments
        ctx_lines = [
            f"COMPLETION LEVEL: {completion_level.upper()}",
            f"USER IDEA:\n{text}",
        ]
        if char_tags:
            ctx_lines.append(f"KNOWN CANONICAL TAGS: {', '.join(char_tags)}")
        if concept_override:
            ctx_lines.append(
                "CONCEPT OVERRIDE (authoritative; copy unchanged into CONCEPT): "
                + concept_override
            )
        if lora_context:
            ctx_lines.append(lora_context)
            if len(multi_relation_names) >= 2:
                ctx_lines.append(
                    _MULTI_RELATION_NAMES_PREFIX + " "
                    + " | ".join(multi_relation_names)
                )
            if has_character_lora:
                ctx_lines.append(
                    _CHARACTER_APPEARANCE_LOCKS_PREFIX + " "
                    + json.dumps(character_appearance_locks, ensure_ascii=False,
                                 separators=(",", ":"))
                )
            ctx_lines.append(f"Registry revision: {lora_revision}")
        context = "\n".join(ctx_lines)

        cache_key = context
        if not reroll and cache_key in _TRANSLATE_CACHE:
            cached = _TRANSLATE_CACHE[cache_key]
            cached_result, cached_breakdown, cached_ir = cached[:3]
            cached_bindings = cached[3] if len(cached) > 3 else []
            cached_warnings = cached[4] if len(cached) > 4 else []
            cached_concept = cached[5] if len(cached) > 5 else concept_override
            cached_repetition = cached[6] if len(cached) > 6 else False
            return finish(
                cached_result, cached_breakdown, cached_ir,
                _prompt_ir_meta(
                    "visual_composer", reroll, cached_ir, char_tags, hits,
                    completion_level=completion_level, concept=cached_concept,
                    concept_override_applied=concept_override is not None,
                    repetition_collapsed=cached_repetition,
                ),
                cached_bindings, cached_warnings,
            )
        try:
            translated = await siliconflow_translate(context, reroll=reroll)
            new_tags, breakdown, nl, prompt_ir, character_hints = translated[:5]
            lora_choices = translated[5] if len(translated) > 5 else {}
            concept = translated[6] if len(translated) > 6 else None
            repetition_collapsed = bool(translated[7]) if len(translated) > 7 else False
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        if concept_override is not None:
            concept = concept_override
        lookup_results = []
        resolved_char_tags = list(char_tags)
        known_names = set(_character_names())
        active_lora_aliases = lora_selection_aliases(normalized_loras) if has_lora else set()
        # Defense in depth for mocked/legacy callers that bypass siliconflow_translate's repair loop.
        # IR.subject is deliberately not a lookup authority: only explicit CHAR name spans may query/cache.
        character_hints = [
            hint for hint in character_hints
            if _character_hint_issue([hint], text) is None
        ]
        for hint in character_hints:
            name = hint["name"]
            if name.strip().lower() in active_lora_aliases:
                continue
            candidate = _normalize_character_candidate(hint["candidate_tag"]) or hint["candidate_tag"]
            if name in known_names or candidate in char_tags:
                continue
            lookup = await lookup_character(name, candidate)
            lookup_results.append(lookup)
            canonical = lookup.get("canonical_tag")
            if lookup.get("status") == "likely_supported" and canonical not in resolved_char_tags:
                resolved_char_tags.append(canonical)
            elif lookup.get("status") == "unavailable" and candidate not in resolved_char_tags:
                # 兜底: Danbooru 不可达时用 LLM 候选（已归一化），不写 auto cache，只服务本次 Prompt
                resolved_char_tags.append(candidate)
        # 编译: canonical 角色 + Composer Prompt；仅保留确定性计数/显式构图护栏。
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        control_text = text + (("\n" + concept_override) if concept_override else "")
        composer_tags = _prepare_composer_tags(new_list, prompt_ir, control_text,
                                               resolved_char_tags)
        result = compile_prompt(resolved_char_tags, composer_tags, nl,
                                infer_render_profile(prompt_ir))
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras, lora_choices)
        result = compile_lora_bindings(result, bindings)
        # reroll 不写缓存: 探索性结果不应顶掉正常翻译的缓存原版 (见 D19)
        if not reroll:
            if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
                _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
            _TRANSLATE_CACHE[cache_key] = (
                result, breakdown, prompt_ir, bindings, lora_warnings,
                concept, repetition_collapsed,
            )
        return finish(
            result, breakdown, prompt_ir,
            _prompt_ir_meta(
                "visual_composer", reroll, prompt_ir,
                resolved_char_tags, hits, lookup_results,
                completion_level=completion_level, concept=concept,
                concept_override_applied=concept_override is not None,
                repetition_collapsed=repetition_collapsed,
            ),
            bindings, lora_warnings,
        )

    if backend == "google":
        try:
            translated_missing = await google_translate_batch(misses)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        result = compile_prompt(char_tags, hits + translated_missing, profile="tag_first")
        bindings, lora_warnings, _ = resolve_lora_selections(normalized_loras)
        if has_lora:
            lora_warnings.append("Google 翻译降级路径未执行 LoRA 语义冲突检查")
        result = compile_lora_bindings(result, bindings)
        return finish(result, None, None,
                      _prompt_ir_meta("translation", reroll,
                                      char_tags=char_tags, attribute_tags=hits,
                                      completion_level=completion_level),
                      bindings, lora_warnings)

    raise HTTPException(500, f"未知的 translate 后端: {backend}")

async def google_translate_batch(texts: list[str]) -> list[str]:
    """免费 Google Translate gtx 端点 (本机需可访问谷歌). 任一条失败抛异常."""
    res = []
    for t in texts:
        r = await CLIENT.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "zh-CN", "tl": "en", "dt": "t", "q": t},
            timeout=10,
        )
        r.raise_for_status()
        res.append("".join(seg[0] for seg in r.json()[0]).strip())
    return res
