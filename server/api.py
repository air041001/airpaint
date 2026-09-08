"""FastAPI 组装、鉴权、任务队列与对话会话。"""
import asyncio
import base64
import copy
import hashlib
import json
import os
import random
import re
import struct
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.lora import (
    _bindings_as_selections,
    compile_lora_bindings,
    get_lora_registry,
    resolve_lora_selections,
)
from server.prompt_engine import _IR_FIELDS, _normalize_optional_concept, _validate_prompt_ir, translate
from server.persistence import OwnershipError, QuotaExceeded, local_day, redact_secrets
from server.runtime import CLIENT, IDENTITY, IMAGES, JOBS, QUEUE, SESSIONS, SOURCE_IMAGES, STORE, USAGE
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
    MAX_REFERENCE_IMAGE_CHARS,
    MAX_SOURCE_IMAGE_CHARS,
    TOKENS,
    WORKFLOWS,
    _normalize_completion_level,
    normalize_reference_scope,
    normalize_image_fit,
    normalize_denoise,
)
from server.workflow_engine import (
    ComfyRejected,
    ComfyResultUnavailable,
    ComfySubmissionUncertain,
    ComfyUnavailable,
    build_prompt,
    inspect_comfy_prompt,
    retrieve_comfy_result,
    submission_payload,
    submit_to_comfy,
    upload_image_to_comfy,
)


