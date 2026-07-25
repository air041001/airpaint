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
DICT = yaml.safe_load((BASE / "dict.yaml").read_text(encoding="utf-8")) or {}
DICT = {str(k).strip().lower(): str(v).strip() for k, v in DICT.items() if v is not None}

# 角色词典: 中文名 -> danbooru 精确 tag. Qwen3-8B 认不准角色 tag (字面翻译/编造/漏认),
# 故角色走词典可靠命中, 命中后把 tag 作为上下文喂给 LLM (见 decisions.md D12).
_CHAR_DICT_PATH = BASE / "char_dict.yaml"
CHAR_DICT = yaml.safe_load(_CHAR_DICT_PATH.read_text(encoding="utf-8")) if _CHAR_DICT_PATH.exists() else {}
CHAR_DICT = {str(k).strip(): str(v).strip() for k, v in CHAR_DICT.items() if v is not None}

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
    return FileResponse(WEB_DIR / "index.html")

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
    "You are a prompt engineer for anime image generation. "
    "Convert user input into english danbooru-style tags. "
    "Output ONLY lowercase tags separated by commas and spaces. "
    "No explanations, no periods, no quotes, no markdown.\n\n"
    "Rules:\n"
    "1. If the input provides known character tags, include them EXACTLY as given.\n"
    "2. If user describes a feeling/mood (e.g. 治愈, 忧郁, 春天的感觉), expand into matching scene+lighting+style tags.\n"
    "3. If user describes specific details (hair/eyes/clothing), preserve them.\n"
    "4. Subject: if a person/character is mentioned or implied, include a count tag (1girl/1boy). "
    "If the input is PURELY a mood/scene with no person, focus on scenery and do NOT force a character.\n"
    "5. Keep total under 200 characters.\n\n"
    "Examples:\n"
    "Input: 白发蓝眼睛猫耳少女\n"
    "Output: 1girl, white hair, blue eyes, cat ears, smile, school uniform, standing, indoors, soft lighting, anime style\n\n"
    "Input: 想要春天的感觉\n"
    "Output: spring, cherry blossoms, petals falling, gentle breeze, warm sunlight, pastel colors, peaceful, garden, anime style\n\n"
    "Input: [Character: march_7th_(honkai:_star_rail)] 三月七在樱花树下\n"
    "Output: march_7th_(honkai:_star_rail), 1girl, solo, cherry blossoms, tree, petals, spring, smile, standing, outdoors, soft lighting, anime style"
)


def detect_characters(text: str) -> list[tuple[str, str]]:
    """扫描输入里出现的已知角色名, 返回 [(中文名, danbooru_tag)]. 子串匹配."""
    return [(name, tag) for name, tag in CHAR_DICT.items() if name in text]


async def siliconflow_translate(text: str) -> str:
    """走硅基流动 Qwen 模型翻译 + 意图扩写, 失败抛异常 (由上层转 HTTPException)"""
    api_key = CFG.get("siliconflow_api_key", "").strip()
    model = CFG.get("siliconflow_model", "Qwen/Qwen3-8B")
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")

    chars = detect_characters(text)
    # 快速路径: 输入基本只是一个已知角色名 (无其他描述) -> 直接出 tag, 跳过 LLM.
    # 原因: LLM 对裸角色名会疯狂编场景/武器 (实测 7.9s + sword/combat/archer 等噪声 tag).
    if chars:
        remain = text
        for name, _ in chars:
            remain = remain.replace(name, "")
        if not re.sub(r"[,，、;；\s\n]+", "", remain):
            return ", ".join(tag for _, tag in chars) + ", 1girl, solo"

    # 角色命中: 把可靠 tag 作为上下文喂给 LLM (LLM 认不准角色 tag, 只让它管场景/氛围扩写)
    char_ctx = f"[Character: {', '.join(tag for _, tag in chars)}] " if chars else ""

    r = await CLIENT.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SILICONFLOW_SYSTEM_PROMPT},
                # /no_think: Qwen3 软开关, 强制不进思考模式 (思考会慢到 30s+ 且易复读)
                {"role": "user", "content": "/no_think " + char_ctx + text},
            ],
            "temperature": 0.2,
            "max_tokens": 180,
            # ★ 关键: enable_thinking 必须放顶层, 放 extra_body 里硅基流动不认 -> 思考没关掉.
            #   实测放顶层后 3s 出结果, 放 extra_body 里要 27s+ 并出现 tag 复读退化.
            "enable_thinking": False,
        },
        timeout=30,
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

    # 兜底: 检测重复 tag (模型复读), 出现3次以上相同 tag 说明输出异常
    tags = [t.strip() for t in out.split(",")]
    from collections import Counter
    dupes = [t for t, c in Counter(tags).most_common(3) if c >= 3 and t]
    if dupes:
        raise RuntimeError(f"翻译输出异常(重复tag: {dupes[0]}), 请重试")

    return out


