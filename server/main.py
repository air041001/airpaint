# -*- coding: utf-8 -*-
"""ComfyUI Web MVP 后端网关
职责: 鉴权 / 限流 / 排队(并发=1) / 中文->tag 翻译 / 内容过滤 / 调用 ComfyUI API / 返回图片
"""
import asyncio
import json
import random
import re
import time
import uuid
from pathlib import Path

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
DICT_PATH = BASE / "dict.yaml"
CHAR_DICT_PATH = BASE / "char_dict.yaml"


class HotDict:
    """YAML 词典 + mtime 热更新: 文件存盘后下次访问自动重载, 不用重启后端 (见 D21).
    用法等同 dict (.get / .items). 每次访问 stat 一下 mtime, 没变就跳过 (微秒级).
    解析失败 (如存盘写到一半 / YAML 笔误) 保留旧词典并打印警告, 不阻断翻译."""
    def __init__(self, path: Path, key_fn=str.lower):
        self.path = path
        self.key_fn = key_fn
        self._mtime = 0.0
        self._d: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            return
        try:
            m = self.path.stat().st_mtime
            if m == self._mtime:
                return
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            # 整体重建再整体赋值: 读端要么见旧要么见新, 不会读到半成品
            self._d = {self.key_fn(str(k).strip()): str(v).strip() for k, v in raw.items() if v is not None}
            self._mtime = m
        except Exception as e:
            print(f"[HotDict] 重载 {self.path.name} 失败, 保留旧词典: {e}", flush=True)

    def get(self, key):
        self.reload()
        return self._d.get(key)

    def items(self):
        self.reload()
        return self._d.items()


# 属性/感觉词典: 中文 -> danbooru tag. key 小写匹配.
DICT = HotDict(DICT_PATH, key_fn=str.lower)
# 角色词典: 中文名 -> danbooru 精确 tag. Qwen3-8B 认不准角色 tag (字面翻译/编造/漏认),
# 故角色走词典可靠命中, 命中后把 tag 作为上下文喂给 LLM (见 decisions.md D12). key 不小写 (中文无大小写).
CHAR_DICT = HotDict(CHAR_DICT_PATH, key_fn=lambda s: s)

COMFY = CFG["comfy_url"].rstrip("/")
TOKENS = set(CFG.get("tokens", []))
DAILY_LIMIT = int(CFG.get("daily_limit", 30))
BANNED = [w.lower() for w in CFG.get("banned_words", [])]
WORKFLOWS = CFG.get("workflows", {})

IMAGES = BASE / "images"
IMAGES.mkdir(exist_ok=True)

app = FastAPI(title="ComfyUI Web MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.get("allow_origins", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/images", StaticFiles(directory=IMAGES), name="images")

# 静态托管前端网页: 访问 https://airpaint.xyz/ 直接返回 index.html
# 这样前后端共用一个域名, 告别 GitHub Pages
WEB_DIR = BASE.parent / "web"

@app.get("/", include_in_schema=False)
async def index():
    # no-cache: 前端改了 (如修 fail-to-fetch) 后, 访客浏览器永远拿最新 HTML, 不被旧缓存卡住
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

JOBS: dict[str, dict] = {}          # job_id -> job
QUEUE: asyncio.Queue[str] = asyncio.Queue()
USAGE: dict[str, list] = {}         # token -> [date, count]
CLIENT = httpx.AsyncClient(timeout=60)
CLIENT_ID = uuid.uuid4().hex


# ---------- 鉴权 & 限流 ----------
def auth(req: Request) -> str:
    token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not TOKENS or token not in TOKENS:
        raise HTTPException(401, "无效 token")
    today = time.strftime("%Y-%m-%d")
    rec = USAGE.setdefault(token, [today, 0])
    if rec[0] != today:
        rec[0], rec[1] = today, 0
    if rec[1] >= DAILY_LIMIT:
        raise HTTPException(429, f"今日已达 {DAILY_LIMIT} 张上限")
    return token


def verify_token(req: Request) -> str:
    """仅校验 token (不查日限), 给非出图接口用 (如 /api/translate) -- 翻译不占 GPU, 不该被 image 限额挡."""
    token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not TOKENS or token not in TOKENS:
        raise HTTPException(401, "无效 token")
    return token