app = FastAPI(title="AirPaint")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CFG.get("allow_origins", ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.mount("/lora-previews", StaticFiles(directory=LORA_PREVIEWS), name="lora-previews")
WEB_DIR = BASE.parent / "web"
AUTH_COOKIE = "airpaint_session"
_ALLOWED_OWNER_IDS = {IDENTITY.for_token(token) for token in TOKENS}
_worker_task: asyncio.Task | None = None
_queued_for_worker: set[str] = set()


@app.get("/", include_in_schema=False)
async def index():
    # no-cache: 前端改了 (如修 fail-to-fetch) 后, 访客浏览器永远拿最新 HTML, 不被旧缓存卡住
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

def _header_token(req: Request) -> str:
    return req.headers.get("authorization", "").removeprefix("Bearer ").strip()


def verify_token(req: Request) -> str:
    """Return a stable owner id from a valid invite header or signed cookie."""
    token = _header_token(req)
    if token and token in TOKENS:
        return IDENTITY.for_token(token)
    cookie = req.cookies.get(AUTH_COOKIE, "")
    owner_id = IDENTITY.verify_cookie(cookie, allowed_owner_ids=_ALLOWED_OWNER_IDS)
    if owner_id:
        return owner_id
    raise HTTPException(401, "邀请码无效或登录已过期")


def auth(req: Request) -> str:
    """Compatibility alias; generation quota is checked only in SQLite."""
    return verify_token(req)


def _set_auth_cookie(req: Request, response: Response, owner_id: str) -> None:
    forwarded = req.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    secure = req.url.scheme == "https" or forwarded == "https"
    response.set_cookie(
        AUTH_COOKIE,
        IDENTITY.issue_cookie(owner_id),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _cache_job(job: dict) -> dict:
    JOBS[job["id"]] = job
    return job


def _load_job(job_id: str, owner_id: str | None = None) -> dict | None:
    job = STORE.get_job(job_id, owner_id)
    if job:
        _cache_job(job)
    return job


async def _schedule(job_id: str) -> None:
    if job_id in _queued_for_worker:
        return
    _queued_for_worker.add(job_id)
    await QUEUE.put(job_id)


def _job_source_path(job: dict) -> Path | None:
    reference = job.get("source_image_ref")
    if not reference:
        return None
    candidate = SOURCE_IMAGES / Path(reference).name
    if candidate.name != reference or not candidate.is_file():
        return None
    return candidate


def _job_request(job: dict, image_filename: str | None) -> dict:
    built = build_prompt(
        job["workflow"], job["prompt_en"], job.get("width"), job.get("height"),
        job.get("loras"), job.get("strength_char"), job.get("strength_style"),
        image_filename, job.get("denoise"), job.get("detailer"),
        lora_bindings=job.get("lora_bindings"),
        registry_revision=job.get("registry_revision"), seed=job["seed"],
        fit_mode=job.get("fit_mode", "preserve"),
        crop_position=job.get("crop_position", "center"),
    )
    prompt_id = job.get("comfy_prompt_id") or str(uuid.uuid4())
    return submission_payload(built, prompt_id, job["id"])


async def _finish_from_comfy(job: dict) -> dict:
    prompt_id = job.get("comfy_prompt_id")
    if not prompt_id:
        raise ComfyResultUnavailable("任务没有 ComfyUI prompt_id，无法取回结果")
    destination = f"{job['id']}.png"
    saved, output = await retrieve_comfy_result(prompt_id, destination)
    now = time.time()
    job.update(
        status="done", output_image_ref=saved, comfy_output=output,
        completed_at=now, updated_at=now, error_kind=None, error_message=None,
    )
    return _cache_job(STORE.save_job(job))


async def _monitor_submitted(job: dict) -> None:
    prompt_id = job.get("comfy_prompt_id")
    if not prompt_id:
        job.update(
            status="reconcile_pending", error_kind="missing_prompt_id",
            error_message="任务缺少 ComfyUI 编号，不能安全重发",
        )
        _cache_job(STORE.save_job(job))
        return
    deadline = time.time() + int(CFG.get("timeout_seconds", 300))
    while True:
        try:
            evidence = await inspect_comfy_prompt(prompt_id)
        except ComfyUnavailable as exc:
            job.update(
                status="reconcile_pending", error_kind="comfy_unreachable",
                error_message=str(exc),
            )
            _cache_job(STORE.save_job(job))
            return
        state = evidence["state"]
        if state == "failed":
            now = time.time()
            job.update(
                status="failed", failed_at=now, error_kind="execution_failed",
                error_message=evidence.get("error") or "ComfyUI 执行失败",
            )
            _cache_job(STORE.save_job(job))
            return
        if state == "result_ready":
            job.update(status="result_ready", comfy_output=evidence.get("entry"))
            _cache_job(STORE.save_job(job))
            try:
                await _finish_from_comfy(job)
            except (ComfyUnavailable, ComfyResultUnavailable) as exc:
                job.update(
                    status="result_ready", error_kind="result_download_failed",
                    error_message=str(exc),
                )
                _cache_job(STORE.save_job(job))
            return
        if state in {"running", "submitted"}:
            job.update(status=state, error_kind=None, error_message=None)
            _cache_job(STORE.save_job(job))
        elif state == "missing" and time.time() >= deadline:
            job.update(
                status="reconcile_pending", error_kind="comfy_job_missing",
                error_message="ComfyUI 队列和历史都找不到该编号；为避免重复生成，未自动重发",
            )
            _cache_job(STORE.save_job(job))
            return
        if time.time() >= deadline and job.get("status") != "result_pending":
            job.update(
                status="result_pending", error_kind="wait_timeout",
                error_message="等待已超时，但 GPU 任务可能仍在运行；服务会继续按 ComfyUI 编号核对",
            )
            _cache_job(STORE.save_job(job))
        await asyncio.sleep(2 if time.time() < deadline else 10)


async def _submit_queued(job: dict) -> None:
    image_filename = job.get("comfy_image_filename")
    source_path = _job_source_path(job)
    if job.get("source_image_ref") and not source_path:
        now = time.time()
        job.update(
            status="failed", failed_at=now, error_kind="source_image_missing",
            error_message="原图文件缺失，任务没有提交到 ComfyUI",
        )
        _cache_job(STORE.save_job(job))
        return
    if source_path and not image_filename:
        try:
            image_filename = await upload_image_to_comfy(
                source_path.read_bytes(), f"airpaint-{job['id']}.png"
            )
        except ComfyUnavailable as exc:
            job.update(
                status="waiting_for_comfy", error_kind="comfy_unreachable",
                error_message=str(exc),
            )
            _cache_job(STORE.save_job(job))
            await asyncio.sleep(5)
            await _schedule(job["id"])
            return
        except ComfyRejected as exc:
            now = time.time()
            job.update(
                status="failed", failed_at=now, error_kind="image_upload_rejected",
                error_message=str(exc),
            )
            _cache_job(STORE.save_job(job))
            return
        job["comfy_image_filename"] = image_filename

    request = _job_request(job, image_filename)
    now = time.time()
    job.update(
        status="dispatching", comfy_prompt_id=request["prompt_id"],
        dispatch_started_at=now, request_snapshot=redact_secrets(request),
        error_kind=None, error_message=None,
    )
    _cache_job(STORE.save_job(job))
    try:
        await submit_to_comfy(request)
    except ComfyUnavailable as exc:
        job.update(
            status="waiting_for_comfy", dispatch_started_at=None,
            error_kind="comfy_unreachable", error_message=str(exc),
        )
        _cache_job(STORE.save_job(job))
        await asyncio.sleep(5)
        await _schedule(job["id"])
        return
    except ComfySubmissionUncertain as exc:
        job.update(
            status="reconcile_pending", error_kind="submission_uncertain",
            error_message=str(exc),
        )
        _cache_job(STORE.save_job(job))
        return
    except ComfyRejected as exc:
        now = time.time()
        job.update(
            status="failed", failed_at=now, error_kind="submission_rejected",
            error_message=str(exc),
        )
        _cache_job(STORE.save_job(job))
        return
    job.update(status="submitted", submitted_at=time.time())
    _cache_job(STORE.save_job(job))
    await _monitor_submitted(job)


async def worker():
    while True:
        job_id = await QUEUE.get()
        _queued_for_worker.discard(job_id)
        try:
            job = _load_job(job_id)
            if not job:
                continue
            if job["status"] in {"queued", "waiting_for_comfy"}:
                await _submit_queued(job)
            elif job["status"] in {
                "dispatching", "submitted", "running", "result_pending",
                "result_ready", "reconcile_pending",
            }:
                await _monitor_submitted(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job = _load_job(job_id)
            if job:
                now = time.time()
                job.update(
                    status="failed", failed_at=now, error_kind="airpaint_internal",
                    error_message=f"AirPaint 内部错误: {exc}",
                )
                _cache_job(STORE.save_job(job))
        finally:
            QUEUE.task_done()


async def _restore_scheduler() -> int:
    jobs = STORE.recoverable_jobs()
    jobs.sort(key=lambda item: (
        item["status"] in {"queued", "waiting_for_comfy"}, item["created_at"]
    ))
    for job in jobs:
        _cache_job(job)
        await _schedule(job["id"])
    return len(jobs)


@app.on_event("startup")
async def _startup():
    global _worker_task
    await _restore_scheduler()
    _worker_task = asyncio.create_task(worker(), name="airpaint-single-worker")


@app.on_event("shutdown")
async def _shutdown():
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    STORE.checkpoint()
    await CLIENT.aclose()
    STORE.close()


@app.get("/api/images/{filename}")
async def protected_image(filename: str, owner_id: str = Depends(verify_token)):
    safe_name = Path(filename).name
    if safe_name != filename or not STORE.output_owned_by(owner_id, safe_name):
        raise HTTPException(404, "图片不存在")
    path = IMAGES / safe_name
    if not path.is_file():
        raise HTTPException(404, "图片文件不存在")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

def check_banned(text: str):
    low = text.lower()
    for w in BANNED:
        if w in low:
            raise HTTPException(400, f"提示词包含被禁止的内容: {w}")

@app.get("/api/health")
async def health():
    try:
        r = await CLIENT.get(f"{COMFY}/system_stats", timeout=5)
        comfy_ok = r.status_code == 200
    except Exception:
        comfy_ok = False
    return {
        "ok": True,
        "comfy": comfy_ok,
        "database": STORE.integrity_check() == "ok",
        "schema_version": STORE.schema_version,
    }

@app.get("/api/auth/check")
async def auth_check(req: Request, response: Response, owner_id: str = Depends(verify_token)):
    """Validate login and refresh an HttpOnly cookie; no quota is consumed."""
    _set_auth_cookie(req, response, owner_id)
    return {"ok": True, "daily_used": STORE.usage_count(owner_id), "daily_limit": DAILY_LIMIT}


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE, path="/")
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


def _request_image(body: dict, field: str, *, legacy: bool = False) -> str:
    value = body.get(field)
    if value is None and legacy:
        value = body.get("image")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, f"{field} 必须是 base64 字符串")
    limit = MAX_SOURCE_IMAGE_CHARS if field == "source_image" else MAX_REFERENCE_IMAGE_CHARS
    if len(value) > limit:
        raise HTTPException(400, f"{field} 过大")
    return value.strip()


def _decode_image(value: str) -> bytes:
    payload = value.partition(",")[2] if value.startswith("data:") else value
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(400, "图片不是有效 base64")
    if not data or not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8")
                        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
        raise HTTPException(400, "图片必须是 PNG、JPEG 或 WebP")
    return data


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    """读取 PNG/JPEG 尺寸用于提示，不改变上传图片或工作流画幅。"""
    if data.startswith(b"\x89PNG") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9):
                continue
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3) and length >= 7:
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return width, height
            offset += length
    return None


