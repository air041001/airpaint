# -*- coding: utf-8 -*-
"""ComfyUI Web MVP 后端网关
职责: 鉴权 / 限流 / 排队(并发=1) / 中文->tag 翻译 / 内容过滤 / 调用 ComfyUI API / 返回图片
"""
import asyncio
import base64
import hashlib
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
# 角色词典: 中文名 -> danbooru 精确 tag. LLM 认不准角色 tag (字面翻译/编造/漏认),
# 故角色走词典可靠命中, 命中后把 tag 作为上下文喂给 LLM (见 decisions.md D12). key 不小写 (中文无大小写).
CHAR_DICT = HotDict(CHAR_DICT_PATH, key_fn=lambda s: s)

COMFY = CFG["comfy_url"].rstrip("/")
TOKENS = set(CFG.get("tokens", []))
DAILY_LIMIT = int(CFG.get("daily_limit", 30))
BANNED = [w.lower() for w in CFG.get("banned_words", [])]
WORKFLOWS = CFG.get("workflows", {})

# ---------- LoRA 注册表: config 手动 + 自动扫描 Civitai ----------
# 三层叠加 (优先级高->低): config.yaml 手动配置 > Civitai hash lookup 自动补全 > 裸文件名
# config 里有的条目: 用 config 的 trigger/type/name (人判断最准, 含服装变体)
# config 里没有的 .safetensors: 按 SHA256 查 Civitai 取 trainedWords/modelName/tags
#   有 trainedWords -> 自动可用; 没有 -> 标记 "未配置", 需手动加 config
LORA_DIR = Path(CFG.get("comfy_dir", ".")) / "models" / "loras"
LORA_CACHE_FILE = BASE / "lora_cache.json"
# {filename_stem: {sha256, trainedWords, modelName, tags, baseModel, type, fetchedAt}}
_lora_auto: dict[str, dict] = {}
_lora_auto_loaded = False


