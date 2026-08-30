"""FastAPI 组装、鉴权、任务队列与对话会话。"""
import asyncio
import base64
import re
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.knowledge import _character_names
from server.lora import (
    _bindings_as_selections,
    compile_lora_bindings,
    get_lora_registry,
    resolve_lora_selections,
)
from server.prompt_engine import _normalize_optional_concept, translate
from server.runtime import CLIENT, IMAGES, JOBS, QUEUE, SESSIONS, USAGE
from server.settings import (
    BASE,
    BANNED,
    CFG,
    COMFY,
    DAILY_LIMIT,
    DEFAULT_COMPLETION_LEVEL,
    LORA_PREVIEWS,
    MAX_COMPILED_PROMPT_CHARS,
    MAX_DIALOG_DELTA_CHARS,
    MAX_PROMPT_EN_CHARS,
    MAX_USER_PROMPT_CHARS,
    TOKENS,
    WORKFLOWS,
    _normalize_completion_level,
)
from server.workflow_engine import submit_and_wait, upload_image_to_comfy


app = FastAPI(title="AirPaint")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.get("allow_origins", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/images", StaticFiles(directory=IMAGES), name="images")
app.mount("/lora-previews", StaticFiles(directory=LORA_PREVIEWS), name="lora-previews")
WEB_DIR = BASE.parent / "web"


@app.get("/", include_in_schema=False)
async def index():
    # no-cache: 前端改了 (如修 fail-to-fetch) 后, 访客浏览器永远拿最新 HTML, 不被旧缓存卡住
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

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

def check_banned(text: str):
    low = text.lower()
    for w in BANNED:
        if w in low:
            raise HTTPException(400, f"提示词包含被禁止的内容: {w}")

async def worker():
    while True:
        job_id = await QUEUE.get()
        job = JOBS[job_id]
        job["status"] = "running"
        try:
            fname = await submit_and_wait(job["workflow"], job["prompt_en"], job.get("width"), job.get("height"),
                                         job.get("loras"), job.get("strength_char"), job.get("strength_style"),
                                         job.get("image_filename"), job.get("denoise"),
                                         job.get("detailer"), lora_bindings=job.get("lora_bindings"),
                                         registry_revision=job.get("registry_revision"))
            job.update(status="done", image=f"/images/{fname}")
        except Exception as e:
            job.update(status="failed", error=str(e))
        finally:
            QUEUE.task_done()

@app.on_event("startup")
async def _startup():
    asyncio.create_task(worker())

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
    """LoRA Asset 列表；Registry Profiles 优先，unknown/incomplete 放 other。"""
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
            "trigger_policy": v.get("trigger_policy", "none"),
            "provides": v.get("provides", []),
            "verified": v.get("verified"),
            "strength_model": v.get("strength_model", 1.0),
            "strength_clip": v.get("strength_clip", 1.0),
            "default_profile": (v.get("selection") or {}).get("default_profile"),
            # 保留响应字段兼容旧前端；现在只要 Asset 有多个 Profile 就可多选。
            "allow_multiple_profiles": len(v.get("profiles") or {}) > 1,
            "profiles": [
                {
                    "id": pid,
                    "name": profile.get("name", pid),
                    "aliases": profile.get("aliases", []),
                    "provides": profile.get("provides", []),
                    "verified": profile.get("verified"),
                    "optional": [
                        {"id": oid, "name": option.get("name", oid),
                         "provides": option.get("provides", [])}
                        for oid, option in (profile.get("optional_tags") or {}).items()
                    ],
                }
                for pid, profile in (v.get("profiles") or {}).items()
            ],
        }
        for v in reg.values()
    ]
    stackable_detail_types = {"style", "action", "expression"}
    return {"characters": [i for i in items if i["type"] == "character"],
            "styles": [i for i in items if i["type"] in stackable_detail_types],
            "other": [i for i in items
                      if i["type"] != "character" and i["type"] not in stackable_detail_types]}

def _extract_lora_selections(body: dict):
    if "lora_selections" in body:
        return body.get("lora_selections") or []
    loras = body.get("loras")
    if loras is not None:
        return loras
    single = body.get("lora")
    return [single] if single else []