# ---------- 内容过滤 ----------
def check_banned(text: str):
    low = text.lower()
    for w in BANNED:
        if w in low:
            raise HTTPException(400, f"提示词包含被禁止的内容: {w}")


# ---------- 中文 -> tag 翻译 ----------
# 简单的进程内 LRU 缓存, 相同中文提示直接返回上次结果, 省 API 调用
_TRANSLATE_CACHE: dict[str, str] = {}
_TRANSLATE_CACHE_MAX = 500

SILICONFLOW_SYSTEM_PROMPT = (
    "You are a prompt engineer for the Anima anime image model. "
    "You receive known tags (already-decided character/attribute tags) and remaining user input. "
    "Decompose the intent of ONLY the remaining input, then emit tags. Do NOT repeat or rephrase known tags.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <concrete place + setting tags>\n"
    "composition: <framing / camera angle / orientation tags>\n"
    "mood: <emotion -> atmosphere tags>\n"
    "lighting: <light tags>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. METAPHORS / FEELINGS (未来的方向, 青春结束, 治愈, 春天的感觉) go in mood -> atmosphere+scene tags. "
    "NEVER translate them as literal nouns (not 'future', not 'youth').\n"
    "2. SPATIAL RELATIONS (看向窗外, 背对, 望向, 仰视) go in composition with concrete framing tags "
    "(e.g. facing window, looking out window, from behind, back to viewer, from below).\n"
    "3. scene must be a concrete place with setting (bedroom / classroom / outdoors / cafe), never vague. Pick one and commit.\n"
    "4. Subject: if a person is implied, put a count tag (1girl/1boy) FIRST in TAGS. "
    "If the remaining is PURELY scenery with no person, focus on scenery, do NOT force a character.\n"
    "5. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
    "6. Use lowercase danbooru tags; spaces preferred over underscores. Do NOT combine mutually exclusive styles. "
    "Do NOT add realistic/photorealistic/3d/render tags - the target model is anime-only.\n"
    "7. TAGS collects every concrete tag implied by the 5 fields above. Keep under ~200 chars.\n\n"
    "Examples:\n"
    "Known character tags: march_7th_(honkai:_star_rail)\n"
    "Remaining: 在樱花树下\n"
    "scene: outdoors, under cherry blossom tree\n"
    "composition: standing, full body, looking at viewer\n"
    "mood: cheerful, serene\n"
    "lighting: soft daylight, petals falling\n"
    "style: anime style\n"
    "TAGS: 1girl, solo, cherry blossoms, tree, petals, spring, smile, standing, full body, looking at viewer, outdoors, soft daylight, anime style\n\n"
    "Remaining: 穿着学生服的少女坐在房间书桌上 看向窗外 那是未来的方向\n"
    "scene: bedroom, desk by window, afternoon\n"
    "composition: sitting at desk, facing window, looking out window, from behind, side view\n"
    "mood: wistful, longing, hopeful for the future\n"
    "lighting: soft daylight from window\n"
    "style: anime style, clean lines\n"
    "TAGS: 1girl, school uniform, sitting, desk, bedroom, window, looking out window, facing window, from behind, side view, soft daylight, anime style, clean lines\n\n"
    "Remaining: 想要春天的感觉\n"
    "scene: garden, spring, outdoors\n"
    "composition: scenic, wide shot\n"
    "mood: peaceful, gentle, renewal\n"
    "lighting: warm sunlight\n"
    "style: anime style, pastel colors\n"
    "TAGS: spring, cherry blossoms, petals falling, gentle breeze, warm sunlight, pastel colors, peaceful, garden, outdoors, anime style"
)


# LLM 结构化输出的字段 (顺序即展示顺序). TAGS 行单独解析为最终 tag.
_STRUCTURED_FIELDS = ("scene", "composition", "mood", "lighting", "style")