def _load_lora_cache():
    """从磁盘加载自动扫描缓存 (避免每次重启都请求 Civitai)."""
    global _lora_auto, _lora_auto_loaded
    if _lora_auto_loaded:
        return
    try:
        if LORA_CACHE_FILE.exists():
            _lora_auto = json.loads(LORA_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[LoRA] 缓存文件读取失败: {e}", flush=True)
    _lora_auto_loaded = True


def _save_lora_cache():
    """持久化自动扫描缓存到磁盘."""
    try:
        LORA_CACHE_FILE.write_text(json.dumps(_lora_auto, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[LoRA] 缓存文件写入失败: {e}", flush=True)


def _read_sha256(filepath: Path) -> str | None:
    """读 SHA256: 优先 LoraManager .metadata.json -> .sha256 文件 -> 现算 (大文件慢)."""
    stem = filepath.stem
    d = filepath.parent
    meta = d / f"{stem}.metadata.json"
    if meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            sha = (m.get("sha256") or "").strip().lower()
            if sha:
                return sha
        except Exception:
            pass
    sha_file = d / f"{stem}.sha256"
    if sha_file.exists():
        try:
            return sha_file.read_text(encoding="utf-8").strip().lower()
        except Exception:
            pass
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


async def _civitai_lookup(sha256: str) -> dict | None:
    """按 SHA256 查 Civitai API, 返回 {trainedWords, modelName, tags, baseModel, type} 或 None."""
    try:
        r = await CLIENT.get(
            f"https://civitai.com/api/v1/model-versions/by-hash/{sha256}", timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        model = d.get("model") or {}
        # Civitai model.tags 是 [{name, count}] 对象数组, 提取 name 列表再判类型
        # (修: 旧代码 "character" in tags 当字符串数组用, 永远 False -> 全 unknown)
        raw_tags = model.get("tags") or []
        tag_names = [t.get("name", "") if isinstance(t, dict) else str(t) for t in raw_tags]
        lora_type = "character" if "character" in tag_names else (
            "style" if any(t in tag_names for t in ("style", "artist", "artstyle")) else "unknown")
        return {
            "trainedWords": d.get("trainedWords") or [],
            "modelName": model.get("name", ""),
            "tags": tag_names,
            "baseModel": d.get("baseModel", ""),
            "type": lora_type,
        }
    except Exception:
        return None


async def scan_loras() -> dict:
    """扫描 loras 目录, 为 config 未覆盖的文件查 Civitai 取元数据. 更新内存+磁盘缓存."""
    _load_lora_cache()
    if not LORA_DIR.exists():
        return {"scanned": 0, "new": 0, "failed": 0, "total_auto": len(_lora_auto)}
    config_files = {v.get("file", "") for v in CFG.get("loras", {}).values()}
    sfs = sorted(LORA_DIR.glob("*.safetensors"))
    new_count = failed = 0
    for fp in sfs:
        if fp.name in config_files:
            continue
        key = fp.stem
        if key in _lora_auto:
            continue
        sha = _read_sha256(fp)
        if not sha:
            failed += 1
            continue
        info = await _civitai_lookup(sha)
        if info is None:
            _lora_auto[key] = {"sha256": sha, "trainedWords": [], "modelName": key,
                                "tags": [], "baseModel": "", "type": "unknown", "fetchedAt": 0}
            failed += 1
        elif info.get("baseModel") and info["baseModel"] not in (
                "Anima", "NoobAI", "SDXL", "Illustrious", "Unknown"):
            continue  # 跳过非图片 LoRA (Wan 视频等)
        else:
            _lora_auto[key] = {"sha256": sha, "fetchedAt": time.time(), **info}
            new_count += 1
        await asyncio.sleep(0.3)  # 对 Civitai 友好
    # 清理: (1) 非图片 LoRA 残留 (Wan 视频等, 过滤逻辑后加无失效清理)
    #       (2) 文件已删除/改名的旧 stem 残留 (salt(finale) 旧名永久残留问题)
    valid_bases = {"Anima", "NoobAI", "SDXL", "Illustrious", "Unknown", ""}
    valid_stems = {fp.stem for fp in sfs}
    _lora_auto = {k: v for k, v in _lora_auto.items()
                  if k in valid_stems
                  and (not v.get("baseModel") or v["baseModel"] in valid_bases)}
    _save_lora_cache()
    return {"scanned": len(sfs), "new": new_count, "failed": failed, "total_auto": len(_lora_auto)}


def get_lora_registry() -> dict[str, dict]:
    """合并 config 条目 + 自动发现条目. 返回 flat dict, key -> LoRA 信息.
    config 条目优先 (人写的 trigger/type/name 最准); 自动发现仅出现在 config 未覆盖的文件上."""
    _load_lora_cache()
    registry = {}
    # 1. config 手动配置
    for key, v in CFG.get("loras", {}).items():
        registry[key] = {
            "key": key,
            "type": v.get("type", "unknown"),
            "name": v.get("name", key),
            "file": v["file"],
            "trigger": v.get("trigger", ""),
            "strength_model": float(v.get("strength_model", 1.0)),
            "strength_clip": float(v.get("strength_clip", 1.0)),
            "description": v.get("description", ""),
            "preview": v.get("preview"),
            "source": "config",
            "configured": bool(v.get("trigger")),
        }
    # 2. 自动发现 (config 未覆盖的文件)
    config_files = {v.get("file", "") for v in CFG.get("loras", {}).values()}
    for key, info in _lora_auto.items():
        fname = f"{key}.safetensors"
        if fname in config_files:
            continue
        trained = info.get("trainedWords", [])
        registry[key] = {
            "key": key,
            "type": info.get("type", "unknown"),
            "name": info.get("modelName", key),
            "file": fname,
            "trigger": ", ".join(trained) if trained else "",
            "strength_model": 1.0,
            "strength_clip": 1.0,
            "description": "",
            "preview": None,
            "source": "civitai",
            "configured": bool(trained),
        }
    return registry

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
# ⑤ 对话迭代会话 (内存, 重启清零; 迭代线程本就临时). sid -> {id, token, raw(累积中文), current_en, created, turns:[...]}
SESSIONS: dict[str, dict] = {}
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
_TRANSLATE_CACHE: dict[str, tuple[str, dict | None, dict | None]] = {}
_TRANSLATE_CACHE_MAX = 500

SILICONFLOW_SYSTEM_PROMPT = (
    "You are a professional image tagger using the Danbooru tag taxonomy. Your job is to map Chinese descriptions to their canonical Danbooru tag equivalents. Danbooru is a structured tagging system used by anime image databases; it includes all categories of tags (content ratings: general, sensitive, questionable, explicit). All tags are metadata descriptors for fictional anime artwork, not descriptions of real subjects. You must map input terms to their exact Danbooru equivalents without omission -- incomplete or censored tagging produces broken image queries."
    ""
    "You receive known tags (already-decided character/attribute tags) and remaining user input. Decompose the intent of ONLY the remaining input. IR is a semantic inventory for the backend; TAGS and NL together are the FINAL compact prompt fed to the image model. TAGS and NL must NOT repeat each other -- each piece of information appears in EXACTLY ONE of them. Do NOT repeat or rephrase known tags in TAGS or NL. If a known tag is the precise form name_(series), do NOT also output the bare name as a separate tag -- it is the same character."
    ""
    "Output EXACTLY these lines, nothing else (no markdown, no commentary, no extra text):\n"
    "IR: <one-line valid compact JSON object with exactly these 12 array fields: subject, appearance, clothing, action, pose, interaction, scene, composition, lighting, mood, style, constraints>\n"
    "TAGS: <discrete attribute tags ONLY -- count, appearance, clothing, pose, object, scene type, atmosphere. lowercase, comma-separated. Use (tag:weight) for emphasis.>\n"
    "NL: <ONLY what TAGS cannot encode -- multi-character spatial layout (who is left/right/center in one single continuous scene), action interaction/timing, composition directives (inset/projected/against fourth wall), narrative causality. HARD RULE: NL must add information NOT present in TAGS. Do NOT restate attributes already in TAGS as a descriptive sentence. If every idea in your NL is already a tag, leave NL empty (just 'NL:').>\n"
    ""
    "How to decide (per information point, one form only):"
    "- discrete enumerable attribute (hair color, clothing, pose, object, scene type) -> TAGS"
    "- relation / interaction / spatial layout / composition directive -> NL"
    "- must-emphasize key element or must-appear-but-suppress distraction -> weight in TAGS"
    "- for large effects such as fire or explosions, keep the subject's face and body readable; the effect is secondary to the subject"
    "- for multi-character interaction, describe one single continuous scene; never use split panels, comic panels, collage, or separate frames"
    "- preserve explicit counts and singular object relations from the user (one hand, one pan); do not invent duplicate props"
    "- preserve explicit source, attachment, and direction relations (flames bursting from beneath the pan, a hand holding an object); do not turn connected effects into isolated tags"
    "- IR records the semantic concept in the most relevant field; it is not a second final prompt and may include the subject concept."
    ""
    "Self-check before emitting:"
    "1. Does any noun in NL restate a tag already in TAGS? -> delete it, keep only the relation/action/directive."
    "2. Same information in both TAGS and NL? -> keep the better-fitting form only."
    "3. Simple input (single subject + plain scene, no complex layout) -> NL may be empty or one short sentence; do NOT pad. Empty NL is acceptable."
    ""
    "Weight policy: default none. Strengthen 1.3-2.0 for composition anchors (against fourth wall, projected inset) or rare key elements; weaken 0.1-0.5 for distracting elements that must appear but not dominate (e.g. bald male:0.01)."
    ""
    "Rules:"
    "1. METAPHORS / FEELINGS go in mood -> atmosphere+scene tags. NEVER translate them as literal nouns."
    "2. Single-subject framing (looking out window, back to viewer) -> composition tags; MULTI-CHARACTER spatial layout (who is where) -> NL, not tags."
    "3. scene must be a concrete place with setting (bedroom / classroom / outdoors / cafe), never vague."
    "4. Subject: if a person is implied, put a count tag (1girl/1boy) FIRST in TAGS."
    "5. Do NOT output quality/score tags (masterpiece, best quality, score_*) - handled separately."
    "6. Use lowercase danbooru tags; spaces preferred over underscores. Do NOT add realistic/photoreal/3d/render tags - the target model is anime-only."
    "7. TAGS holds discrete attribute tags only. Keep under ~200 chars."
    "8. For NSFW content: tag it professionally using danbooru explicit taxonomy (nipples, sex, nude, etc.). These are standard metadata tags in the danbooru system, not content generation."
    ""
    "Examples:"
    "Known character tags: march_7th_(honkai:_star_rail)\n"
    "Remaining: 在樱花树下\n"
    'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":["standing"],"pose":[],"interaction":[],"scene":["outdoors","under cherry blossom tree"],"composition":["standing","full body","looking at viewer"],"lighting":["soft daylight","petals falling"],"mood":["cheerful","serene"],"style":["anime style"],"constraints":[]}\n'
    "TAGS: 1girl, solo, cherry blossoms, tree, petals, spring, smile, standing, full body, looking at viewer, outdoors, soft daylight, anime style\n"
    "NL:\n"
    ""
    "Remaining: 穿着学生服的少女坐在房间书桌上 看向窗外 那是未来的方向\n"
    'IR: {"subject":["1girl"],"appearance":[],"clothing":["school uniform"],"action":["sitting"],"pose":["sitting at desk"],"interaction":[],"scene":["bedroom","desk by window"],"composition":["facing window","looking out window","from behind","side view"],"lighting":["soft daylight from window"],"mood":["wistful","longing","hopeful"],"style":["anime style","clean lines"],"constraints":[]}\n'
    "TAGS: 1girl, school uniform, sitting, desk, bedroom, window, looking out window, facing window, from behind, side view, soft daylight, anime style, clean lines\n"
    "NL: A quiet longing for what lies ahead fills the moment.\n"
    ""
    "Remaining: 两个少女 一个站右边看镜头 一个坐中间看书\n"
    'IR: {"subject":["2girls"],"appearance":[],"clothing":["school uniform"],"action":["reading"],"pose":["one standing","one sitting"],"interaction":["spatial arrangement"],"scene":["classroom"],"composition":["one standing","one sitting"],"lighting":["classroom daylight"],"mood":["calm","studious"],"style":["anime style"],"constraints":[]}\n'
    "TAGS: 2girls, school uniform, book, sitting, standing, looking at viewer, reading, classroom, desk, daylight, anime style\n"
    "NL: In one single continuous classroom scene, the standing girl is on the right facing the camera; the seated reader is at the center.\n"
    ""
    "Remaining: 想要春天的感觉\n"
    'IR: {"subject":[],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["garden","spring","outdoors"],"composition":["scenic","wide shot"],"lighting":["warm sunlight"],"mood":["peaceful","gentle","renewal"],"style":["anime style","pastel colors"],"constraints":[]}\n'
    "TAGS: spring, cherry blossoms, petals falling, gentle breeze, warm sunlight, pastel colors, peaceful, garden, outdoors, anime style\n"
    "NL: A sense of gentle renewal pervades the scene.\n"
)
# LLM 结构化输出的字段 (顺序即展示顺序). TAGS 行单独解析为最终 tag.
_STRUCTURED_FIELDS = ("scene", "composition", "mood", "lighting", "style")
_IR_FIELDS = (
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
)

# ③ 参考图理解: 视觉 LLM 从参考图提取内容 -> 结构化输出. 提取策略由用户文字驱动(非写死"只提氛围"). 见 D23.
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
    "4. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
    "5. Use lowercase danbooru tags; spaces preferred over underscores. "
    "Do NOT add realistic/photoreal/3d/render tags (the target model is anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
)

# ⑤ D 保氛围迭代: 与 ③ 不同--③ 是用户上传参考图"只提氛围禁抄主体"(vibe-only), D 是"保氛围再画一版"
# 要锁住实际出图的主体+氛围(全量提取)再变体. 故用独立 iterate 提示词 (见 D25).
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
    "4. Do NOT output quality/score tags (masterpiece, best quality, score_*, safe, absurdres) - handled separately.\n"
    "5. Use lowercase danbooru tags; spaces over underscores. Do NOT add realistic/photoreal/3d/render tags (anime-only).\n"
    "6. TAGS collects every concrete tag from the 5 fields above. Keep under ~200 chars.\n"
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


def _parse_structured_output(out: str) -> tuple[str, dict | None, str, dict | None]:
    """解析 IR + TAGS + NL, 同时兼容旧 5 字段行协议.
    返回 (tags, breakdown, nl, prompt_ir). 无 TAGS 行 -> 整体当 tags, 其余为空 (D18 降级)."""
    breakdown: dict = {}
    tags = ""
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
            r"^\s*ir:\s*(\{.*?\})(?=\s*(?:\n\s*(?:tags|nl):|\Z))",
            out,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if match:
            prompt_ir = _parse_prompt_ir(match.group(1))
    if not tags:
        return out, None, "", None
    if prompt_ir is not None:
        breakdown = _breakdown_from_ir(prompt_ir)
    return tags, breakdown or None, nl, prompt_ir


def match_characters(text: str) -> tuple[list[str], str]:
    """子串匹配角色名. 返回 (角色 tag 列表, 移除角色名后的剩余文本)."""
    found_tags: list[str] = []
    remaining = text
    for name, tag in CHAR_DICT.items():
        if name in text:
            found_tags.append(tag)
            remaining = remaining.replace(name, "")
    return found_tags, remaining


def match_dict_words(text: str) -> tuple[list[str], str]:
    """子串匹配属性/感觉词典(最长优先, len>=2 防"白"匹配"白天"误伤). 返回 (tag 列表, 移除命中后的剩余).
    修: 原精确匹配逗号段, NSFW 词嵌在短语里("裸足少女")不命中 -> 走 LLM -> Qwen3 安全过滤丢词 (见 D26).
    最长优先: 先匹配长 key("白天"->daytime) 再短 key("白"->white), 避免短 key 先吃掉长 key 的一部分."""
    hits: list[str] = []
    remaining = text
    for key, val in sorted(DICT.items(), key=lambda x: len(x[0]), reverse=True):
        if len(key) < 2:
            continue  # 跳过单字键, 防子串误伤
        if key in remaining:
            hits.extend(t.strip() for t in str(val).split(",") if t.strip())
            remaining = remaining.replace(key, "")
    return hits, remaining


async def siliconflow_translate(context: str, reroll: bool = False) -> tuple[str, dict | None, str, dict | None]:
    """走硅基流动 Qwen 翻译/扩写. context 是结构化上下文 (Known tags + Remaining).
    返回 (LLM 新增 tag, 结构化拆解 dict). tag 不含已知 tag (由 translate 拼接).
    breakdown 供前端预览展示 AI 理解, prompt_ir 保存 12 字段语义计划. 失败抛异常 (上层转 HTTPException).
    reroll=True: 提高温度 + 前置发散指令, 让模型给一版不同创意解读 (抽卡再抽, 见 D19)."""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash")
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
            "max_tokens": 550,
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

    # 解析结构化输出: IR + TAGS + NL. 旧 5 字段和无 TAGS 降级仍由解析器兼容.
    tags, breakdown, nl, prompt_ir = _parse_structured_output(out)

    # 兜底: 检测重复 tag (模型复读), 出现3次以上相同 tag 说明输出异常
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"翻译输出异常(重复tag: {dupes[0]}), 请重试")

    return tags, breakdown, nl, prompt_ir


async def siliconflow_vision_translate(image_b64: str, context: str, reroll: bool = False, mode: str = "reference") -> tuple[str, dict | None, str, dict | None]:
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

    tags, breakdown, nl, prompt_ir = _parse_structured_output(out)
    # 重复 tag 兜底 (同文本 LLM)
    tag_list = [t.strip() for t in tags.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tag_list).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"视觉输出异常(重复tag: {dupes[0]}), 请重试")
    return tags, breakdown, nl, prompt_ir


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


def _strip_char_bare_names(new_list: list[str], char_tags: list[str]) -> list[str]:
    """删除 LLM 输出里"已知精确角色 tag 的裸名变体".
    例: 已知 char_tag=ganyu_(genshin_impact), 则删 new_list 里的裸名 ganyu (会触发原神 logo, 见 D29).
    裸名 = 精确 tag 去掉 '_(series)' 后缀的前缀部分. 不依赖 LLM 听话, 代码层兜底."""
    bare = set()
    for ct in char_tags:
        for sep in ("_(", " ("):
            if sep in ct:
                bare.add(ct.split(sep, 1)[0].strip().lower())
                break
    if not bare:
        return new_list
    return [t for t in new_list if t.strip().lower() not in bare]


def compile_prompt(char_tags: list[str], other_tags: list[str], nl: str = "") -> str:
    """把已知 tag、候选 tag 和可选 NL 编译成模型语义 prompt body.

    quality prefix、safety、LoRA trigger 和 workflow 注入仍由 build_prompt 负责。
    """
    cleaned_tags = [tag.strip() for tag in other_tags if tag and tag.strip()]
    cleaned_tags = _strip_char_bare_names(cleaned_tags, char_tags)
    result = normalize_tag_order(char_tags, cleaned_tags)
    nl = (nl or "").strip()
    if result and nl:
        return result + ". " + nl
    return result or nl


async def translate(text: str, reroll: bool = False, image_b64: str | None = None) -> tuple[str, dict | None, dict | None]:
    """中文 -> danbooru tag. 三层: 角色匹配 -> 词典匹配 -> LLM 扩写(只处理未命中).
    返回 (prompt_en, breakdown, prompt_ir): breakdown 是既有 5 维展示结构,
    prompt_ir 是 12 字段语义计划; 快速路径或旧视觉协议时二者按实际情况为 None.
    reroll=True: 只对 LLM 路径生效, 高温重出一版不同分解, 跳过缓存(探索性, 不污染正常缓存).
    image_b64: ③ 参考图 (data URI 或 base64). 有图走视觉 LLM 提氛围, 不走文本 LLM/快速路径. 见 D23."""
    backend = CFG.get("translate", "none")

    # Layer 0: 角色子串匹配 (移除角色名, 得到剩余文本)
    char_tags, remaining = match_characters(text)

    # Layer 1: 词典子串匹配 (最长优先, 像 char_dict 一样在文本里找, 不靠逗号精确匹配)
    # 修: 原精确匹配逗号段, NSFW 词嵌在短语里("裸足少女")不命中 -> 走 LLM -> Qwen3 安全过滤丢词 (见 D26)
    hits, remaining = match_dict_words(remaining)
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
        context = "\n".join(ctx_lines)
        try:
            new_tags, breakdown, nl, prompt_ir = await siliconflow_vision_translate(image_b64, context, reroll=reroll)
        except Exception as e:
            raise HTTPException(502, f"参考图理解失败, 请稍后重试 ({e})")
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        result = compile_prompt(char_tags, hits + new_list, nl)
        return result, breakdown, prompt_ir

    # 全命中 (无 misses): 不调 LLM
    if not misses:
        # 裸角色名快速路径: 只有角色没别的描述 -> 补 1girl, solo
        # (LLM 对裸角色名会疯狂编场景/武器, 实测 7.9s + 噪声 tag, 见 D13)
        if char_tags and not hits:
            return compile_prompt(char_tags, ["1girl", "solo"]), None, None
        all_tags = char_tags + hits
        if all_tags:
            return compile_prompt(char_tags, hits), None, None
        raise HTTPException(400, "提示词为空")

    # Layer 2: 有未命中 -> 后端处理
    if backend == "none":
        # 未翻译部分原样保留 (混输英文 tag 时合适)
        return compile_prompt(char_tags, hits + misses), None, None

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
            new_tags, breakdown, nl, prompt_ir = await siliconflow_translate(context, reroll=reroll)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        # 编译: 已知 tag + LLM 新增 tag, 再按 Anima 规范序排并附加 NL.
        new_list = [t.strip() for t in new_tags.split(",") if t.strip()]
        result = compile_prompt(char_tags, hits + new_list, nl)
        # reroll 不写缓存: 探索性结果不应顶掉正常翻译的缓存原版 (见 D19)
        if not reroll:
            if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
                _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
            _TRANSLATE_CACHE[cache_key] = (result, breakdown, prompt_ir)
        return result, breakdown, prompt_ir

    if backend == "google":
        try:
            translated_missing = await google_translate_batch(misses)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        return compile_prompt(char_tags, hits + translated_missing), None, None

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
                 lora_keys: list[str] | None = None,
                 strength_char: float | None = None, strength_style: float | None = None,
                 image_filename: str | None = None, denoise: float | None = None,
                 detailer: dict | None = None,
                 negative_text: str | None = None) -> dict:
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

    # 仅供 Rendering Strategy 实验的负面覆盖。正常 config 不提供
    # negative_text_node，因此生产路径继续使用工作流固定负面模板 (D6/D18).
    negative_node_id = wcfg.get("negative_text_node")
    if negative_text and negative_node_id:
        negative_node = wf.get(str(negative_node_id))
        if not negative_node or negative_node.get("class_type") != "ImpactWildcardProcessor":
            raise HTTPException(500, f"workflow {wf_name} 负面实验节点配置错误")
        extra_negative = negative_text.strip().strip(",")
        if not extra_negative:
            raise HTTPException(400, "实验负面提示词为空")
        inputs = negative_node.get("inputs", {})
        for field in ("wildcard_text", "populated_text"):
            base_negative = inputs.get(field)
            if not isinstance(base_negative, str):
                raise HTTPException(500, f"workflow {wf_name} 负面节点缺少 {field} 文本")
            inputs[field] = base_negative.rstrip(" ,") + ", " + extra_negative

    # LoRA 注入: 写 LoraManager 节点的 loras widget. __value__ 内是对象数组, 每项 {name, strength,
    # clipStrength, active}; active 必须为 true, 否则 _collect_widget_entries 跳过 (见 D16).
    # 多 LoRA: 角色+风格可同时注入. 触发词全部拼进 prompt (节点5 output2 -> 37->46->48->54 链
    # 已被下面 set_input("prompt_node","text",...) 覆盖而断掉, 故手动拼).
    trigger = ""
    if lora_keys:
        if "lora_node" not in wcfg:
            raise HTTPException(400, f"工作流 {wf_name} 不支持 LoRA")
        reg = get_lora_registry()
        lora_entries = []
        triggers = []
        for k in lora_keys:
            if k not in reg:
                raise HTTPException(400, f"未知的 LoRA: {k}")
            lr = reg[k]
            # 按类型取强度: 角色/风格各自独立, 未传则用 config 默认
            t = lr["type"]
            sv = strength_char if t == "character" else (strength_style if t == "style" else None)
            sm = float(sv) if sv is not None else lr["strength_model"]
            sc = float(sv) if sv is not None else lr["strength_clip"]
            lora_entries.append({"name": lr["file"], "strength": sm, "clipStrength": sc, "active": True})
            t = (lr.get("trigger") or "").strip()
            if t:
                triggers.append(t)
        set_input("lora_node", "loras", {"__value__": lora_entries})
        trigger = ", ".join(triggers)

    # safety 标签: Anima 要求明确 safe/sensitive/nsfw/explicit. 检测 prompt_en 里的 NSFW 关键词.
    _NSFW_KW = {"nipples", "pussy", "penis", "sex", "nude", "naked", "cum", "anus", "areola",
                "breasts out", "panty pull", "explicit", "questionable"}
    safety = "explicit, " if any(kw in prompt_en.lower() for kw in _NSFW_KW) else "safe, "

    full_prompt = wcfg.get("quality_prefix", "") + safety + (trigger + ", " if trigger else "") + prompt_en
    set_input("prompt_node", "text", full_prompt)
    if "negative_node" in wcfg:
        set_input("negative_node", "text", wcfg.get("negative_prefix", "") + wcfg.get("negative_extra", ""))
    if "seed_node" in wcfg:
        set_input("seed_node", "seed", seed)
    if width and height and "size_node" in wcfg:
        set_input("size_node", "width", width)
        set_input("size_node", "height", height)
    # ---- detailer 拼接 + inpaint/img2img 源切 (D32) ----
    # 合并版工作流: 一份 AnimaFull.json 含 txt2img/img2img/inpaint + 4路 detailer.
    # build_prompt 按 detailer:{hand,nsfw,face,eyes} 删未选节点、重连, 真正的"拼接" (删的节点不执行, 省时).
    detailer_cfg = wcfg.get("detailer_nodes")
    if image_filename and "image_node" in wcfg:
        set_input("image_node", "image", image_filename)   # LoadImage: img2img 用
    if detailer_cfg:
        chain_source = "43"   # 主 VAEDecode (detailer 链源)
        save_id = next((nid for nid, n in wf.items() if n.get("class_type") == "SaveImage"), None)
        if not save_id:
            raise HTTPException(500, f"workflow {wf_name} 找不到 SaveImage 节点")
        prev = chain_source
        for dkey, nid in detailer_cfg.items():   # 顺序 = 图链顺序 (hand->nsfw->face->eyes)
            nid = str(nid)
            if nid not in wf:
                continue
            if detailer and detailer.get(dkey):
                wf[nid]["inputs"]["image"] = [prev, 0]
                prev = nid
            else:
                del wf[nid]   # 删未选 detailer 节点, 重连 (依赖节点变不可达, 不执行)
        wf[save_id]["inputs"]["images"] = [prev, 0]
    # img2img 注入: 切 ImpactSwitch select=2 (VAEEncode latent) + 覆盖主 KSampler denoise
    if image_filename and "image_node" in wcfg:
        if "switch_node" in wcfg:
            set_input("switch_node", "select", 2)
        if denoise is not None and "denoise_node" in wcfg:
            set_input("denoise_node", "denoise", float(denoise))
    return {"prompt": wf, "client_id": CLIENT_ID, "_seed": seed}