def _normalize_seed(value) -> int:
    if value is None:
        return random.randint(1, 2**31 - 1)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise HTTPException(400, "seed 必须是 1~2^63-1 的整数")
    return value


def _request_ir(value) -> dict | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != set(_IR_FIELDS)
            or any(not isinstance(items, list) or any(not isinstance(x, str) for x in items)
                   for items in value.values()) or len(json.dumps(value)) > 24000):
        raise HTTPException(400, "prompt_ir 必须是完整十二字段字符串数组")
    return _validate_prompt_ir(value)


_SNAPSHOT_FIELDS = (
    "prompt_raw", "prompt_en", "prompt_ir", "prompt_edited", "concept", "completion_level",
    "lora_bindings", "registry_revision", "width", "height", "seed", "parent_job_id",
    "generation_mode", "denoise", "fit_mode", "crop_position", "change_fields",
    "reference_contract", "reference_scope", "detailer",
)


def _state_snapshot(job: dict) -> dict:
    return copy.deepcopy({key: job.get(key) for key in _SNAPSHOT_FIELDS})


def _public_job(job: dict) -> dict:
    keys = ("id", "status", "prompt_raw", "prompt_en", "workflow", "concept", "completion_level",
            "lora_bindings", "lora_warnings", "registry_revision", "prompt_ir", "seed",
            "parent_job_id", "generation_mode", "denoise", "fit_mode", "crop_position",
            "width", "height", "state_snapshot", "change_fields", "image_warnings",
            "reference_contract", "reference_scope", "image", "error", "error_kind",
            "error_message", "comfy_prompt_id", "created_at", "queued_at", "submitted_at",
            "completed_at", "failed_at", "session_id", "idempotent_replay")
    return {key: job[key] for key in keys if key in job}