@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只编译不排队: 中文构思 -> Anima Prompt，不计入 image 限额。
    返回 {concept, prompt_en, breakdown, prompt_ir, prompt_ir_meta}；completion_level 控制补全幅度，
    concept_override 可把用户编辑后的构思重新编译为 Prompt。
    body.reroll=true: LLM 高温重出一版不同画师补全方案 (抽卡再抽, 跳过缓存, 见 D19).
    body.image: 可选, 参考图 base64 (data URI). 有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23)."""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    image = (body.get("image") or "").strip()
    if not prompt and not image:
        raise HTTPException(400, "提示词和参考图不能同时为空")
    if len(prompt) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"提示词过长(>{MAX_USER_PROMPT_CHARS})")
    if len(image) > 5_000_000:
        raise HTTPException(400, "参考图过大(>5MB)")
    if prompt:
        check_banned(prompt)
    reroll = bool(body.get("reroll"))
    completion_level = _normalize_completion_level(body.get("completion_level"))
    concept_override = _normalize_optional_concept(
        body.get("concept_override"), "concept_override")
    if concept_override:
        check_banned(concept_override)
    prompt_en, breakdown, prompt_ir, prompt_ir_meta = await translate(
        prompt, reroll=reroll, image_b64=(image or None),
        lora_selections=_extract_lora_selections(body), include_meta=True,
        completion_level=completion_level, concept_override=concept_override,
    )
    check_banned(prompt_en)
    return {
        "prompt_en": prompt_en,
        "breakdown": breakdown,
        "prompt_ir": prompt_ir,
        "prompt_ir_meta": prompt_ir_meta,
        "concept": prompt_ir_meta.get("concept"),
        "lora_bindings": prompt_ir_meta.get("lora_bindings", []),
        "lora_warnings": prompt_ir_meta.get("lora_warnings", []),
        "registry_revision": prompt_ir_meta.get("registry_revision"),
    }

async def _enqueue(token: str, wf_name: str, prompt_en: str, prompt_raw: str,
                   size, lora_selections, strength_char, strength_style,
                   image_filename: str | None = None, denoise: float | None = None,
                   detailer: dict | None = None,
                   lora_bindings: list[dict] | None = None,
                   registry_revision: str | None = None,
                   concept: str | None = None,
                   completion_level: str = DEFAULT_COMPLETION_LEVEL) -> str:
    """校验并入队一次出图 (USAGE+1 / JOBS / QUEUE.put). create_job 与 /api/dialog/turn 共用. 返回 job_id.
    prompt_en/prompt_raw 的 banned 检查由调用方负责 (两处逻辑不同)."""
    completion_level = _normalize_completion_level(completion_level)
    concept = _normalize_optional_concept(concept, "concept")
    if wf_name not in WORKFLOWS:
        raise HTTPException(400, "未知工作流")
    wcfg = WORKFLOWS[wf_name]
    width = height = None
    if wcfg.get("sizes"):
        size = size or wcfg["sizes"][0]
        if size not in wcfg["sizes"]:
            raise HTTPException(400, "非法尺寸")
        width, height = map(int, size.split("x"))
    # LoRA selection/binding 校验；客户端 injected_tags/file 不可信，按 ID + revision 重解析。
    resolved_bindings: list[dict] = []
    lora_warnings: list[str] = []
    resolved_revision: str | None = None
    binding_requests = None
    if lora_bindings:
        if not isinstance(lora_bindings, list):
            raise HTTPException(400, "lora_bindings 必须是数组")
        binding_requests = _bindings_as_selections(
            [b for b in lora_bindings if isinstance(b, dict)])
    elif lora_selections:
        binding_requests = lora_selections
    if binding_requests:
        if "lora_node" not in wcfg:
            raise HTTPException(400, "该工作流不支持 LoRA")
        resolved_bindings, lora_warnings, resolved_revision = resolve_lora_selections(
            binding_requests,
            expected_revision=registry_revision if lora_bindings else None,
        )
        for sv in (strength_char, strength_style):
            if sv is not None:
                try:
                    sv = float(sv)
                except (TypeError, ValueError):
                    raise HTTPException(400, "LoRA 强度需为数字")
                if not (0 <= sv <= 1):
                    raise HTTPException(400, "LoRA 强度需在 0~1 之间")
        prompt_en = compile_lora_bindings(prompt_en, resolved_bindings)
    else:
        strength_char = strength_style = None
    if len(prompt_en) > MAX_COMPILED_PROMPT_CHARS:
        raise HTTPException(400, f"编译后的提示词过长(>{MAX_COMPILED_PROMPT_CHARS})")
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
        "concept": concept, "completion_level": completion_level,
        "width": width, "height": height,
        "loras": [b["key"] for b in resolved_bindings] or None,
        "lora_bindings": resolved_bindings,
        "lora_warnings": lora_warnings,
        "registry_revision": resolved_revision,
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
    lora_selections = _extract_lora_selections(body)
    lora_bindings = body.get("lora_bindings") or None
    registry_revision = (body.get("registry_revision") or "").strip() or None
    concept = _normalize_optional_concept(body.get("concept"), "concept")
    completion_level = _normalize_completion_level(body.get("completion_level"))
    if not prompt_en or len(prompt_en) > MAX_PROMPT_EN_CHARS:
        raise HTTPException(400, f"提示词为空或过长(>{MAX_PROMPT_EN_CHARS})")
    if prompt_raw != prompt_en and len(prompt_raw) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"原始提示词过长(>{MAX_USER_PROMPT_CHARS})")
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
                            body.get("size"), lora_selections,
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, detailer,
                            lora_bindings=lora_bindings,
                            registry_revision=registry_revision,
                            concept=concept, completion_level=completion_level)
    job = JOBS[job_id]
    return {"id": job_id, "prompt_en": job["prompt_en"],
            "lora_bindings": job.get("lora_bindings", []),
            "lora_warnings": job.get("lora_warnings", []),
            "registry_revision": job.get("registry_revision"),
            "concept": job.get("concept"),
            "completion_level": job.get("completion_level")}

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, token: str = Depends(auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    queued_ids = list(QUEUE._queue)  # MVP 简单读取
    resp = {k: job[k] for k in (
        "id", "status", "prompt_raw", "prompt_en", "workflow",
        "concept", "completion_level", "lora_bindings", "lora_warnings",
        "registry_revision") if k in job}
    if job["status"] == "queued":
        resp["position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 1
    if job["status"] == "done":
        resp["image"] = job["image"]
    if job["status"] == "failed":
        resp["error"] = job.get("error", "未知错误")
    return resp

@app.post("/api/dialog/turn")
async def dialog_turn(req: Request, token: str = Depends(auth)):
    """⑤ 对话迭代: 每轮一次出图 (走 _enqueue/worker, 计入日限). action:
    start=建会话+首图; redo(换一版)=delta 有则 raw+=delta 重翻译, 无则复用 current_en 换 seed;
    vibe(保氛围)=上一张图走 iterate 视觉全量提取(锁主体+氛围)再变体. 显式路由不猜意图, 见 D25."""
    body = await req.json()
    action = (body.get("action") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    delta = (body.get("delta") or "").strip()
    if len(delta) > MAX_DIALOG_DELTA_CHARS:
        raise HTTPException(400, f"改动描述过长(>{MAX_DIALOG_DELTA_CHARS})")
    wf_name = body.get("workflow", "")
    image_filename = None
    denoise = None
    requested_lora_selections = _extract_lora_selections(body)

    if action == "start":
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > MAX_USER_PROMPT_CHARS:
            raise HTTPException(400, f"提示词为空或过长(>{MAX_USER_PROMPT_CHARS})")
        completion_level = _normalize_completion_level(body.get("completion_level"))
        check_banned(prompt)
        try:
            prompt_en, _, _, translate_meta = await translate(
                prompt, lora_selections=requested_lora_selections, include_meta=True,
                completion_level=completion_level)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"翻译失败, 请稍后重试 ({e})")
        check_banned(prompt_en)
        session_id = uuid.uuid4().hex[:10]
        session_bindings = translate_meta.get("lora_bindings", [])
        SESSIONS[session_id] = {
            "id": session_id, "token": token, "raw": prompt,
            "current_en": prompt_en, "created": time.time(), "turns": [],
            "concept": translate_meta.get("concept"),
            "completion_level": completion_level,
            "lora_selections": _bindings_as_selections(session_bindings),
            "lora_bindings": session_bindings,
            "lora_warnings": translate_meta.get("lora_warnings", []),
            "registry_revision": translate_meta.get("registry_revision"),
        }
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
            "concept": src_job.get("concept"),
            "completion_level": _normalize_completion_level(
                src_job.get("completion_level")),
            "lora_selections": _bindings_as_selections(src_job.get("lora_bindings")),
            "lora_bindings": src_job.get("lora_bindings", []),
            "lora_warnings": src_job.get("lora_warnings", []),
            "registry_revision": src_job.get("registry_revision"),
            "turns": [{"job_id": src_job_id, "action": "start-image", "delta": "",
                       "prompt_en": src_job.get("prompt_en", ""),
                       "concept": src_job.get("concept"),
                       "completion_level": _normalize_completion_level(
                           src_job.get("completion_level"))}],
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
                    for name in _character_names():
                        if name in session["raw"]:
                            session["raw"] = session["raw"].replace(name, "")
                    session["raw"] = re.sub(r"[,，\s]+", " ", session["raw"]).strip()
                session["raw"] = (session["raw"] + "，" + delta) if session["raw"] else delta
                try:
                    prompt_en, _, _, translate_meta = await translate(
                        session["raw"], lora_selections=session.get("lora_selections", []),
                        include_meta=True,
                        completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                    session["concept"] = translate_meta.get("concept")
                    session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                    session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                    session["registry_revision"] = translate_meta.get("registry_revision")
                    session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
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
                prompt_en, _, _, translate_meta = await translate(
                    delta, image_b64=image_b64,
                    lora_selections=session.get("lora_selections", []), include_meta=True,
                    completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                # 视觉路径没有 Composer CONCEPT；不要把上一轮构思错误归因给新 Prompt。
                session["concept"] = translate_meta.get("concept")
                session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                session["registry_revision"] = translate_meta.get("registry_revision")
                session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
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
                    prompt_en, _, _, translate_meta = await translate(
                        session["raw"], lora_selections=session.get("lora_selections", []),
                        include_meta=True,
                        completion_level=session.get("completion_level", DEFAULT_COMPLETION_LEVEL))
                    session["concept"] = translate_meta.get("concept")
                    session["lora_bindings"] = translate_meta.get("lora_bindings", [])
                    session["lora_warnings"] = translate_meta.get("lora_warnings", [])
                    session["registry_revision"] = translate_meta.get("registry_revision")
                    session["lora_selections"] = _bindings_as_selections(session["lora_bindings"])
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
    session = SESSIONS[session_id]
    job_id = await _enqueue(token, wf_name, prompt_en, raw,
                            body.get("size"), session.get("lora_selections", []),
                            body.get("strength_char"), body.get("strength_style"),
                            image_filename, denoise, body.get("detailer"),
                            lora_bindings=session.get("lora_bindings"),
                            registry_revision=session.get("registry_revision"),
                            concept=session.get("concept"),
                            completion_level=session.get(
                                "completion_level", DEFAULT_COMPLETION_LEVEL))
    session["turns"].append({"job_id": job_id, "action": action, "delta": delta,
                             "prompt_en": JOBS[job_id]["prompt_en"],
                             "concept": JOBS[job_id].get("concept"),
                             "completion_level": JOBS[job_id].get("completion_level")})
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
            "concept": t.get("concept"),
            "completion_level": t.get("completion_level"),
            "status": job.get("status", "?"), "image": job.get("image"), "error": job.get("error"),
        })
    return {"session_id": session_id, "raw": session["raw"], "current_en": session["current_en"],
            "concept": session.get("concept"),
            "completion_level": session.get("completion_level", DEFAULT_COMPLETION_LEVEL),
            "lora_bindings": session.get("lora_bindings", []),
            "lora_warnings": session.get("lora_warnings", []),
            "registry_revision": session.get("registry_revision"), "turns": turns}