# ③ 参考图理解: 视觉 LLM 从参考图提取氛围/配色/构图/场景 -> 同样的结构化输出格式 (复用 _parse_structured_output).
# 图是"氛围参考"(非图生图): 提取 mood/color/lighting/composition, 不照搬图的主体 (除非文本指定). 见 D23.
VISION_SYSTEM_PROMPT = (
    "You are a prompt engineer for the Anima anime image model. You receive a REFERENCE IMAGE (a VIBE reference: "
    "mood/color/lighting/composition/setting), plus known tags and remaining user text. Extract ONLY the image's VIBE, "
    "combine with the text's subject, then emit danbooru tags. Do NOT repeat or rephrase known tags.\n\n"
    "Output EXACTLY these lines, nothing else (no markdown, no quotes, no extra text):\n"
    "scene: <the PLACE/setting tags from the image, e.g. beach, bedroom, outdoors, cafe>\n"
    "composition: <framing / camera angle / orientation tags, from the image>\n"
    "mood: <emotion -> atmosphere tags, from the image>\n"
    "lighting: <light tags, from the image>\n"
    "style: <art style tags>\n"
    "TAGS: <final danbooru tags, lowercase, comma-separated>\n\n"
    "Rules:\n"
    "1. VIBE ONLY from the image: mood, color palette, lighting, composition, and the PLACE/setting (beach, bedroom, etc.).\n"
    "2. Do NOT copy the image's SUBJECT APPEARANCE -- no clothing, hair color, eye color, accessories, body type, pose, "
    "or character identity from the image. The subject comes from the remaining TEXT, not the image. "
    "Example: if the image shows a girl in a red swimsuit with cat ears at a beach, and the text only says '少女', "
    "output '1girl' + the beach/lighting/mood vibe -- NOT 'cat ears', 'swimsuit', or 'red hair'.\n"
    "3. If remaining text gives a subject (a character, 1girl/1boy, an action), use THAT subject; let the image fill ONLY the vibe.\n"
    "4. If remaining is empty, output only the vibe + a generic count tag (1girl/1boy) if a person fits; "
    "do NOT replicate the image's specific character design.\n"
    "5. Do NOT include any character/series tags already listed in Known character tags.\n"
    "6. Put a count tag (1girl/1boy/solo) FIRST in TAGS if a person is implied.\n"
    "7. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
    "8. Use lowercase danbooru tags; spaces preferred over underscores. Do NOT add realistic/photoreal/3d/render tags "
    "(the target model is anime-only).\n"
    "9. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
)


def _parse_structured_output(out: str) -> tuple[str, dict | None]:
    """解析 LLM 结构化输出 (scene/composition/mood/lighting/style 各一行 + TAGS 行).
    返回 (tags, breakdown). 无 TAGS 行 -> 整体当 tags, breakdown=None (降级到旧扁平行为, 见 D18)."""
    breakdown: dict = {}
    tags = ""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("tags:"):
            tags = line.split(":", 1)[1].strip()
            continue
        for f in _STRUCTURED_FIELDS:
            if low.startswith(f + ":"):
                breakdown[f] = line.split(":", 1)[1].strip()
                break
    if not tags:
        return out, None
    return tags, breakdown or None


def match_characters(text: str) -> tuple[list[str], str]:
    """子串匹配角色名. 返回 (角色 tag 列表, 移除角色名后的剩余文本)."""
    found_tags: list[str] = []
    remaining = text
    for name, tag in CHAR_DICT.items():
        if name in text:
            found_tags.append(tag)
            remaining = remaining.replace(name, "")
    return found_tags, remaining