_CLIENT_REQUEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def _client_request_id(req: Request, body: dict) -> str:
    headers = getattr(req, "headers", {}) or {}
    value = body.get("client_request_id") or headers.get("idempotency-key") or uuid.uuid4().hex
    if not isinstance(value, str) or not _CLIENT_REQUEST_RE.fullmatch(value.strip()):
        raise HTTPException(400, "client_request_id 必须为 8~128 位字母、数字或 . _ : -")
    return value.strip()


def _request_fingerprint(body: dict) -> str:
    normalized = redact_secrets(copy.deepcopy(body))
    normalized.pop("client_request_id", None)
    for key in ("image", "reference_image", "source_image"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _idempotent_existing(owner_id: str, request_id: str, fingerprint: str) -> dict | None:
    existing = STORE.get_job_by_request(owner_id, request_id)
    if not existing:
        return None
    previous = existing.get("request_fingerprint")
    if previous and previous != fingerprint:
        raise HTTPException(409, "client_request_id 已用于不同请求")
    existing["idempotent_replay"] = True
    return _cache_job(existing)

@app.post("/api/translate")
async def translate_prompt(req: Request, token: str = Depends(verify_token)):
    """只编译不排队: 中文构思 -> Anima Prompt，不计入 image 限额。
    返回 {concept, prompt_en, breakdown, prompt_ir, prompt_ir_meta}；completion_level 控制补全幅度，
    concept_override 可把用户编辑后的构思重新编译为 Prompt。
    body.reroll=true: LLM 高温重出一版不同画师补全方案 (抽卡再抽, 跳过缓存, 见 D19).
    reference_image/source_image 经 Vision 生成参考契约，再交 Composer；image 保留兼容。"""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    source_image = _request_image(body, "source_image")
    image = _request_image(body, "reference_image", legacy=not bool(source_image))
    if source_image and image:
        raise HTTPException(400, "reference_image 和 source_image 不能同时提供")
    scope = normalize_reference_scope(body.get("reference_scope"))
    if source_image:
        image, scope = source_image, "full"
    if not prompt and not image:
        raise HTTPException(400, "提示词和参考图不能同时为空")
    if len(prompt) > MAX_USER_PROMPT_CHARS:
        raise HTTPException(400, f"提示词过长(>{MAX_USER_PROMPT_CHARS})")
    if image:
        _decode_image(image)
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
        reference_scope=scope, source_image=bool(source_image),
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
        "reference_contract": prompt_ir_meta.get("reference_contract"),
        "reference_scope": prompt_ir_meta.get("reference_scope"),
    }