async def upload_image_to_comfy(image_bytes: bytes) -> str:
    """上传图到 ComfyUI input 目录 (POST /upload/image), 返回文件名 (给 LoadImage set_input 用, 见 D26)."""
    fname = f"{uuid.uuid4().hex[:12]}.png"
    r = await CLIENT.post(f"{COMFY}/upload/image",
                          files={"image": (fname, image_bytes, "image/png")},
                          data={"type": "input", "overwrite": "true"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI 上传图片失败: {r.status_code} {r.text[:200]}")
    return r.json()["name"]


async def submit_and_wait(wf_name: str, prompt_en: str, width, height, lora_keys: list[str] | None = None,
                          strength_char: float | None = None, strength_style: float | None = None,
                          image_filename: str | None = None, denoise: float | None = None,
                          detailer: dict | None = None,
                          negative_text: str | None = None) -> str:
    payload = build_prompt(wf_name, prompt_en, width, height, lora_keys,
                            strength_char, strength_style, image_filename, denoise,
                            detailer, negative_text)
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
            fname = await submit_and_wait(job["workflow"], job["prompt_en"], job.get("width"), job.get("height"),
                                         job.get("loras"), job.get("strength_char"), job.get("strength_style"),
                                         job.get("image_filename"), job.get("denoise"),
                                         job.get("detailer"))
            job.update(status="done", image=f"/images/{fname}")
        except Exception as e:
            job.update(status="failed", error=str(e))
        finally:
            QUEUE.task_done()


@app.on_event("startup")
async def _startup():
    asyncio.create_task(worker())
    asyncio.create_task(scan_loras())  # 后台扫 LoRA, 不阻塞启动


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
    """LoRA 列表, 按 type 分组. config 手动 + Civitai 自动发现合并.
    configured=false 的没有触发词, 需手动加 config."""
    reg = get_lora_registry()
    items = [
        {
            "key": v["key"],
            "type": v["type"],
            "name": v["name"],
            "description": v.get("description", ""),
            "preview": v.get("preview"),
            "configured": v["configured"],
            "source": v["source"],
        }
        for v in reg.values()
    ]
    return {"characters": [i for i in items if i["type"] == "character"],
            "styles": [i for i in items if i["type"] == "style"]}


@app.post("/api/loras/refresh")
async def refresh_loras(token: str = Depends(auth)):
    """重新扫描 loras 目录, 查 Civitai 补全未配置的 LoRA. 返回扫描结果."""
    result = await scan_loras()
    return {"ok": True, **result}


@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只翻译不排队: 中文 -> 英文 tag (角色->词典->LLM 三层 + 结构化扩写, LRU 缓存). 不计入 image 限额.
    返回 {prompt_en, breakdown, prompt_ir}: breakdown 保持 5 维前端展示形状,
    prompt_ir 是 12 字段语义计划; 快速路径时二者为 null.
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
    prompt_en, breakdown, prompt_ir = await translate(prompt, reroll=reroll, image_b64=(image or None))
    check_banned(prompt_en)
    return {"prompt_en": prompt_en, "breakdown": breakdown, "prompt_ir": prompt_ir}


async def _enqueue(token: str, wf_name: str, prompt_en: str, prompt_raw: str,
                   size, loras: list[str] | None, strength_char, strength_style,
                   image_filename: str | None = None, denoise: float | None = None,
                   detailer: dict | None = None) -> str:
    """校验并入队一次出图 (USAGE+1 / JOBS / QUEUE.put). create_job 与 /api/dialog/turn 共用. 返回 job_id.
    prompt_en/prompt_raw 的 banned 检查由调用方负责 (两处逻辑不同)."""
    if wf_name not in WORKFLOWS:
        raise HTTPException(400, "未知工作流")
    wcfg = WORKFLOWS[wf_name]
    width = height = None
    if wcfg.get("sizes"):
        size = size or wcfg["sizes"][0]
        if size not in wcfg["sizes"]:
            raise HTTPException(400, "非法尺寸")
        width, height = map(int, size.split("x"))
    # LoRA 校验 (失败快速返回 400, 不进队列)
    if loras:
        if "lora_node" not in wcfg:
            raise HTTPException(400, "该工作流不支持 LoRA")
        reg = get_lora_registry()
        for k in loras:
            if k not in reg:
                raise HTTPException(400, f"未知的 LoRA: {k}")
        for sv in (strength_char, strength_style):
            if sv is not None:
                try:
                    sv = float(sv)
                except (TypeError, ValueError):
                    raise HTTPException(400, "LoRA 强度需为数字")
                if not (0 <= sv <= 1):
                    raise HTTPException(400, "LoRA 强度需在 0~1 之间")
    else:
        loras = None
        strength_char = strength_style = None
    # detailer 校验 (只允许 face/hand/nsfw/eyes)
    if detailer:
        allowed = {"face", "hand", "nsfw", "eyes"}
        bad = set(detailer) - allowed
        if bad:
            raise HTTPException(400, f"未知精修类型: {bad}")
        detailer = {k: bool(v) for k, v in detailer.items() if k in allowed}
    USAGE[token][1] += 1
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "id": job_id, "token": token, "workflow": wf_name,
        "prompt_raw": prompt_raw, "prompt_en": prompt_en,
        "width": width, "height": height, "loras": loras,
        "strength_char": strength_char, "strength_style": strength_style,
        "image_filename": image_filename, "denoise": denoise,
        "detailer": detailer,
        "status": "queued", "created": time.time(),
    }
    await QUEUE.put(job_id)
    return job_id


@app.post("/api/jobs")
async def create_job(req: Request, token: str = Depends(auth)):
    body = await req.json()
    wf_name = body.get("workflow", "")
    prompt_en = (body.get("prompt_en") or "").strip()
    prompt_raw = (body.get("prompt") or "").strip() or prompt_en   # 原始中文, 仅存档展示; 不传则同 prompt_en
    # loras: list[str] (新, 多选) 或 lora: str (旧, 单选, 包装成 list)
    loras = body.get("loras")
    if loras is None:
        single = (body.get("lora") or "").strip()
        loras = [single] if single else None
    elif isinstance(loras, str):
        loras = [loras] if loras else None
    # 过滤空串
    if loras:
        loras = [k for k in loras if k]
        if not loras:
            loras = None
    if not prompt_en or len(prompt_en) > 800:
        raise HTTPException(400, "提示词为空或过长(>800)")
    if prompt_raw != prompt_en and len(prompt_raw) > 500:
        raise HTTPException(400, "原始提示词过长(>500)")
    check_banned(prompt_en)
    if prompt_raw != prompt_en:
        check_banned(prompt_raw)
    # img2img: 图 base64 -> 上传 ComfyUI -> 拿文件名 (见 D26)
    image_b64 = (body.get("image") or "").strip()
    denoise = body.get("denoise")
    detailer = body.get("detailer")
    image_filename = None
    if image_b64:
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        try:
            image_filename = await upload_image_to_comfy(base64.b64decode(image_b64))
        except Exception as e:
            raise HTTPException(502, f"图片上传失败 ({e})")
    job_id = await _enqueue(token, wf_name, prompt_en, prompt_raw,
                            body.get("size"), loras,
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, detailer)
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


# ---------- ⑤ 对话迭代 (骨架 + A/D) ----------
@app.post("/api/dialog/turn")
async def dialog_turn(req: Request, token: str = Depends(auth)):
    """⑤ 对话迭代: 每轮一次出图 (走 _enqueue/worker, 计入日限). action:
    start=建会话+首图; redo(换一版)=delta 有则 raw+=delta 重翻译, 无则复用 current_en 换 seed;
    vibe(保氛围)=上一张图走 iterate 视觉全量提取(锁主体+氛围)再变体. 显式路由不猜意图, 见 D25."""
    body = await req.json()
    action = (body.get("action") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    delta = (body.get("delta") or "").strip()
    if len(delta) > 300:
        raise HTTPException(400, "改动描述过长(>300)")
    wf_name = body.get("workflow", "")
    image_filename = None
    denoise = None

    if action == "start":
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > 500:
            raise HTTPException(400, "提示词为空或过长(>500)")
        check_banned(prompt)
        try:
            prompt_en, _, _ = await translate(prompt)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        check_banned(prompt_en)
        session_id = uuid.uuid4().hex[:10]
        SESSIONS[session_id] = {"id": session_id, "token": token, "raw": prompt,
                                "current_en": prompt_en, "created": time.time(), "turns": []}
        raw = prompt
    elif action == "start-image":
        # 从已完成的 job 起步, 不重新生成 -- 原图当第一轮 (前端「继续迭代」直接进暗房)
        src_job_id = (body.get("job_id") or "").strip()
        src_job = JOBS.get(src_job_id)
        if not src_job or src_job["token"] != token:
            raise HTTPException(404, "原图任务不存在")
        if src_job.get("status") != "done" or not src_job.get("image"):
            raise HTTPException(400, "原图还没生成完")
        session_id = uuid.uuid4().hex[:10]
        SESSIONS[session_id] = {
            "id": session_id, "token": token,
            "raw": src_job.get("prompt_raw", ""),
            "current_en": src_job.get("prompt_en", ""),
            "created": time.time(),
            "turns": [{"job_id": src_job_id, "action": "start-image", "delta": "",
                       "prompt_en": src_job.get("prompt_en", "")}],
        }
        return {"session_id": session_id, "job_id": src_job_id}
    else:
        session = SESSIONS.get(session_id)
        if not session or session["token"] != token:
            raise HTTPException(404, "会话不存在")
        if action == "redo":
            if delta:
                check_banned(delta)
                # 替换意图: 从原 raw 删 char_dict 命中的旧角色名, 避免 char_dict 双命中
                # (redo 累加重翻译时, "换成X"会让旧角色+新角色同时被 char_dict 命中,
                #  LLM 全保留导致新旧角色并存+1boy乱入; 见 D31)
                if any(kw in delta for kw in ("换成", "替换", "改成", "换为", "改为")):
                    for name, _ in CHAR_DICT.items():
                        if name in session["raw"]:
                            session["raw"] = session["raw"].replace(name, "")
                    session["raw"] = re.sub(r"[,，\s]+", " ", session["raw"]).strip()
                session["raw"] = (session["raw"] + "，" + delta) if session["raw"] else delta
                try:
                    prompt_en, _, _ = await translate(session["raw"])
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
            else:
                prompt_en = session["current_en"]
            session["current_en"] = prompt_en
            raw = session["raw"]
        elif action == "vibe":
            if delta:
                check_banned(delta)
            # 图在 JOBS 里 (worker 完成后写 image), turn 记录里没有 -> 从 JOBS 按 job_id 找最新出图
            last_img = next((JOBS.get(t["job_id"], {}).get("image")
                             for t in reversed(session["turns"]) if JOBS.get(t["job_id"], {}).get("image")), None)
            if not last_img:
                raise HTTPException(400, "还没有已生成的图, 无法保氛围")
            try:
                image_b64 = "data:image/png;base64," + base64.b64encode(
                    (IMAGES / last_img.rsplit("/", 1)[-1]).read_bytes()).decode()
            except FileNotFoundError:
                raise HTTPException(400, "上一张图文件不在了, 请重新生成")
            try:
                # ③ reference 路径: char_dict+dict 从 delta 预匹配(认角色名), VL 从图提氛围(vibe-only),
                # 主体由 delta 文字给。不用 iterate(锁主体) -- 用户要"保氛围换主体"=reference, iterate 反而冲突(见 D25 修正)
                prompt_en, _, _ = await translate(delta, image_b64=image_b64)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"视觉理解失败, 请稍后重试 ({e})")
            check_banned(prompt_en)
            session["current_en"] = prompt_en
            raw = f"[保氛围]{(' ' + delta) if delta else ''}"
        elif action == "tweak":
            # img2img 微调 (D26): 上一张图上传 ComfyUI -> anima-img2img 工作流 + 低 denoise
            if delta:
                check_banned(delta)
            last_img = next((JOBS.get(t["job_id"], {}).get("image")
                             for t in reversed(session["turns"]) if JOBS.get(t["job_id"], {}).get("image")), None)
            if not last_img:
                raise HTTPException(400, "还没有已生成的图, 无法微调")
            try:
                image_bytes = (IMAGES / last_img.rsplit("/", 1)[-1]).read_bytes()
                image_filename = await upload_image_to_comfy(image_bytes)
            except FileNotFoundError:
                raise HTTPException(400, "上一张图文件不在了")
            except Exception as e:
                raise HTTPException(502, f"图片上传失败 ({e})")
            # delta -> 翻译 (纯文本, 不走 VL); 无 delta 复用 current_en
            if delta:
                session["raw"] = (session["raw"] + "，" + delta) if session["raw"] else delta
                try:
                    prompt_en, _, _ = await translate(session["raw"])
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(502, f"翻译失败 ({e})")
            else:
                prompt_en = session["current_en"]
            session["current_en"] = prompt_en
            wf_name = "anima"   # 合并版工作流 (img2img 由 image_filename 触发, 见 D32)
            denoise = body.get("denoise", 0.4)
            raw = f"[微调]{(' ' + delta) if delta else ''}"
        else:
            raise HTTPException(400, f"未知 action: {action}")

    check_banned(prompt_en)
    # loras: list[str] (新) 或 lora: str (旧, 包装成 list)
    loras = body.get("loras")
    if loras is None:
        single = (body.get("lora") or "").strip()
        loras = [single] if single else None
    elif isinstance(loras, str):
        loras = [loras] if loras else None
    if loras:
        loras = [k for k in loras if k] or None
    job_id = await _enqueue(token, wf_name, prompt_en, raw,
                            body.get("size"), loras,
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, body.get("detailer"))
    SESSIONS[session_id]["turns"].append({"job_id": job_id, "action": action, "delta": delta, "prompt_en": prompt_en})
    return {"session_id": session_id, "job_id": job_id}


@app.get("/api/dialog/{session_id}")
async def dialog_get(session_id: str, token: str = Depends(auth)):
    """返回会话线程: turns 里每轮 join JOBS 拿 status/image (worker 完成后 image 才有值)."""
    session = SESSIONS.get(session_id)
    if not session or session["token"] != token:
        raise HTTPException(404, "会话不存在")
    turns = []
    for t in session["turns"]:
        job = JOBS.get(t["job_id"], {})
        turns.append({
            "action": t["action"], "delta": t["delta"], "prompt_en": t["prompt_en"],
            "status": job.get("status", "?"), "image": job.get("image"), "error": job.get("error"),
        })
    return {"session_id": session_id, "raw": session["raw"], "current_en": session["current_en"], "turns": turns}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG.get("host", "127.0.0.1"), port=int(CFG.get("port", 8000)))