async def translate(text: str) -> str:
    """中文 -> danbooru tag. 优先命中本地词典, 剩余部分整段送 LLM."""
    backend = CFG.get("translate", "none")

    # 按逗号切, 逐段查词典; 全部命中就不用调 API
    parts = [p.strip() for p in re.split(r"[,，、;；\n]+", text) if p.strip()]
    if not parts:
        raise HTTPException(400, "提示词为空")

    hits, misses, miss_idx = [], [], []
    for i, p in enumerate(parts):
        h = DICT.get(p.lower())
        hits.append(h)
        if h is None:
            misses.append(p)
            miss_idx.append(i)

    # 全部命中词典: 直接拼接返回
    if not misses:
        return ", ".join(hits)

    # 有未命中: 按配置的后端处理
    if backend == "none":
        # 未翻译部分原样保留 (仅当用户混输英文 tag 时合适)
        return ", ".join(h if h is not None else parts[i] for i, h in enumerate(hits))

    if backend == "siliconflow":
        # 整段中文送 LLM (上下文完整, 质量更好), 而不是逐词翻译
        cache_key = text.strip()
        if cache_key in _TRANSLATE_CACHE:
            return _TRANSLATE_CACHE[cache_key]
        try:
            translated = await siliconflow_translate(text)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        # 简单容量控制
        if len(_TRANSLATE_CACHE) >= _TRANSLATE_CACHE_MAX:
            _TRANSLATE_CACHE.pop(next(iter(_TRANSLATE_CACHE)))
        _TRANSLATE_CACHE[cache_key] = translated
        return translated

    if backend == "google":
        try:
            translated_missing = await google_translate_batch(misses)
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        result = list(hits)
        for i, t in zip(miss_idx, translated_missing):
            result[i] = t
        return ", ".join(result)

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
    """剔除/替换"只能从 ComfyUI 前端排队时才能跑"的节点。
    这些节点依赖 extra_pnginfo['workflow'] (前端 UI 图), 而后端走 /prompt API 不带它, 会崩:
      - WidgetToString (KJNodes): 读 extra_pnginfo['workflow'] -> TypeError
      - Image Saver Metadata:     依赖 WidgetToString
      - Image Saver Simple:       依赖上面的 metadata, 且 embed_workflow 也要 extra_pnginfo
    把 Image Saver Simple 换成内置 SaveImage (API 可靠出图, outputs.images 标准格式, 后端能读)。"""
    INCOMPAT = {"WidgetToString", "Image Saver Metadata"}
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


def build_prompt(wf_name: str, prompt_en: str, width: int | None, height: int | None) -> dict:
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

    full_prompt = wcfg.get("quality_prefix", "") + prompt_en
    set_input("prompt_node", "text", full_prompt)
    if "negative_node" in wcfg:
        set_input("negative_node", "text", wcfg.get("negative_prefix", "") + wcfg.get("negative_extra", ""))
    if "seed_node" in wcfg:
        set_input("seed_node", "seed", seed)
    if width and height and "size_node" in wcfg:
        set_input("size_node", "width", width)
        set_input("size_node", "height", height)
    return {"prompt": wf, "client_id": CLIENT_ID, "_seed": seed}


async def submit_and_wait(wf_name: str, prompt_en: str, width, height) -> str:
    payload = build_prompt(wf_name, prompt_en, width, height)
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
            fname = await submit_and_wait(job["workflow"], job["prompt_en"], job.get("width"), job.get("height"))
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


@app.get("/api/workflows")
async def list_workflows(token: str = Depends(auth)):
    return [
        {"name": k, "label": v.get("label", k), "sizes": v.get("sizes")}
        for k, v in WORKFLOWS.items()
    ]


@app.post("/api/jobs")
async def create_job(req: Request, token: str = Depends(auth)):
    body = await req.json()
    wf_name = body.get("workflow", "")
    prompt_raw = (body.get("prompt") or "").strip()
    if wf_name not in WORKFLOWS:
        raise HTTPException(400, "未知工作流")
    if not prompt_raw or len(prompt_raw) > 500:
        raise HTTPException(400, "提示词为空或过长(>500)")
    check_banned(prompt_raw)

    prompt_en = await translate(prompt_raw)
    check_banned(prompt_en)

    wcfg = WORKFLOWS[wf_name]
    width = height = None
    if wcfg.get("sizes"):
        size = body.get("size") or wcfg["sizes"][0]
        if size not in wcfg["sizes"]:
            raise HTTPException(400, "非法尺寸")
        width, height = map(int, size.split("x"))

    USAGE[token][1] += 1
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "id": job_id, "token": token, "workflow": wf_name,
        "prompt_raw": prompt_raw, "prompt_en": prompt_en,
        "width": width, "height": height,
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