async def _enqueue(owner_id: str, wf_name: str, prompt_en: str, prompt_raw: str,
                   size, lora_selections, strength_char, strength_style,
                   image_filename: str | None = None, denoise: float | None = None,
                   detailer: dict | None = None,
                   lora_bindings: list[dict] | None = None,
                   registry_revision: str | None = None,
                   concept: str | None = None,
                   completion_level: str = DEFAULT_COMPLETION_LEVEL,
                   prompt_ir: dict | None = None, seed: int | None = None,
                   fit_mode: str = "preserve", crop_position: str = "center",
                   parent_job_id: str | None = None, generation_mode: str | None = None,
                   change_fields: list[str] | None = None, prompt_edited: bool = False,
                   reference_contract: dict | None = None, reference_scope: str | None = None,
                   source_dimensions: tuple[int, int] | None = None,
                   source_image_bytes: bytes | None = None,
                   client_request_id: str | None = None,
                   request_fingerprint: str | None = None,
                   session: dict | None = None,
                   turn: dict | None = None) -> str:
    """Validate and atomically persist one generation before scheduling it."""
    completion_level = _normalize_completion_level(completion_level)
    concept = _normalize_optional_concept(concept, "concept")
    prompt_ir = _request_ir(prompt_ir)
    if reference_contract is not None:
        if not isinstance(reference_contract, dict):
            raise HTTPException(400, "reference_contract 必须是对象")
        reference_scope = normalize_reference_scope(reference_scope or reference_contract.get("scope"))
        reference_contract = {
            "scope": reference_scope, "fields": _request_ir(reference_contract.get("fields")),
            "source_model": str(reference_contract.get("source_model") or "")[:200],
        }
    seed = _normalize_seed(seed)
    fit_mode, crop_position = normalize_image_fit(fit_mode, crop_position)
    has_source = bool(image_filename or source_image_bytes)
    denoise = normalize_denoise(denoise, required=has_source)
    if denoise is not None and not has_source:
        raise HTTPException(400, "denoise 只能用于 Img2Img")
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
        for binding in resolved_bindings:
            override = strength_char if binding.get("type") == "character" else strength_style
            if override is not None:
                binding["strength_model"] = binding["strength_clip"] = float(override)
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
    image_warnings = []
    if source_dimensions and width and height and all(source_dimensions):
        ratio = source_dimensions[0] / source_dimensions[1]
        if abs(ratio / (width / height) - 1) > 0.03:
            sizes = wcfg.get("sizes") or []
            nearest = min(sizes, key=lambda s: abs((int(s.split("x")[0]) / int(s.split("x")[1])) / ratio - 1)) if sizes else None
            image_warnings.append(f"原图比例与 {width}×{height} 不同，将按{'裁切' if fit_mode == 'crop' else '保留全图并扩边'}处理；最近画幅为 {nearest}。尺寸未自动改变。")
    job_id = uuid.uuid4().hex[:10]
    created_at = time.time()
    session_id = session.get("id") if session else None
    source_image_ref = f"{job_id}.png" if source_image_bytes else None
    job = {
        "id": job_id, "owner_id": owner_id, "client_request_id": client_request_id,
        "request_fingerprint": request_fingerprint,
        "workflow": wf_name,
        "prompt_raw": prompt_raw, "prompt_en": prompt_en,
        "concept": concept, "completion_level": completion_level,
        "width": width, "height": height,
        "loras": [b["key"] for b in resolved_bindings] or None,
        "lora_bindings": resolved_bindings,
        "lora_warnings": lora_warnings,
        "registry_revision": resolved_revision,
        "strength_char": strength_char, "strength_style": strength_style,
        "source_image_ref": source_image_ref,
        "comfy_image_filename": image_filename, "denoise": denoise,
        "detailer": detailer,
        "prompt_ir": prompt_ir, "prompt_edited": bool(prompt_edited), "seed": seed,
        "parent_job_id": parent_job_id, "session_id": session_id,
        "generation_mode": generation_mode or ("img2img" if has_source else "txt2img"),
        "fit_mode": fit_mode, "crop_position": crop_position,
        "change_fields": change_fields or [], "image_warnings": image_warnings,
        "reference_contract": copy.deepcopy(reference_contract), "reference_scope": reference_scope,
        "status": "queued", "created": created_at, "created_at": created_at,
        "queued_at": created_at, "updated_at": created_at,
    }
    job["state_snapshot"] = _state_snapshot(job)

    if session:
        _update_session(session, job)
        if turn is not None:
            turn = copy.deepcopy(turn)
            turn["session_state"] = {
                key: copy.deepcopy(value)
                for key, value in session.items()
                if key not in {"turns", "owner_id"}
            }

    source_path = SOURCE_IMAGES / source_image_ref if source_image_ref else None
    if source_path:
        temporary = SOURCE_IMAGES / f".{source_image_ref}.{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(source_image_bytes)
        os.replace(temporary, source_path)
    try:
        saved, created, count = STORE.create_generation(
            job, daily_limit=DAILY_LIMIT, usage_date=local_day(created_at),
            session=session, turn=turn,
        )
    except QuotaExceeded as exc:
        if source_path:
            source_path.unlink(missing_ok=True)
        raise HTTPException(429, str(exc))
    except OwnershipError as exc:
        if source_path:
            source_path.unlink(missing_ok=True)
        raise HTTPException(404, str(exc))
    except Exception:
        if source_path:
            source_path.unlink(missing_ok=True)
        raise

    # Compatibility mirror only; SQLite remains authoritative.
    USAGE[owner_id] = [local_day(created_at), count]
    if not created:
        if source_path:
            source_path.unlink(missing_ok=True)
        saved["idempotent_replay"] = True
        _cache_job(saved)
        return saved["id"]
    _cache_job(saved)
    if session and turn is not None:
        session.setdefault("turns", []).append({
            "job_id": job_id,
            "action": turn["action"],
            "delta": turn.get("delta", ""),
            "created_at": created_at,
        })
        SESSIONS[session["id"]] = copy.deepcopy(session)
    await _schedule(job_id)
    return job_id