async def siliconflow_translate(context: str, reroll: bool = False) -> tuple[str, dict | None]:
    """走硅基流动 Qwen 翻译/扩写. context 是结构化上下文 (Known tags + Remaining).
    返回 (LLM 新增 tag, 结构化拆解 dict). tag 不含已知 tag (由 translate 拼接).
    breakdown 供前端预览展示 AI 理解 (scene/composition/mood/lighting/style). 失败抛异常 (上层转 HTTPException).
    reroll=True: 提高温度 + 前置发散指令, 让模型给一版不同创意解读 (抽卡再抽, 见 D19)."""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "Qwen/Qwen3-8B")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")

    # thinking 默认关 (D2: 思考慢 30s+ 且易复读); 结构化字段已是强制表态机制, 不依赖 CoT.
    # 隐喻/场景仍弱时 config 翻 translate_enable_thinking: true 重测, 不动代码 (见 D18).
    thinking = bool(CFG.get("translate_enable_thinking", False))

    # reroll: 高温 + 发散指令. /no_think 仍是 user 首token (thinking 开则不前置), nudge 跟在后面.
    temperature = float(CFG.get("reroll_temperature", 0.9)) if reroll else 0.4
    nudge = ("Give a DIFFERENT, more creative interpretation than the obvious one. "
             "Vary the scene, mood and lighting; pick an unexpected but coherent setting. "
             "Still follow the output format and the known-tags rule.\n\n") if reroll else ""
    user_content = ("/no_think " if not thinking else "") + nudge + context

    r = await CLIENT.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SILICONFLOW_SYSTEM_PROMPT},
                # /no_think: Qwen3 软开关, 强制不进思考模式 (思考会慢到 30s+ 且易复读). thinking 开则不前置.
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": 400,
            # ★ 关键: enable_thinking 必须放顶层, 放 extra_body 里硅基流动不认 -> 思考没关掉. (见 D2)
            "enable_thinking": thinking,
        },
        timeout=40,
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

    # 解析结构化输出: 5 字段 + TAGS 行. 无 TAGS 行则整体当 tag (降级, 见 D18).
    tags, breakdown = _parse_structured_output(out)

    # 兜底: 检测重复 tag (模型复读), 出现3次以上相同 tag 说明输出异常
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"翻译输出异常(重复tag: {dupes[0]}), 请重试")

    return tags, breakdown


