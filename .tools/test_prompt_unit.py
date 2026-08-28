# -*- coding: utf-8 -*-
"""零依赖 Prompt Engine 纯函数回归检查。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import main


def test_character_match():
    tags, remaining = main.match_characters("甘雨")
    assert tags == ["ganyu_(genshin_impact)"], tags
    assert remaining == "", repr(remaining)


def test_bare_character_fast_path():
    prompt_en, breakdown, prompt_ir = asyncio.run(main.translate("甘雨"))
    assert prompt_en == "1girl, solo, ganyu_(genshin_impact)", prompt_en
    assert breakdown is None, breakdown
    assert prompt_ir is None, prompt_ir


def test_ir_protocol_and_breakdown_derivation():
    output = (
        'IR: {"subject":["1girl"],"appearance":["pink hair"],"clothing":[],"action":["standing"],'
        '"pose":[],"interaction":[],"scene":["beach"],"composition":["full body"],'
        '"lighting":["bright sunlight"],"mood":["carefree"],"style":["anime style"],"constraints":[]}\n'
        "TAGS: 1girl, pink hair, beach\n"
        "NL:\n"
    )
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags == "1girl, pink hair, beach", tags
    assert breakdown == {
        "scene": "beach",
        "composition": "full body",
        "mood": "carefree",
        "lighting": "bright sunlight",
        "style": "anime style",
    }, breakdown
    assert nl == "", nl
    assert prompt_ir and prompt_ir["subject"] == ["1girl"], prompt_ir


def test_legacy_protocol_fallback():
    output = "scene: bedroom\ncomposition: close-up\nTAGS: 1girl, bedroom\nNL:\n"
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags == "1girl, bedroom", tags
    assert breakdown == {"scene": "bedroom", "composition": "close-up"}, breakdown
    assert nl == "", nl
    assert prompt_ir is None, prompt_ir


def test_tag_order_and_deduplication():
    result = main.normalize_tag_order(
        ["ganyu_(genshin_impact)"],
        ["blue eyes", "1girl", "solo", "blue eyes"],
    )
    assert result == "1girl, solo, ganyu_(genshin_impact), blue eyes", result


def test_character_bare_name_is_removed():
    result = main._strip_char_bare_names(
        ["ganyu", "ganyu_(genshin_impact)", "blue eyes"],
        ["ganyu_(genshin_impact)"],
    )
    assert result == ["ganyu_(genshin_impact)", "blue eyes"], result


def test_compile_prompt_merges_tags_and_nl():
    result = main.compile_prompt(
        ["ganyu_(genshin_impact)"],
        ["ganyu", "1girl", "solo", "blue eyes", "blue eyes"],
        "She stands by the window.",
    )
    assert result == "1girl, solo, ganyu_(genshin_impact), blue eyes. She stands by the window.", result


def test_experiment_negative_override_is_opt_in():
    workflow = main.WORKFLOWS["anima"]
    previous = workflow.pop("negative_text_node", None)
    workflow["negative_text_node"] = "4"
    try:
        payload = main.build_prompt("anima", "1girl, sitting", 832, 1216,
                                    negative_text="standing, walking")
        negative = payload["prompt"]["4"]["inputs"]
        assert negative["wildcard_text"].endswith("standing, walking"), negative
        assert negative["populated_text"].endswith("standing, walking"), negative
    finally:
        if previous is None:
            workflow.pop("negative_text_node", None)
        else:
            workflow["negative_text_node"] = previous


def test_build_prompt_routes_txt2img_and_img2img_explicitly():
    txt2img = main.build_prompt("anima", "1girl, beach", 1024, 1536)["prompt"]
    assert txt2img["42"]["inputs"]["select"] == 1
    assert txt2img["56"]["inputs"]["width"] == 1024
    assert txt2img["56"]["inputs"]["height"] == 1536

    img2img = main.build_prompt(
        "anima", "1girl, beach", 1024, 1536,
        image_filename="uploaded.png", denoise=0.35,
    )["prompt"]
    assert img2img["42"]["inputs"]["select"] == 2
    assert img2img["0"]["inputs"]["image"] == "uploaded.png"
    assert img2img["6"]["inputs"]["denoise"] == 0.35


def test_build_prompt_keeps_rating_tags_manual():
    prompt_node = str(main.WORKFLOWS["anima"]["prompt_node"])
    plain = main.build_prompt("anima", "1girl, sitting", 832, 1216)["prompt"]
    plain_text = plain[prompt_node]["inputs"]["text"]
    assert "safe" not in plain_text and "explicit" not in plain_text, plain_text

    adult = main.build_prompt("anima", "1girl, nude", 832, 1216)["prompt"]
    adult_text = adult[prompt_node]["inputs"]["text"]
    assert "explicit" not in adult_text, adult_text

    manual = main.build_prompt("anima", "safe, 1girl, sitting", 832, 1216)["prompt"]
    manual_text = manual[prompt_node]["inputs"]["text"]
    assert manual_text.count("safe") == 1, manual_text


def test_render_profile_inference():
    simple = {"subject": ["1girl"], "action": ["standing"], "pose": [], "interaction": []}
    nsfw_simple = {"subject": ["1girl"], "appearance": ["adult", "nude"],
                   "action": ["standing"], "pose": ["legs apart"], "interaction": []}
    complex_pose = {"subject": ["1girl"], "action": ["running", "looking back"],
                    "pose": ["one leg raised"], "interaction": []}
    two_people = {"subject": ["2boys"], "action": ["fighting"], "pose": [],
                  "interaction": ["facing each other"]}
    assert main.infer_render_profile(simple) == "relation_hybrid"
    assert main.infer_render_profile(nsfw_simple) == "tag_first"
    assert main.infer_render_profile(complex_pose) == "relation_hybrid"
    assert main.infer_render_profile(two_people) == "relation_hybrid"


def test_tag_first_profile_drops_nl():
    result = main.compile_prompt([], ["1girl", "coffee"], "She holds one cup.", "tag_first")
    assert result == "1girl, coffee", result
    result = main.compile_prompt([], ["2boys", "sword"], "They face each other.", "relation_hybrid")
    assert result.endswith(". They face each other."), result


def test_prompt_ir_meta_is_additive():
    result = asyncio.run(main.translate("甘雨", include_meta=True))
    assert len(result) == 4, result
    prompt_en, breakdown, prompt_ir, meta = result
    assert prompt_en == "1girl, solo, ganyu_(genshin_impact)", prompt_en
    assert breakdown is None and prompt_ir is None
    assert meta["mode"] == "canonical", meta
    assert meta["expansion_applied"] is False, meta
    assert meta["concept"] == "用户锁定：甘雨｜模型补全：无", meta
    assert meta["completion_level"] == "auto", meta


def test_reroll_uses_new_painter_plan_metadata():
    meta = main._prompt_ir_meta("painter_expansion", reroll=True)
    assert meta["expansion_applied"] is True, meta
    assert meta["reroll_strategy"] == "new_painter_plan", meta


def test_painter_prompt_protocol_is_final_prompt():
    output = (
        'IR: {"subject":["1girl"],"appearance":["pink hair"],"clothing":[],"action":["standing"],'
        '"pose":[],"interaction":[],"scene":["cherry blossom tree"],"composition":["medium shot"],'
        '"lighting":["soft afternoon light"],"mood":["gentle"],"style":["anime style"],"constraints":[]}\n'
        "PROMPT: 1girl, pink hair, standing under cherry blossom tree, medium shot, soft afternoon light, anime style\n"
    )
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags.startswith("1girl, pink hair"), tags
    assert nl == "", nl
    assert breakdown["scene"] == "cherry blossom tree", breakdown
    assert prompt_ir and prompt_ir["subject"] == ["1girl"], prompt_ir


def test_visual_composer_content_fidelity_contract():
    prompt = main.PAINTER_SYSTEM_PROMPT
    assert "visible body detail, act, and framing requirement as a hard visual lock" in prompt
    assert "do not euphemize, conceal, crop out, or replace it" in prompt
    assert "Do not infer either nudity or coverage from a sexual act alone" in prompt
    assert "override wins over an active LoRA outfit only for the affected area" in prompt


def test_visual_composer_protocol_is_strict_and_collapses_whole_repeat():
    output = (
        "CONCEPT: 用户锁定：蓝发少女｜模型补全：百合花束与窗边逆光\n"
        'IR: {"subject":["1girl"],"appearance":["blue hair"],"clothing":[],"action":["holding bouquet"],"pose":[],"interaction":[],"scene":["window"],"composition":["upper body"],"lighting":["backlighting"],"mood":["gentle"],"style":[],"constraints":[]}\n'
        "PROMPT: 1girl, solo, blue hair, holding bouquet, window, backlighting, "
        "1girl, solo, blue hair, holding bouquet, window, backlighting\n"
    )
    prompt, breakdown, nl, prompt_ir, hints, choices, concept, collapsed = (
        main._parse_composer_output(output)
    )
    assert prompt == "1girl, solo, blue hair, holding bouquet, window, backlighting", prompt
    assert collapsed is True
    assert concept == "用户锁定：蓝发少女｜模型补全：百合花束与窗边逆光", concept
    assert prompt_ir["scene"] == ["window"] and breakdown["scene"] == "window"
    assert nl == "" and hints == [] and choices == {}

    missing_concept = output.split("\n", 1)[1]
    try:
        main._parse_composer_output(missing_concept)
    except RuntimeError as exc:
        assert "协议" in str(exc), exc
    else:
        raise AssertionError("Composer response without CONCEPT must be rejected")

    incomplete_ir = output.replace(',"constraints":[]', "")
    try:
        main._parse_composer_output(incomplete_ir)
    except RuntimeError as exc:
        assert "IR" in str(exc), exc
    else:
        raise AssertionError("Composer IR without all 12 fields must be rejected")


def test_visual_composer_rejects_unrenderable_model_additions():
    close_crop = (
        "CONCEPT: 用户锁定：角色与泳装｜模型补全：斜倚躺椅，上衣被轻拉，裙摆被掀起，低角度近景\n"
        'IR: {"subject":["1girl"],"appearance":[],"clothing":["swimsuit"],'
        '"action":["adjusting top","lifting skirt"],"pose":["legs crossed"],'
        '"interaction":[],"scene":["poolside"],"composition":["low-angle close-up"],'
        '"lighting":["dappled sunlight"],"mood":["languid"],"style":[],"constraints":[]}\n'
        "PROMPT: 1girl, solo, adjusting top, lifting skirt, legs crossed, low-angle close-up, poolside\n"
    )
    try:
        main._parse_composer_output(close_crop)
    except RuntimeError as exc:
        assert "可画性冲突" in str(exc), exc
    else:
        raise AssertionError("close crop plus off-frame leg action must be rejected")

    two_manual_actions = close_crop.replace(
        '"pose":["legs crossed"]', '"pose":["reclining"]'
    ).replace(
        '"composition":["low-angle close-up"]',
        '"composition":["three-quarter body view"]',
    ).replace(
        "legs crossed, low-angle close-up", "reclining, three-quarter body view",
    )
    try:
        main._parse_composer_output(two_manual_actions)
    except RuntimeError as exc:
        assert "多个手部/服装操作" in str(exc), exc
    else:
        raise AssertionError("multiple model-invented garment actions must be rejected")

    accidental_crop = (
        "CONCEPT: 用户锁定：蕾米埃尔黑色形态与深色高领上衣｜"
        "模型补全：一手轻捏裙摆，一手抚过发梢；中景近身裁切，聚焦上半身与手部细节\n"
        'IR: {"subject":["1girl"],"appearance":[],"clothing":["dark high-collared top","short skirt"],'
        '"action":["lifting skirt hem","brushing hair"],"pose":["looking back over shoulder"],'
        '"interaction":[],"scene":["minimal dark background"],"composition":["medium shot","upper body focus"],'
        '"lighting":["side top lighting"],"mood":["alluring"],"style":[],"constraints":[]}\n'
        "PROMPT: 1girl, solo, looking back over shoulder, short skirt with hem lifted by one hand, "
        "other hand brushing through hair, medium shot, upper body focus, minimal dark background\n"
    )
    try:
        main._parse_composer_output(accidental_crop)
    except RuntimeError as exc:
        assert "交互区域完整入镜" in str(exc), exc
    else:
        raise AssertionError("upper-body crop plus model-added skirt interaction must be rejected")

    intentional_three_quarter = accidental_crop.replace(
        "一手轻捏裙摆，一手抚过发梢；中景近身裁切，聚焦上半身与手部细节",
        "以一手轻捏裙摆为核心，另一只手自然垂落；采用四分之三身构图",
    ).replace(
        '"action":["lifting skirt hem","brushing hair"]',
        '"action":["lifting skirt hem"]',
    ).replace(
        '"composition":["medium shot","upper body focus"]',
        '"composition":["three-quarter body view"]',
    ).replace(
        "other hand brushing through hair, medium shot, upper body focus",
        "other hand resting naturally, three-quarter body view",
    )
    parsed = main._parse_composer_output(intentional_three_quarter)
    assert "three-quarter body view" in parsed[0], parsed[0]


def test_composer_guard_only_enforces_count_and_explicit_full_body_lock():
    tags = main._prepare_composer_tags(
        ["close-up", "upper body", "painterly", "silhouette", "soft daylight"],
        {"subject": ["girl"]},
        "少女从头到脚完整可见",
        [],
    )
    assert tags[0] == "1girl", tags
    assert "full body" in tags, tags
    assert "close-up" not in tags and "upper body" not in tags, tags
    # 新路径不再继承旧 Painter 对画风/剪影的主观删改。
    assert "painterly" in tags and "silhouette" in tags, tags


def test_workflow_negative_contains_compact_anatomy_guard():
    workflow = json.loads((ROOT / "server" / "workflows" / "AnimaFull.json").read_text(
        encoding="utf-8"))
    negative = workflow["4"]["inputs"]
    required = (
        "bad anatomy", "bad hands", "missing fingers", "extra fingers",
        "fused fingers", "extra arms", "extra legs", "bad feet", "malformed feet",
    )
    for field in ("wildcard_text", "populated_text"):
        for term in required:
            assert term in negative[field], (field, term, negative[field])


def test_siliconflow_composer_bypasses_ordinary_dict_and_isolates_completion_cache():
    old_translate = main.siliconflow_translate
    old_match_dict = main.match_dict_words
    old_backend = main.CFG.get("translate")
    old_cache = dict(main._TRANSLATE_CACHE)
    calls = []

    def fail_dict(_):
        raise AssertionError("SiliconFlow text Composer must not call ordinary dict")

    async def fake_translate(context, reroll=False):
        calls.append(context)
        output = (
            "CONCEPT: 用户锁定：黑发少女｜模型补全：玻璃花温室与晨光\n"
            'IR: {"subject":["1girl"],"appearance":["black hair"],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["greenhouse"],"composition":["full body"],"lighting":["morning light"],"mood":["gentle"],"style":[],"constraints":[]}\n'
            "PROMPT: 1girl, solo, black hair, greenhouse, full body, morning light\n"
        )
        return main._parse_composer_output(output)

    try:
        main.CFG["translate"] = "siliconflow"
        main.siliconflow_translate = fake_translate
        main.match_dict_words = fail_dict
        main._TRANSLATE_CACHE = {}
        auto = asyncio.run(main.translate(
            "黑发少女，画得好看一点", completion_level="auto", include_meta=True))
        free = asyncio.run(main.translate(
            "黑发少女，画得好看一点", completion_level="free", include_meta=True))
        assert len(calls) == 2, calls
        assert "COMPLETION LEVEL: AUTO" in calls[0], calls[0]
        assert "COMPLETION LEVEL: FREE" in calls[1], calls[1]
        assert auto[3]["mode"] == "visual_composer", auto[3]
        assert auto[3]["concept"].startswith("用户锁定："), auto[3]
        assert free[3]["completion_level"] == "free", free[3]
    finally:
        main.siliconflow_translate = old_translate
        main.match_dict_words = old_match_dict
        if old_backend is None:
            main.CFG.pop("translate", None)
        else:
            main.CFG["translate"] = old_backend
        main._TRANSLATE_CACHE = old_cache


def test_concept_override_is_authoritative_and_validated():
    old_translate = main.siliconflow_translate
    old_backend = main.CFG.get("translate")
    old_cache = dict(main._TRANSLATE_CACHE)
    override = "用户锁定：黑发少女｜模型补全：雨后荷塘、青色薄纱裙与侧逆光"

    async def fake_translate(context, reroll=False):
        assert override in context, context
        output = (
            "CONCEPT: 用户锁定：错误｜模型补全：错误\n"
            'IR: {"subject":["1girl"],"appearance":["black hair"],"clothing":["teal dress"],"action":[],"pose":[],"interaction":[],"scene":["lotus pond"],"composition":[],"lighting":["rim light"],"mood":[],"style":[],"constraints":[]}\n'
            "PROMPT: 1girl, solo, black hair, teal dress, lotus pond, rim light\n"
        )
        return main._parse_composer_output(output)

    try:
        main.CFG["translate"] = "siliconflow"
        main.siliconflow_translate = fake_translate
        main._TRANSLATE_CACHE = {}
        _, _, _, meta = asyncio.run(main.translate(
            "黑发少女", concept_override=override, include_meta=True))
        assert meta["concept"] == override, meta
        assert meta["concept_override_applied"] is True, meta
    finally:
        main.siliconflow_translate = old_translate
        if old_backend is None:
            main.CFG.pop("translate", None)
        else:
            main.CFG["translate"] = old_backend
        main._TRANSLATE_CACHE = old_cache

    try:
        main._normalize_optional_concept("只写一段自由文本", "concept_override")
    except main.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("unstructured concept override must be rejected")


def test_painter_tag_guard_preserves_woman_and_suppresses_unrequested_silhouette():
    tags = main._prepare_painter_tags(
        ["woman", "silhouette of a woman", "bedroom"],
        {"subject": ["woman"]},
        "女性裸体躺在卧室的床上",
        [],
    )
    assert tags[0] == "1girl", tags
    assert all("silhouette" not in tag for tag in tags), tags


def test_painter_tag_guard_keeps_nsfw_body_framing_and_default_anime_style():
    tags = main._prepare_painter_tags(
        ["woman", "nude", "close-up", "painterly", "soft lighting"],
        {"subject": ["woman"], "appearance": ["nude"]},
        "女性裸体躺在卧室的床上",
        [],
    )
    assert "1girl" in tags, tags
    assert "close-up" not in tags and "painterly" not in tags, tags
    assert "three-quarter view" in tags, tags


def test_painter_tag_guard_preserves_explicit_user_intent():
    tags = main._prepare_painter_tags(
        ["1girl", "black lace lingerie", "seductive"],
        {"subject": ["1girl"], "constraints": ["nsfw"]},
        "成年女性裸体穿着黑色蕾丝内衣",
        [],
    )
    assert "nude" in tags, tags


def test_character_hint_protocol_parser():
    hints = main._parse_character_hints(
        "CHAR: 帕姆 => pom_pom_(honkai:_star_rail); 未知角色 => unknown_tag"
    )
    assert hints == [
        {"name": "帕姆", "candidate_tag": "pom_pom_(honkai:_star_rail)"},
        {"name": "未知角色", "candidate_tag": "unknown_tag"},
    ], hints


def test_character_hint_ir_fallback():
    hints = main._infer_character_hints_from_ir(
        {"subject": ["hakurei_reimu_(touhou)"], "appearance": []},
        ["博丽灵梦"],
        set(),
    )
    assert hints == [{"name": "博丽灵梦", "candidate_tag": "hakurei_reimu_(touhou)"}], hints
    assert main._infer_character_hints_from_ir(
        {"subject": ["ganyu_(genshin_impact)"]},
        ["站在望舒客栈的阳台上"],
        set(),
        ["ganyu_(genshin_impact)"],
    ) == []
    assert main._infer_character_hints_from_ir(
        {"subject": ["misaka_mikoto"]},
        ["御坂美琴"],
        set(),
    ) == [{"name": "御坂美琴", "candidate_tag": "misaka_mikoto"}]
    assert main._infer_character_hints_from_ir(
        {"subject": ["yukinoshita yukino"]},
        ["雪之下雪乃"],
        set(),
    ) == [{"name": "雪之下雪乃", "candidate_tag": "yukinoshita_yukino"}]


def test_character_bare_name_space_variant_is_removed():
    result = main._strip_char_bare_names(
        ["ai hoshino", "ai_hoshino", "stage"],
        ["ai_hoshino_(oshi_no_ko)"],
    )
    assert result == ["stage"], result
    result = main._strip_char_bare_names(
        ["yukinoshita yukino", "long black hair"],
        ["yukinoshita_yukino"],
    )
    assert result == ["long black hair"], result


def test_danbooru_character_classification():
    likely_supported = main._classify_danbooru_rows(
        [{"name": "known_char", "category": 4, "post_count": 100,
          "is_deprecated": False}],
        "known_char",
    )
    weak = main._classify_danbooru_rows(
        [{"name": "rare_char", "category": 4, "post_count": 2,
          "is_deprecated": False}],
        "rare_char",
    )
    artist = main._classify_danbooru_rows(
        [{"name": "artist_name", "category": 1, "post_count": 10000,
          "is_deprecated": False}],
        "artist_name",
    )
    assert likely_supported["status"] == "likely_supported", likely_supported
    assert weak["status"] == "weak", weak
    assert artist["status"] == "absent", artist


def test_auto_character_cache_write_and_match():
    with tempfile.TemporaryDirectory() as temp:
        old_dir, old_path, old_auto = main.KNOWLEDGE_CACHE_DIR, main.CHAR_AUTO_PATH, main.CHAR_AUTO
        try:
            cache_dir = Path(temp)
            main.KNOWLEDGE_CACHE_DIR = cache_dir
            main.CHAR_AUTO_PATH = cache_dir / "characters_auto.yaml"
            main.CHAR_AUTO = main.HotDict(main.CHAR_AUTO_PATH, key_fn=lambda s: s)
            main._record_auto_character("测试角色", "test_character_(series)")
            tags, remaining = main.match_characters("测试角色站在街上")
            assert tags == ["test_character_(series)"], tags
            assert remaining == "站在街上", repr(remaining)
        finally:
            main.KNOWLEDGE_CACHE_DIR, main.CHAR_AUTO_PATH, main.CHAR_AUTO = old_dir, old_path, old_auto


def test_unavailable_lookup_is_retryable():
    class FakeResponse:
        status_code = 503
        text = "temporary"

        def json(self):
            return {}

    calls = 0

    async def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    old_get = main.CLIENT.get
    old_cache, old_loaded = main._CHAR_LOOKUP_CACHE, main._CHAR_LOOKUP_CACHE_LOADED
    try:
        main.CLIENT.get = fake_get
        main._CHAR_LOOKUP_CACHE = {}
        main._CHAR_LOOKUP_CACHE_LOADED = True
        first = asyncio.run(main.lookup_character("临时角色", "temporary_character"))
        second = asyncio.run(main.lookup_character("临时角色", "temporary_character"))
        assert first["status"] == second["status"] == "unavailable"
        assert calls == 2, calls
        assert "临时角色|temporary_character" not in main._CHAR_LOOKUP_CACHE
    finally:
        main.CLIENT.get = old_get
        main._CHAR_LOOKUP_CACHE, main._CHAR_LOOKUP_CACHE_LOADED = old_cache, old_loaded


def test_unknown_character_fallback_on_unavailable():
    old_translate = main.siliconflow_translate
    old_lookup = main.lookup_character
    old_dict = main.match_dict_words
    old_cache = dict(main._TRANSLATE_CACHE)

    async def fake_translate(context, reroll=False):
        out = (
            'IR: {"subject":["yukinoshita yukino"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["classroom"],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
            "PROMPT: yukinoshita yukino, classroom\n"
        )
        return main._parse_structured_output(out) + (main._parse_character_hints(out),)

    async def fake_lookup(name, candidate):
        return {"name": name, "candidate_tag": candidate, "canonical_tag": "",
                "post_count": 0, "status": "unavailable", "source": "danbooru", "error": "mock down"}

    def fake_dict(text):
        return [], text

    main.siliconflow_translate = fake_translate
    main.lookup_character = fake_lookup
    main.match_dict_words = fake_dict
    main._TRANSLATE_CACHE = {}
    try:
        prompt, breakdown, ir, meta = asyncio.run(
            main.translate("雪之下雪乃坐在教室里", include_meta=True)
        )
        assert "yukinoshita_yukino" in prompt, prompt
        assert "yukinoshita yukino" not in prompt, prompt
        assert meta["character_lookup"][0]["status"] == "unavailable", meta
    finally:
        main.siliconflow_translate = old_translate
        main.lookup_character = old_lookup
        main.match_dict_words = old_dict
        main._TRANSLATE_CACHE = old_cache


def test_lora_registry_preserves_nested_profiles():
    registry = main.get_lora_registry()
    denia = registry["denia"]
    assert denia["profiles"]["white"]["required_tags"] == ["denia \\(wuthering waves\\)"]
    assert denia["profiles"]["black"]["optional_tags"]["arm_tattoo"]["tags"] == ["arm tattoo"]
    assert denia["registry_revision"] == main.LORA_REGISTRY.snapshot()[1]
    deepseek = registry["deepseek_maid"]["profiles"]["maid"]
    assert deepseek["required_tags"] == ["deepseek_whale_girl", "deepseek_maid_outfit"]
    assert deepseek["default_tags"] == []
    assert "very long hair" in deepseek["optional_tags"]["identity_front"]["tags"]
    assert "black mary janes" in deepseek["optional_tags"]["maid_front_full"]["tags"]
    assert deepseek["verified"] == "candidate"
    assert registry["deepseek_maid"]["strength_model"] == 0.85


def test_bright_afternoon_uses_daylight_not_golden_hour():
    tags, remaining = main.match_dict_words("明亮午后光线")
    assert "bright afternoon" in tags, tags
    assert "high sun" in tags, tags
    assert "golden" not in tags and "golden hour" not in tags, tags
    assert remaining == "光线", remaining


def test_hot_lora_registry_keeps_last_good_snapshot():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "registry.yaml"
        path.write_text(
            "schema_version: 1\nloras:\n  test:\n    name: Test\n    type: style\n"
            "    file: test.safetensors\n    trigger_policy: none\n",
            encoding="utf-8",
        )
        hot = main.HotLoraRegistry(path)
        before, revision = hot.snapshot()
        assert before["loras"]["test"]["name"] == "Test"
        path.write_text("schema_version: [broken", encoding="utf-8")
        after, after_revision = hot.snapshot()
        assert after == before
        assert after_revision == revision


def test_lora_legacy_key_resolves_explicit_profile():
    bindings, warnings, revision = main.resolve_lora_selections(["denia_black"])
    assert not warnings, warnings
    assert revision == main.LORA_REGISTRY.snapshot()[1]
    assert bindings[0]["key"] == "denia"
    assert bindings[0]["profile"] == "black"
    assert bindings[0]["resolved_by"] == "explicit"
    assert bindings[0]["injected_tags"][0] == "blackdenia \\(wuthering waves\\)"


def test_lora_auto_profile_and_optional_ids_are_whitelisted():
    bindings, warnings, _ = main.resolve_lora_selections(
        [{"key": "denia", "mode": "auto"}],
        {"denia": {"profile": "white", "optional": ["white_dress", "made_up"]}},
    )
    assert bindings[0]["profile"] == "white"
    assert bindings[0]["optional"] == ["white_dress"]
    assert "white dress" in bindings[0]["injected_tags"]
    assert any("made_up" in warning for warning in warnings), warnings


def test_lora_explicit_profile_still_accepts_semantic_optional_choice():
    bindings, warnings, _ = main.resolve_lora_selections(
        [{"key": "denia", "profile": "white", "mode": "explicit"}],
        {"denia": {"profile": "black", "optional": ["white_dress"]}},
    )
    assert not warnings, warnings
    assert bindings[0]["profile"] == "white"
    assert bindings[0]["resolved_by"] == "explicit"
    assert bindings[0]["optional"] == ["white_dress"]
    assert "white dress" in bindings[0]["injected_tags"]


def test_lora_intent_alias_locks_profile_and_optional_ids():
    selections = main.apply_lora_intent_hints(
        "黑达妮娅露出手臂纹身", [{"key": "denia", "mode": "auto"}]
    )
    assert selections == [{
        "key": "denia", "profile": "black", "mode": "auto", "optional": ["arm_tattoo"]
    }], selections
    bindings, warnings, _ = main.resolve_lora_selections(selections)
    assert not warnings, warnings
    assert bindings[0]["resolved_by"] == "intent_alias"
    assert "arm tattoo" in bindings[0]["injected_tags"]


def test_deepseek_view_recipes_are_conditional():
    base, warnings, _ = main.resolve_lora_selections([
        {"key": "deepseek_maid", "profile": "maid", "mode": "explicit"}
    ])
    assert not warnings, warnings
    assert base[0]["injected_tags"] == ["deepseek_whale_girl", "deepseek_maid_outfit"]

    selections = main.apply_lora_intent_hints(
        "deepseek正面全身站立",
        [{"key": "deepseek_maid", "profile": "maid", "mode": "explicit"}],
    )
    assert selections[0]["optional"] == ["identity_front", "maid_front_full"], selections
    detailed, warnings, _ = main.resolve_lora_selections(selections)
    assert not warnings, warnings
    assert "very long hair" in detailed[0]["injected_tags"]
    assert "black mary janes" in detailed[0]["injected_tags"]


def test_lora_registry_revision_rejects_stale_binding():
    try:
        main.resolve_lora_selections(["denia_white"], expected_revision="stale-revision")
    except main.HTTPException as exc:
        assert exc.status_code == 409, exc
        assert "LoRA Registry" in str(exc.detail), exc.detail
    else:
        raise AssertionError("stale LoRA registry revision must be rejected")


def test_lora_binding_compiler_is_exact_and_idempotent():
    bindings, _, _ = main.resolve_lora_selections(["denia_white"])
    first = main.compile_lora_bindings("1girl, solo, denia wuthering waves, beach, sunset", bindings)
    second = main.compile_lora_bindings(first, bindings)
    assert first == second
    assert first.count("denia \\(wuthering waves\\)") == 1, first
    assert "denia wuthering waves" not in first, first
    assert first.startswith("1girl, solo, denia \\(wuthering waves\\)"), first


def test_lora_binding_compiler_removes_sibling_profile_trigger():
    bindings, warnings, _ = main.resolve_lora_selections([
        {"key": "remielle_dan", "profile": "swim", "mode": "explicit"}
    ])
    assert not warnings, warnings
    compiled = main.compile_lora_bindings(
        "1girl, solo, remielle_dan \\(swim\\), remielle_dan, Remielle Dan swimsuit form, "
        "white sleeveless top with purple ruffles, remielle_dan lounging by the pool, reclining, poolside",
        bindings,
    )
    segments = [segment.strip() for segment in compiled.split(",")]
    assert "remielle_dan" not in segments, compiled
    assert "Remielle Dan swimsuit form" not in segments, compiled
    assert "white sleeveless top with purple ruffles" not in segments, compiled
    assert segments.count("remielle_dan \\(swim\\)") == 1, compiled
    assert segments.count("wearing a white sleeveless top with purple ruffles") == 1, compiled
    assert "lounging by the pool" in segments, compiled
    assert not any(segment.startswith("remielle dan lounging") for segment in segments), compiled
    assert "reclining" in segments and "poolside" in segments, compiled


def test_character_lora_blocks_profile_name_appearance_inference():
    assert main._explicit_character_appearance_locks(
        "蕾米埃尔黑色形态，人物为主，背景从简"
    ) == set()
    assert main._explicit_character_appearance_locks(
        "蕾米埃尔黑色形态，改成黑色长发和蓝眼睛"
    ) == {"black hair", "blue eyes"}
    assert main._explicit_character_appearance_locks(
        "Remielle black form with pink hair and purple eyes"
    ) == {"pink hair", "purple eyes"}
    assert main._explicit_character_appearance_locks(
        "黑色发饰，蓝色眼影，人物站在窗前"
    ) == set()

    prompt_ir = {field: [] for field in main._IR_FIELDS}
    prompt_ir["appearance"] = ["long black hair", "red eyes", "star hair ornament"]
    prompt = "1girl, solo, long black hair, red eyes, star hair ornament, dark short skirt"
    issue = main._composer_character_lora_appearance_issue(prompt_ir, prompt, set())
    assert issue and "black hair" in issue and "red eyes" in issue, issue

    issue = main._composer_character_lora_appearance_issue(
        prompt_ir, prompt, {"black hair", "red eyes"}
    )
    assert issue is None, issue


def test_character_lora_appearance_conflict_repairs_once():
    old_client = main.CLIENT
    old_key = main.CFG.get("siliconflow_api_key")
    payloads = []

    bad = (
        "CONCEPT: 用户锁定：蕾米埃尔黑色形态｜模型补全：黑色长发与暗色背景\n"
        'IR: {"subject":["1girl"],"appearance":["long black hair"],"clothing":[],'
        '"action":[],"pose":[],"interaction":[],"scene":["dark background"],'
        '"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
        'LORA: {"remielle_dan":{"profile":"black","optional":[]}}\n'
        "PROMPT: 1girl, solo, long black hair, dark background\n"
    )
    repaired = (
        "CONCEPT: 用户锁定：蕾米埃尔黑色形态｜模型补全：暗色背景\n"
        'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":[],"pose":[],'
        '"interaction":[],"scene":["dark background"],"composition":[],"lighting":[],'
        '"mood":[],"style":[],"constraints":[]}\n'
        'LORA: {"remielle_dan":{"profile":"black","optional":[]}}\n'
        "PROMPT: 1girl, solo, dark background\n"
    )

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, output):
            self.output = output

        def json(self):
            return {"choices": [{"message": {"content": self.output}}]}

    class FakeClient:
        async def post(self, *args, **kwargs):
            payloads.append(kwargs["json"])
            return FakeResponse(bad if len(payloads) == 1 else repaired)

    context = (
        "COMPLETION LEVEL: AUTO\nUSER IDEA:\n蕾米埃尔黑色形态\n"
        "ACTIVE LORA CONTEXT\n"
        "USER-LOCKED CHARACTER APPEARANCE OVERRIDES: []"
    )
    main.CLIENT = FakeClient()
    main.CFG["siliconflow_api_key"] = "test-key"
    try:
        result = asyncio.run(main.siliconflow_translate(context))
        assert len(payloads) == 2, payloads
        assert "black hair" not in result[0], result[0]
        repair_text = payloads[1]["messages"][1]["content"]
        assert "LoRA 身份冲突" in repair_text and "black hair" in repair_text, repair_text
    finally:
        main.CLIENT = old_client
        if old_key is None:
            main.CFG.pop("siliconflow_api_key", None)
        else:
            main.CFG["siliconflow_api_key"] = old_key

    accessory_only = {field: [] for field in main._IR_FIELDS}
    accessory_only["appearance"] = ["black hair ribbon", "star hair ornament"]
    issue = main._composer_character_lora_appearance_issue(
        accessory_only, "1girl, solo, black hair ribbon, star hair ornament", set()
    )
    assert issue is None, issue


def test_lora_choice_protocol_parser():
    output = (
        'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":[],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
        'LORA: {"denia":{"profile":"white","optional":["white_dress"]}}\n'
        'PROMPT: 1girl, solo, beach\n'
    )
    choices = main._parse_lora_choices(output)
    assert choices == {"denia": {"profile": "white", "optional": ["white_dress"]}}, choices


def test_active_lora_forces_painter_and_compiles_binding():
    old_translate = main.siliconflow_translate
    old_cache = dict(main._TRANSLATE_CACHE)
    calls = []

    async def fake_translate(context, reroll=False):
        calls.append(context)
        out = (
            'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":["standing"],"pose":[],"interaction":[],"scene":["beach"],"composition":["full body"],"lighting":["sunset"],"mood":["calm"],"style":[],"constraints":[]}\n'
            'LORA: {"denia":{"profile":"white","optional":[]}}\n'
            'PROMPT: 1girl, solo, standing, beach, full body, sunset\n'
        )
        return main._parse_structured_output(out) + ([], main._parse_lora_choices(out))

    main.siliconflow_translate = fake_translate
    main._TRANSLATE_CACHE = {}
    try:
        prompt, _, _, meta = asyncio.run(main.translate(
            "站在海边", lora_selections=[{"key": "denia", "mode": "auto"}], include_meta=True
        ))
        assert calls and "ACTIVE LORA CONTEXT" in calls[0], calls
        assert "USER-LOCKED CHARACTER APPEARANCE OVERRIDES: []" in calls[0], calls[0]
        assert "Select exactly one profile ID" in calls[0], calls[0]
        assert "denia \\(wuthering waves\\)" in prompt, prompt
        assert "beach" in prompt and "sunset" in prompt, prompt
        assert meta["lora_aware"] is True
        assert meta["lora_bindings"][0]["profile"] == "white"
    finally:
        main.siliconflow_translate = old_translate
        main._TRANSLATE_CACHE = old_cache


def test_active_lora_is_present_in_vision_path():
    old_vision = main.siliconflow_vision_translate
    calls = []

    async def fake_vision(image_b64, context, reroll=False, mode="reference"):
        calls.append((context, reroll, mode))
        return (
            "1girl, beach, sunset", {"scene": "beach", "lighting": "sunset"}, "", None,
            {"denia": {"profile": "white", "optional": []}},
        )

    main.siliconflow_vision_translate = fake_vision
    try:
        prompt, _, _, meta = asyncio.run(main.translate(
            "站在海边", image_b64="mock-image",
            lora_selections=[{"key": "denia", "mode": "auto"}], include_meta=True,
        ))
        assert calls and "ACTIVE LORA CONTEXT" in calls[0][0], calls
        assert "denia \\(wuthering waves\\)" in prompt, prompt
        assert meta["lora_bindings"][0]["profile"] == "white"
    finally:
        main.siliconflow_vision_translate = old_vision


def test_lora_cache_isolated_by_profile():
    old_translate = main.siliconflow_translate
    old_cache = dict(main._TRANSLATE_CACHE)
    calls = 0

    async def fake_translate(context, reroll=False):
        nonlocal calls
        calls += 1
        profile = "black" if "Locked profile: black" in context else "white"
        out = (
            'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["street"],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
            f'LORA: {{"denia":{{"profile":"{profile}","optional":[]}}}}\n'
            'PROMPT: 1girl, solo, street\n'
        )
        return main._parse_structured_output(out) + ([], main._parse_lora_choices(out))

    main.siliconflow_translate = fake_translate
    main._TRANSLATE_CACHE = {}
    try:
        white = asyncio.run(main.translate("街道", lora_selections=["denia_white"]))[0]
        black = asyncio.run(main.translate("街道", lora_selections=["denia_black"]))[0]
        assert calls == 2, calls
        assert "denia \\(wuthering waves\\)" in white
        assert "blackdenia \\(wuthering waves\\)" in black
    finally:
        main.siliconflow_translate = old_translate
        main._TRANSLATE_CACHE = old_cache


def test_build_prompt_re_resolves_binding_and_deduplicates_trigger():
    bindings, _, revision = main.resolve_lora_selections(["denia_white"])
    payload = main.build_prompt(
        "anima", "1girl, solo, denia \\(wuthering waves\\), beach", 832, 1216,
        lora_bindings=bindings, registry_revision=revision,
    )
    prompt = payload["prompt"][str(main.WORKFLOWS["anima"]["prompt_node"])]["inputs"]["text"]
    loras = payload["prompt"][str(main.WORKFLOWS["anima"]["lora_node"])]["inputs"]["loras"]["__value__"]
    assert prompt.count("denia \\(wuthering waves\\)") == 1, prompt
    assert loras[0]["name"] == "denia_lorav4-000005.safetensors", loras


def test_enqueue_rebuilds_client_binding_from_registry_snapshot():
    old_jobs, old_queue, old_usage = main.JOBS, main.QUEUE, main.USAGE
    _, revision = main.LORA_REGISTRY.snapshot()
    try:
        main.JOBS = {}
        main.QUEUE = asyncio.Queue()
        main.USAGE = {"test-token": ["2099-01-01", 0]}
        job_id = asyncio.run(main._enqueue(
            "test-token", "anima", "1girl, beach", "测试", "832x1216",
            [], None, None,
            lora_bindings=[{
                "key": "denia", "profile": "white", "optional": [],
                "file": "evil.safetensors", "injected_tags": ["evil trigger"],
            }],
            registry_revision=revision,
            concept="用户锁定：少女｜模型补全：海边构图",
            completion_level="free",
        ))
        job = main.JOBS[job_id]
        assert "evil trigger" not in job["prompt_en"], job
        assert "denia \\(wuthering waves\\)" in job["prompt_en"], job
        assert job["lora_bindings"][0]["file"] == "denia_lorav4-000005.safetensors"
        assert job["concept"] == "用户锁定：少女｜模型补全：海边构图"
        assert job["completion_level"] == "free"
        assert asyncio.run(main.QUEUE.get()) == job_id
    finally:
        main.JOBS, main.QUEUE, main.USAGE = old_jobs, old_queue, old_usage


def test_dialog_start_carries_binding_snapshot_into_job():
    old_translate = main.translate
    old_jobs, old_sessions = main.JOBS, main.SESSIONS
    old_queue, old_usage = main.QUEUE, main.USAGE
    bindings, _, revision = main.resolve_lora_selections(["denia_white"])

    class FakeRequest:
        async def json(self):
            return {
                "action": "start", "prompt": "达妮娅在海边", "workflow": "anima",
                "size": "832x1216", "lora_selections": ["denia_white"],
                "completion_level": "free",
            }

    async def fake_translate(*args, **kwargs):
        meta = {
            "lora_bindings": bindings, "lora_warnings": [],
            "registry_revision": revision,
        }
        return "1girl, denia \\(wuthering waves\\), beach", None, None, meta

    try:
        main.translate = fake_translate
        main.JOBS = {}
        main.SESSIONS = {}
        main.QUEUE = asyncio.Queue()
        main.USAGE = {"dialog-token": ["2099-01-01", 0]}
        response = asyncio.run(main.dialog_turn(FakeRequest(), token="dialog-token"))
        session = main.SESSIONS[response["session_id"]]
        job = main.JOBS[response["job_id"]]
        assert session["registry_revision"] == revision
        assert session["lora_bindings"] == bindings
        assert session["completion_level"] == "free"
        assert job["registry_revision"] == revision
        assert job["lora_bindings"][0]["profile"] == "white"
        assert job["completion_level"] == "free"
    finally:
        main.translate = old_translate
        main.JOBS, main.SESSIONS = old_jobs, old_sessions
        main.QUEUE, main.USAGE = old_queue, old_usage


def main_test():
    tests = [
        test_character_match,
        test_bare_character_fast_path,
        test_ir_protocol_and_breakdown_derivation,
        test_legacy_protocol_fallback,
        test_tag_order_and_deduplication,
        test_character_bare_name_is_removed,
        test_compile_prompt_merges_tags_and_nl,
        test_experiment_negative_override_is_opt_in,
        test_build_prompt_routes_txt2img_and_img2img_explicitly,
        test_build_prompt_keeps_rating_tags_manual,
        test_render_profile_inference,
        test_tag_first_profile_drops_nl,
        test_prompt_ir_meta_is_additive,
        test_reroll_uses_new_painter_plan_metadata,
        test_painter_prompt_protocol_is_final_prompt,
        test_visual_composer_content_fidelity_contract,
        test_visual_composer_protocol_is_strict_and_collapses_whole_repeat,
        test_visual_composer_rejects_unrenderable_model_additions,
        test_composer_guard_only_enforces_count_and_explicit_full_body_lock,
        test_workflow_negative_contains_compact_anatomy_guard,
        test_siliconflow_composer_bypasses_ordinary_dict_and_isolates_completion_cache,
        test_concept_override_is_authoritative_and_validated,
        test_painter_tag_guard_preserves_woman_and_suppresses_unrequested_silhouette,
        test_painter_tag_guard_keeps_nsfw_body_framing_and_default_anime_style,
        test_painter_tag_guard_preserves_explicit_user_intent,
        test_character_hint_protocol_parser,
        test_character_hint_ir_fallback,
        test_character_bare_name_space_variant_is_removed,
        test_danbooru_character_classification,
        test_auto_character_cache_write_and_match,
        test_unavailable_lookup_is_retryable,
        test_unknown_character_fallback_on_unavailable,
        test_lora_registry_preserves_nested_profiles,
        test_bright_afternoon_uses_daylight_not_golden_hour,
        test_hot_lora_registry_keeps_last_good_snapshot,
        test_lora_legacy_key_resolves_explicit_profile,
        test_lora_auto_profile_and_optional_ids_are_whitelisted,
        test_lora_explicit_profile_still_accepts_semantic_optional_choice,
        test_lora_intent_alias_locks_profile_and_optional_ids,
        test_deepseek_view_recipes_are_conditional,
        test_lora_registry_revision_rejects_stale_binding,
        test_lora_binding_compiler_is_exact_and_idempotent,
        test_lora_binding_compiler_removes_sibling_profile_trigger,
        test_character_lora_blocks_profile_name_appearance_inference,
        test_character_lora_appearance_conflict_repairs_once,
        test_lora_choice_protocol_parser,
        test_active_lora_forces_painter_and_compiles_binding,
        test_active_lora_is_present_in_vision_path,
        test_lora_cache_isolated_by_profile,
        test_build_prompt_re_resolves_binding_and_deduplicates_trigger,
        test_enqueue_rebuilds_client_binding_from_registry_snapshot,
        test_dialog_start_carries_binding_snapshot_into_job,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} prompt unit tests passed")


if __name__ == "__main__":
    main_test()