@app.post("/api/jobs")
async def create_job(req: Request, owner_id: str = Depends(auth)):
    body = await req.json()
    client_request_id = _client_request_id(req, body)
    request_fingerprint = _request_fingerprint(body)
    existing = _idempotent_existing(owner_id, client_request_id, request_fingerprint)
    if existing:
        return _public_job(existing)
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
    # Source bytes are saved locally before enqueue. Uploading to ComfyUI happens
    # in the worker, so a queued task survives an AirPaint restart.
    image_b64 = _request_image(body, "source_image", legacy=True)
    denoise = normalize_denoise(body.get("denoise"), required=bool(image_b64))
    if denoise is not None and not image_b64:
        raise HTTPException(400, "denoise 只能用于 Img2Img")
    fit_mode, crop_position = normalize_image_fit(body.get("fit_mode"), body.get("crop_position"))
    seed = _normalize_seed(body.get("seed"))
    prompt_ir = _request_ir(body.get("prompt_ir"))
    detailer = body.get("detailer")
    image_bytes = None
    source_dimensions = None
    if image_b64:
        image_bytes = _decode_image(image_b64)
        source_dimensions = _image_dimensions(image_bytes)
    job_id = await _enqueue(owner_id, wf_name, prompt_en, prompt_raw,
                            body.get("size"), lora_selections,
                            body.get("strength_char"), body.get("strength_style"),
                            None, denoise, detailer,
                            lora_bindings=lora_bindings,
                            registry_revision=registry_revision,
                            concept=concept, completion_level=completion_level,
                            prompt_ir=prompt_ir, seed=seed, fit_mode=fit_mode,
                            crop_position=crop_position, source_dimensions=source_dimensions,
                            prompt_edited=bool(body.get("prompt_edited")),
                            reference_contract=body.get("reference_contract"),
                            reference_scope=body.get("reference_scope"),
                            source_image_bytes=image_bytes,
                            client_request_id=client_request_id,
                            request_fingerprint=request_fingerprint)
    replayed = bool(JOBS.get(job_id, {}).get("idempotent_replay"))
    job = _load_job(job_id, owner_id)
    if replayed:
        job["idempotent_replay"] = True
    return _public_job(job)

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, owner_id: str = Depends(verify_token)):
    job = _load_job(job_id, owner_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    queued_ids = list(QUEUE._queue)  # MVP 简单读取
    resp = _public_job(job)
    if job["status"] == "queued":
        resp["position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 1
    return resp


@app.post("/api/jobs/{job_id}/recover")
async def recover_job_result(job_id: str, owner_id: str = Depends(verify_token)):
    """Reconcile or re-download an existing generation without charging quota."""
    job = _load_job(job_id, owner_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"] == "done":
        return _public_job(job)
    if job["status"] == "result_ready":
        try:
            job = await _finish_from_comfy(job)
        except (ComfyUnavailable, ComfyResultUnavailable) as exc:
            job.update(
                error_kind="result_download_failed", error_message=str(exc),
            )
            job = _cache_job(STORE.save_job(job))
        return _public_job(job)
    if job["status"] in {
        "queued", "waiting_for_comfy", "dispatching", "submitted", "running",
        "result_pending", "reconcile_pending",
    }:
        await _schedule(job_id)
        return _public_job(job)
    raise HTTPException(409, "该任务没有可恢复的生成或结果")


@app.get("/api/history")
async def history(limit: int = 20, cursor: str | None = None,
                  owner_id: str = Depends(verify_token)):
    try:
        decoded = STORE.decode_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    jobs, next_cursor = STORE.list_jobs(owner_id, limit=limit, cursor=decoded)
    return {
        "items": [
            _public_job(job) | {"session_ids": STORE.session_ids_for_job(job["id"], owner_id)}
            for job in jobs
        ],
        "next_cursor": next_cursor,
        "daily_used": STORE.usage_count(owner_id),
        "daily_limit": DAILY_LIMIT,
    }


@app.get("/api/requests/{client_request_id}")
async def request_status(client_request_id: str, owner_id: str = Depends(verify_token)):
    if not _CLIENT_REQUEST_RE.fullmatch(client_request_id):
        raise HTTPException(400, "client_request_id 无效")
    job = STORE.get_job_by_request(owner_id, client_request_id)
    if not job:
        raise HTTPException(404, "服务器没有记录该请求")
    return _public_job(_cache_job(job))

def _session_source(session: dict, source_job_id: str | None, owner_id: str) -> dict:
    turn_ids = [turn["job_id"] for turn in session["turns"]]
    if not source_job_id:
        source_job_id = next((job_id for job_id in reversed(turn_ids)
                              if (_load_job(job_id) or {}).get("status") == "done"), None)
    if source_job_id not in turn_ids:
        raise HTTPException(400, "source_job_id 必须属于当前会话")
    source = _load_job(source_job_id, owner_id)
    if not source:
        raise HTTPException(404, "源任务不存在")
    if source.get("status") != "done" or not source.get("image"):
        raise HTTPException(400, "只能从已完成的图片继续")
    return source


def _new_session(owner_id: str) -> dict:
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:10], "owner_id": owner_id,
        "created": now, "created_at": now, "turns": [],
    }


def _update_session(session: dict, job: dict):
    session.update({
        "raw": job["prompt_raw"], "current_en": job["prompt_en"],
        "concept": job.get("concept"), "completion_level": job.get("completion_level"),
        "lora_bindings": copy.deepcopy(job.get("lora_bindings", [])),
        "lora_selections": _bindings_as_selections(job.get("lora_bindings", [])),
        "lora_warnings": job.get("lora_warnings", []),
        "registry_revision": job.get("registry_revision"),
    })


@app.post("/api/dialog/turn")
async def dialog_turn(req: Request, owner_id: str = Depends(verify_token)):
    """显式换一版/微调，从所选父任务快照增量修改，不拼接历史 raw。"""
    body = await req.json()
    action = (body.get("action") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    delta = (body.get("delta") or "").strip()
    if len(delta) > MAX_DIALOG_DELTA_CHARS:
        raise HTTPException(400, f"改动描述过长(>{MAX_DIALOG_DELTA_CHARS})")
    if delta:
        check_banned(delta)
    if action == "start-image":
        src_job_id = (body.get("job_id") or body.get("source_job_id") or "").strip()
        src_job = _load_job(src_job_id, owner_id)
        if not src_job:
            raise HTTPException(404, "原图任务不存在")
        if src_job.get("status") != "done" or not src_job.get("image"):
            raise HTTPException(400, "原图还没生成完")
        session = _new_session(owner_id)
        _update_session(session, src_job)
        session["turns"].append({
            "job_id": src_job_id, "action": "start-image", "delta": "", "created_at": time.time(),
        })
        try:
            STORE.create_session_from_job(
                session, job_id=src_job_id, action="start-image", delta=""
            )
        except OwnershipError as exc:
            raise HTTPException(404, str(exc))
        SESSIONS[session["id"]] = copy.deepcopy(session)
        return {**_public_job(src_job), "session_id": session["id"], "job_id": src_job_id}

    client_request_id = _client_request_id(req, body)
    request_fingerprint = _request_fingerprint(body)
    existing = _idempotent_existing(owner_id, client_request_id, request_fingerprint)
    if existing:
        existing_session = existing.get("session_id") or STORE.session_for_job(existing["id"], owner_id)
        return {
            "session_id": existing_session, "job_id": existing["id"],
            **_public_job(existing),
        }

    source_image_bytes = None
    source_dimensions = None
    source = None
    fit_mode, crop_position = normalize_image_fit(body.get("fit_mode"), body.get("crop_position"))
    denoise = None
    seed = body.get("seed")
    prompt_edited = False
    if action == "start":
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > MAX_USER_PROMPT_CHARS:
            raise HTTPException(400, f"提示词为空或过长(>{MAX_USER_PROMPT_CHARS})")
        check_banned(prompt)
        completion_level = _normalize_completion_level(body.get("completion_level"))
        prompt_en, _, prompt_ir, meta = await translate(
            prompt, lora_selections=_extract_lora_selections(body), include_meta=True,
            completion_level=completion_level)
        session = _new_session(owner_id)
        raw = prompt
        wf_name = body.get("workflow") or next(iter(WORKFLOWS))
        size = body.get("size")
    elif action in {"redo", "tweak", "vibe"}:
        session = STORE.get_session(session_id, owner_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        source = _session_source(session, body.get("source_job_id"), owner_id)
        state = copy.deepcopy(source.get("state_snapshot") or _state_snapshot(source))
        bindings = source.get("lora_bindings", [])
        selections = _bindings_as_selections(bindings)
        if bindings:
            resolve_lora_selections(selections, expected_revision=source.get("registry_revision"))
        if any(key in body for key in ("lora_selections", "loras", "lora")):
            requested, _, _ = resolve_lora_selections(_extract_lora_selections(body))
            if _bindings_as_selections(requested) != selections:
                raise HTTPException(409, "当前迭代链固定 LoRA；更换 LoRA 请回工坊开启新生成链")
        completion_level = _normalize_completion_level(state.get("completion_level"))
        wf_name = body.get("workflow") or source["workflow"]
        size = body.get("size") or (f'{source["width"]}x{source["height"]}' if source.get("width") else None)
        raw = state.get("prompt_raw") or source["prompt_en"]
        prompt_en, prompt_ir = source["prompt_en"], state.get("prompt_ir")
        prompt_edited = bool(state.get("prompt_edited"))
        meta = {
            "concept": state.get("concept"), "lora_bindings": bindings,
            "lora_warnings": source.get("lora_warnings", []),
            "registry_revision": source.get("registry_revision"), "change_fields": [],
            "reference_contract": state.get("reference_contract"),
            "reference_scope": state.get("reference_scope"),
        }
        seed_strategy = body.get("seed_strategy") or ("fixed" if seed is not None else
                                                     "inherit" if action == "tweak" else "random")
        if seed_strategy not in {"inherit", "random", "fixed"}:
            raise HTTPException(400, "seed_strategy 必须是 inherit、random 或 fixed")
        if seed_strategy == "inherit":
            seed = source.get("seed")
        elif seed_strategy == "random":
            seed = None
        elif seed is None:
            raise HTTPException(400, "fixed 策略必须提供 seed")
        seed = _normalize_seed(seed)
        if action == "tweak":
            denoise = normalize_denoise(body.get("denoise"), required=True)
        if action == "vibe":
            image_path = IMAGES / source["image"].rsplit("/", 1)[-1]
            if not image_path.is_file():
                raise HTTPException(400, "源图片文件不在了")
            image_b64 = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()
            prompt_en, _, prompt_ir, meta = await translate(
                delta or raw, image_b64=image_b64, reference_scope="composition_vibe",
                lora_selections=selections, include_meta=True, completion_level=completion_level)
            prompt_edited = False
        elif delta:
            prompt_en, _, prompt_ir, meta = await translate(
                delta, prior_state=state, lora_selections=selections, include_meta=True,
                completion_level=completion_level)
            prompt_edited = False
        if action == "tweak":
            fit_mode, crop_position = normalize_image_fit(
                body.get("fit_mode", source.get("fit_mode")), body.get("crop_position", source.get("crop_position")))
            image_path = IMAGES / source["image"].rsplit("/", 1)[-1]
            if not image_path.is_file():
                raise HTTPException(400, "源图片文件不在了")
            source_image_bytes = image_path.read_bytes()
            source_dimensions = _image_dimensions(source_image_bytes)
    else:
        raise HTTPException(400, f"未知 action: {action}")

    check_banned(prompt_en)
    if source and _bindings_as_selections(meta.get("lora_bindings", [])) != selections:
        raise HTTPException(502, "增量修改改变了固定 LoRA binding，本次未入队；请重试或回工坊开启新链")
    job_id = await _enqueue(
        owner_id, wf_name, prompt_en, raw, size, None,
        None if source else body.get("strength_char"), None if source else body.get("strength_style"),
        None, denoise, body.get("detailer", source.get("detailer") if source else None),
        lora_bindings=meta.get("lora_bindings"), registry_revision=meta.get("registry_revision"),
        concept=meta.get("concept"), completion_level=completion_level, prompt_ir=prompt_ir,
        seed=seed, fit_mode=fit_mode, crop_position=crop_position,
        parent_job_id=source["id"] if source else None,
        generation_mode=action if source else "txt2img",
        change_fields=meta.get("change_fields"), prompt_edited=prompt_edited,
        reference_contract=meta.get("reference_contract"), reference_scope=meta.get("reference_scope"),
        source_dimensions=source_dimensions,
        source_image_bytes=source_image_bytes,
        client_request_id=client_request_id,
        request_fingerprint=request_fingerprint,
        session=session,
        turn={"action": action, "delta": delta},
    )
    replayed = bool(JOBS.get(job_id, {}).get("idempotent_replay"))
    job = _load_job(job_id, owner_id)
    if replayed:
        job["idempotent_replay"] = True
    return {"session_id": session["id"], "job_id": job_id, **_public_job(job)}


@app.get("/api/dialog/{session_id}")
async def dialog_get(session_id: str, owner_id: str = Depends(verify_token)):
    session = STORE.get_session(session_id, owner_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    SESSIONS[session_id] = copy.deepcopy(session)
    turns = []
    for turn in session["turns"]:
        job = _load_job(turn["job_id"], owner_id) or {}
        turns.append({**_public_job(job), "job_id": turn["job_id"],
                      "action": turn["action"], "delta": turn["delta"]})
    return {key: session.get(key) for key in (
        "raw", "current_en", "concept", "completion_level", "lora_bindings",
        "lora_warnings", "registry_revision"
    )} | {"session_id": session_id, "turns": turns}