async def siliconflow_vision_translate(image_b64: str, context: str, reroll: bool = False) -> tuple[str, dict | None]:
    """③ 参考图理解: 走硅基流动 Qwen3-VL, 从参考图提取氛围/配色/构图/场景/光影 -> 结构化 breakdown + TAGS.
    image_b64: data URI (data:image/...;base64,...) 或纯 base64. context 同文本 LLM (Known tags + Remaining).
    返回 (tags, breakdown), 复用 _parse_structured_output. 失败抛异常 (上层转 HTTPException). 见 D23.
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
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                # VL 模型 user content 是数组: 文本 + image_url (OpenAI 视觉格式, 硅基流动兼容, 见 D23)
                {"role": "user", "content": [
                    {"type": "text", "text": nudge + context},
                    {"type": "image_url", "image_url": {"url": image_b64, "detail": "high"}},
                ]},
            ],
            "temperature": temperature,
            "max_tokens": 400,
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

    tags, breakdown = _parse_structured_output(out)
    # 重复 tag 兜底 (同文本 LLM)
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"视觉输出异常(重复tag: {dupes[0]}), 请重试")
    return tags, breakdown


# Anima 期望的 tag 顺序: quality -> count -> character -> general (见 D20).
# quality 由 build_prompt 的 quality_prefix 在更外层 prepend, 这里只规范 prompt_en 内部:
# 把 count (1girl/1boy/solo/...) 从 LLM 输出里提到最前, character 次之, general 垫后. 只重排不增删不去重.
_COUNT_TAG_RE = re.compile(
    r"^(solo|solo focus|"
    r"\d+(girl|boy|other)s?|"      # 1girl, 2girls, 1boy, 1other
    r"\d+\+(girls|boys|others)|"   # 6+girls
    r"multiple (girls|boys|others))$"
)


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


async def translate(text: str, reroll: bool = False, image_b64: str | None = None) -> tuple[str, dict | None]:
    """中文 -> danbooru tag. 三层: 角色匹配 -> 词典匹配 -> LLM 扩写(只处理未命中).
    返回 (prompt_en, breakdown): breakdown 是 LLM 结构化拆解, 快速路径(全命中词典/角色)时为 None.
    reroll=True: 只对 LLM 路径生效, 高温重出一版不同分解, 跳过缓存(探索性, 不污染正常缓存).
    image_b64: ③ 参考图 (data URI 或 base64). 有图走视觉 LLM 提氛围, 不走文本 LLM/快速路径. 见 D23."""
    backend = CFG.get("translate", "none")

    # Layer 0: 角色子串匹配 (移除角色名, 得到剩余文本)
    char_tags, remaining = match_characters(text)

    # Layer 1: 剩余文本按逗号切, 逐段查属性/感觉词典
    parts = [p.strip() for p in re.split(r"[,，、;；\n]+", remaining) if p.strip()]
    hits, misses = [], []
    for p in parts:
        h = DICT.get(p.lower())
        if h is None:
            misses.append(p)
        else:
            # dict 值可能是多 tag ("少女" -> "girl, young, cute, innocent"), 按逗号拆开
            # 否则整个 blob 当一个 tag, 跟 LLM/VL 输出的同名 tag 撞出"伪重复"(normalize 按整元素去重逮不到, 见 D23)
            hits.extend(t.strip() for t in str(h).split(",") if t.strip())

    # ③ 参考图: 有图走视觉 LLM 提取氛围 (图 + 文本上下文), 不走下面的文本 LLM/快速路径.
    # 图是氛围参考, 文本(若有)给主体; 角色词典仍预匹配(可靠). 不缓存(图探索性, key 含图复杂). 见 D23.
    if image_b64:
        ctx_lines = []
        if char_tags:
            ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
        if hits:
            ctx_lines.append(f"Known attribute tags: {', '.join(hits)}")
        ctx_lines.append(f"Remaining: {', '.join(misses) if misses else '(none - extract everything from the image)'}")
        context = "\n".join(ctx_lines)
        try:
            new_tags, breakdown = await siliconflow_vision_translate(image_b64, context, reroll=reroll)
        except Exception as e:
            raise HTTPException(502, f"参考图理解失败, 请稍后重试 ({e})")
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        return normalize_tag_order(char_tags, hits + new_list), breakdown

    # 全命中 (无 misses): 不调 LLM
    if not misses:
        # 裸角色名快速路径: 只有角色没别的描述 -> 补 1girl, solo
        # (LLM 对裸角色名会疯狂编场景/武器, 实测 7.9s + 噪声 tag, 见 D13)
        if char_tags and not hits and not parts:
            return normalize_tag_order(char_tags, ["1girl", "solo"]), None
        all_tags = char_tags + hits
        if all_tags:
            return normalize_tag_order(char_tags, hits), None
        raise HTTPException(400, "提示词为空")

    # Layer 2: 有未命中 -> 后端处理
    if backend == "none":
        # 未翻译部分原样保留 (混输英文 tag 时合适)
        return normalize_tag_order(char_tags, hits + misses), None

    if backend == "siliconflow":
        # 构造上下文: 已知 tag 喂给 LLM, 只让它翻/扩 misses (不重复已知 tag)
        ctx_lines = []
        if char_tags:
            ctx_lines.append(f"Known character tags: {', '.join(char_tags)}")
        if hits:
            ctx_lines.append(f"Known attribute tags: {', '.join(hits)}")
        ctx_lines.append(f"Remaining: {', '.join(misses)}")
        context = "\n".join(ctx_lines)

        cache_key = context
        if not reroll and cache_key in _TRANSLATE_CACHE:
            return _TRANSLATE_CACHE[cache_key]
        try:
            new_tags, breakdown = await siliconflow_translate(context, reroll=reroll)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        # 拼接: 已知 tag + LLM 新增 tag, 再按 Anima 规范序排 (count -> char -> general, 见 D20)
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        result = normalize_tag_order(char_tags, hits + new_list)
        # reroll 不写缓存: 探索性结果不应顶掉正常翻译的缓存原版 (见 D19)
        if not reroll:
            if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
                _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
            _TRANSLATE_CACHE[cache_key] = (result, breakdown)
        return result, breakdown

    if backend == "google":
        try:
            translated_missing = await google_translate_batch(misses)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        return normalize_tag_order(char_tags, hits + translated_missing), None

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


# ---------- Workflow 注入 ----------
def sanitize_for_api(wf: dict) -> dict:
    """剔除/替换"只能从 ComfyUI 前端排队时才能跑"或"会干扰后端取图"的节点。
    依赖 extra_pnginfo['workflow'] (前端 UI 图), 后端 /prompt 不带, 会崩:
      - WidgetToString (KJNodes): 读 extra_pnginfo['workflow'] -> TypeError
      - Image Saver Metadata:     依赖 WidgetToString
      - Image Saver Simple:       依赖上面的 metadata, 且 embed_workflow 也要 extra_pnginfo
    把 Image Saver Simple 换成内置 SaveImage (API 可靠出图, outputs.images 标准格式, 后端能读)。
    另剔 Image Comparer (rgthree): 本身不崩(extra_pnginfo=None 时它只调 save_images 存盘), 但它是
      OUTPUT_NODE(继承 PreviewImage), 中间预览图会进 /history; submit_and_wait 取"第一个有图的节点",
      会误取 comparer 的中间图(手修版/原图)而非最终 SaveImage -> 必须剥。"""
    INCOMPAT = {"WidgetToString", "Image Saver Metadata", "Image Comparer (rgthree)"}
    new_id = 100
    for nid in list(wf.keys()):
        ct = wf[nid].get("class_type", "")
        if ct == "Image Saver Simple":
            images_src = wf[nid].get("inputs", {}).get("images")
            # 文件名带上日期时间, 接近原 Image Saver 的可读性 (避免 anima_00007_.png 这种看不出时间的)
            wf[str(new_id)] = {"class_type": "SaveImage",
                               "inputs": {"images": images_src,
                                          "filename_prefix": f"anima_{time.strftime('%Y%m%d')}"}}
            new_id += 1
            del wf[nid]
        elif ct in INCOMPAT:
            del wf[nid]
    return wf


def build_prompt(wf_name: str, prompt_en: str, width: int | None, height: int | None,
                 lora_key: str | None = None, strength: float | None = None) -> dict:
    wcfg = WORKFLOWS[wf_name]
    wf = json.loads((BASE / wcfg["file"]).read_text(encoding="utf-8"))
    wf = sanitize_for_api(wf)
    seed = random.randint(1, 2**31 - 1)

    # 统一 seed: 把工作流里所有 int 型 seed/noise_seed 输入都写成正整数。
    # 为什么必须做:
    #   ComfyUI 前端排队时用 seed=-1 表示"随机", Impact Pack / rgthree 的 onprompt 钩子
    #   负责在执行前把 -1 替换成真随机数。我们走 /prompt API 时那些钩子拿到的 JSON 是
    #   我们拼的——只要工作流里还残留 -1 (典型: FaceDetailer/SEGS 内部 seed), Impact Pack
    #   的 np.random.default_rng(-1) 直接抛 ValueError, 整个 Impact Pack 异常退出,
    #   FaceDetailer 人脸修复/wildcards 全部失效 (出图"很原生"的直接原因)。
    # 列表值 (如 ["6", 0] 的节点连接) 会被 isinstance(int) 跳过, 连接关系不动。
    SEED_FIELDS = ("seed", "noise_seed")
    for node in wf.values():
        inputs = node.get("inputs", {})
        for field in SEED_FIELDS:
            if field in inputs and isinstance(inputs[field], int):
                inputs[field] = seed

    def set_input(node_key: str, field: str, value):
        node = wf.get(str(wcfg[node_key]))
        if not node:
            raise HTTPException(500, f"workflow {wf_name} 配置错误: 节点 {wcfg[node_key]} 不存在")
        node["inputs"][field] = value

    # LoRA 注入: 写 LoraManager 节点的 loras widget. __value__ 内是对象数组, 每项 {name, strength,
    # clipStrength, active}; active 必须为 true, 否则 _collect_widget_entries 跳过 (见 D16).
    # 触发词手动拼进 prompt: LoraManager 自带的触发词链 (节点5 output2 -> 37 -> 46 -> 48 -> 54)
    # 已被下面 set_input("prompt_node","text",...) 覆盖节点54 text 而断掉, 故必须自己拼.
    trigger = ""
    if lora_key:
        loras = CFG.get("loras", {})
        if lora_key not in loras:
            raise HTTPException(400, f"未知的 LoRA: {lora_key}")
        if "lora_node" not in wcfg:
            raise HTTPException(400, f"工作流 {wf_name} 不支持 LoRA")
        lora = loras[lora_key]
        # strength: 前端传入则同时覆盖 model/clip (LoraManager 默认 clipStrength=strength); 否则用 config 默认
        sm = float(strength) if strength is not None else float(lora.get("strength_model", 1.0))
        sc = float(strength) if strength is not None else float(lora.get("strength_clip", sm))
        set_input("lora_node", "loras", {"__value__": [{
            "name": lora["file"],
            "strength": sm,
            "clipStrength": sc,
            "active": True,
        }]})
        trigger = (lora.get("trigger") or "").strip()

    full_prompt = wcfg.get("quality_prefix", "") + (trigger + ", " if trigger else "") + prompt_en
    set_input("prompt_node", "text", full_prompt)
    if "negative_node" in wcfg:
        set_input("negative_node", "text", wcfg.get("negative_prefix", "") + wcfg.get("negative_extra", ""))
    if "seed_node" in wcfg:
        set_input("seed_node", "seed", seed)
    if width and height and "size_node" in wcfg:
        set_input("size_node", "width", width)
        set_input("size_node", "height", height)
    return {"prompt": wf, "client_id": CLIENT_ID, "_seed": seed}


async def submit_and_wait(wf_name: str, prompt_en: str, width, height, lora_key: str | None = None,
                          strength: float | None = None) -> str:
    payload = build_prompt(wf_name, prompt_en, width, height, lora_key, strength)
    seed = payload.pop("_seed")
    r = await CLIENT.post(f"{COMFY}/prompt", json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI 拒绝: {r.text[:200]}")
    pid = r.json()["prompt_id"]

    deadline = time.time() + int(CFG.get("timeout_seconds", 300))
    while time.time() < deadline:
        await asyncio.sleep(2)
        h = (await CLIENT.get(f"{COMFY}/history/{pid}")).json()
        if pid not in h:
            continue
        entry = h[pid]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError("ComfyUI 执行出错")
        if not status.get("completed", False) and "outputs" not in entry:
            continue
        for node_out in entry.get("outputs", {}).values():
            for img in node_out.get("images", []):
                if img.get("type") in ("output", "temp"):
                    data = (await CLIENT.get(f"{COMFY}/view", params={
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })).content
                    fname = f"{uuid.uuid4().hex[:12]}.png"
                    (IMAGES / fname).write_bytes(data)
                    return fname
        raise RuntimeError("未找到输出图片")
    raise TimeoutError("生成超时")


# ---------- 队列 worker ----------
async def worker():
    while True:
        job_id = await QUEUE.get()
        job = JOBS[job_id]
        job["status"] = "running"
        try:
            fname = await submit_and_wait(job["workflow"], job["prompt_en"], job.get("width"), job.get("height"), job.get("lora"), job.get("strength"))
            job.update(status="done", image=f"/images/{fname}")
        except Exception as e:
            job.update(status="failed", error=str(e))
        finally:
            QUEUE.task_done()


@app.on_event("startup")
async def _startup():
    asyncio.create_task(worker())


# ---------- API ----------
@app.get("/api/health")
async def health():
    try:
        r = await CLIENT.get(f"{COMFY}/system_stats", timeout=5)
        comfy_ok = r.status_code == 200
    except Exception:
        comfy_ok = False
    return {"ok": True, "comfy": comfy_ok}


@app.get("/api/auth/check")
async def auth_check(token: str = Depends(verify_token)):
    """只验证邀请码有效性 (verify_token: 不查日限, 不耗 GPU 配额). 给前端登录门禁用, 避免用 /api/workflows
    (auth, 查日限) 验证导致达日限的朋友登不进来。"""
    return {"ok": True}


@app.get("/api/workflows")
async def list_workflows(token: str = Depends(auth)):
    return [
        {"name": k, "label": v.get("label", k), "sizes": v.get("sizes")}
        for k, v in WORKFLOWS.items()
    ]


@app.get("/api/loras")
async def list_loras(token: str = Depends(auth)):
    """可用 LoRA 列表 (只暴露展示字段, 不含 file/trigger 等内部字段)."""
    return [
        {
            "key": k,
            "name": v.get("name", k),
            "description": v.get("description", ""),
            "preview": v.get("preview"),
        }
        for k, v in CFG.get("loras", {}).items()
    ]


@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只翻译不排队: 中文 -> 英文 tag (角色->词典->LLM 三层 + 结构化扩写, LRU 缓存). 不计入 image 限额.
    返回 {prompt_en, breakdown}: breakdown 是 LLM 结构化拆解 (scene/composition/mood/lighting/style),
    供前端预览展示 AI 理解; 快速路径(全命中词典/角色)时为 null.
    body.reroll=true: LLM 高温重出一版不同分解 (抽卡再抽, 跳过缓存, 见 D19).
    body.image: 可选, 参考图 base64 (data URI). 有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23)."""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    image = (body.get("image") or "").strip()
    if not prompt and not image:
        raise HTTPException(400, "提示词和参考图不能同时为空")
    if len(prompt) > 500:
        raise HTTPException(400, "提示词过长(>500)")
    if len(image) > 5_000_000:
        raise HTTPException(400, "参考图过大(>5MB)")
    if prompt:
        check_banned(prompt)
    reroll = bool(body.get("reroll"))
    prompt_en, breakdown = await translate(prompt, reroll=reroll, image_b64=(image or None))
    check_banned(prompt_en)
    return {"prompt_en": prompt_en, "breakdown": breakdown}


@app.post("/api/jobs")
async def create_job(req: Request, token: str = Depends(auth)):
    body = await req.json()
    wf_name = body.get("workflow", "")
    prompt_en = (body.get("prompt_en") or "").strip()
    prompt_raw = (body.get("prompt") or "").strip() or prompt_en   # 原始中文, 仅存档展示; 不传则同 prompt_en
    lora = (body.get("lora") or "").strip()
    if wf_name not in WORKFLOWS:
        raise HTTPException(400, "未知工作流")
    if not prompt_en or len(prompt_en) > 800:
        raise HTTPException(400, "提示词为空或过长(>800)")
    if prompt_raw != prompt_en and len(prompt_raw) > 500:
        raise HTTPException(400, "原始提示词过长(>500)")
    check_banned(prompt_en)
    if prompt_raw != prompt_en:
        check_banned(prompt_raw)

    wcfg = WORKFLOWS[wf_name]
    width = height = None
    if wcfg.get("sizes"):
        size = body.get("size") or wcfg["sizes"][0]
        if size not in wcfg["sizes"]:
            raise HTTPException(400, "非法尺寸")
        width, height = map(int, size.split("x"))

    # LoRA 校验 (失败快速返回 400, 不进队列)
    strength = None
    if lora:
        if lora not in CFG.get("loras", {}):
            raise HTTPException(400, "未知的 LoRA")
        if "lora_node" not in wcfg:
            raise HTTPException(400, "该工作流不支持 LoRA")
        strength = body.get("strength")
        if strength is not None:
            try:
                strength = float(strength)
            except (TypeError, ValueError):
                raise HTTPException(400, "LoRA 强度需为数字")
            if not (0 <= strength <= 1):
                raise HTTPException(400, "LoRA 强度需在 0~1 之间")

    USAGE[token][1] += 1
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "id": job_id, "token": token, "workflow": wf_name,
        "prompt_raw": prompt_raw, "prompt_en": prompt_en,
        "width": width, "height": height, "lora": lora or None, "strength": strength,
        "status": "queued", "created": time.time(),
    }
    await QUEUE.put(job_id)
    return {"id": job_id, "prompt_en": prompt_en}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, token: str = Depends(auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    queued_ids = list(QUEUE._queue)  # MVP 简单读取
    resp = {k: job[k] for k in ("id", "status", "prompt_raw", "prompt_en", "workflow") if k in job}
    if job["status"] == "queued":
        resp["position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 1
    if job["status"] == "done":
        resp["image"] = job["image"]
    if job["status"] == "failed":
        resp["error"] = job.get("error", "未知错误")
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG.get("host", "127.0.0.1"), port=int(CFG.get("port", 8000)))
