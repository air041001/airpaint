"""ComfyUI workflow 清洗、注入、提交与结果获取。"""
import asyncio
import json
import os
import random
import time
import uuid

import httpx
from fastapi import HTTPException

from server.lora import (
    _bindings_as_selections,
    compile_lora_bindings,
    resolve_lora_selections,
)
from server.runtime import CLIENT, CLIENT_ID, IMAGES
from server.settings import BASE, CFG, COMFY, WORKFLOWS, normalize_image_fit


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

def _workflow_lora_entries(bindings: list[dict], strength_char: float | None = None,
                           strength_style: float | None = None) -> list[dict]:
    """把语义 binding 压成物理文件加载表；同一 safetensors 最多加载一次。"""
    entries: list[dict] = []
    by_file: dict[str, tuple[float, float]] = {}
    for binding in bindings:
        lora_type = binding.get("type")
        legacy_strength = (strength_char if lora_type == "character"
                           else strength_style if lora_type in {"style", "action", "expression"}
                           else None)
        sm = float(legacy_strength) if legacy_strength is not None else float(
            binding.get("strength_model", 1.0))
        sc = float(legacy_strength) if legacy_strength is not None else float(
            binding.get("strength_clip", 1.0))
        for value in (sm, sc):
            if not 0 <= value <= 2:
                raise HTTPException(400, "LoRA 强度需在 0~2 之间")
        filename = str(binding.get("file") or "").strip()
        if not filename:
            raise HTTPException(500, f"LoRA {binding.get('key', '')} 缺少文件名")
        previous = by_file.get(filename)
        if previous is not None:
            if previous != (sm, sc):
                raise HTTPException(400, f"同一 LoRA 文件 {filename} 被以不同强度重复选择")
            continue
        by_file[filename] = (sm, sc)
        entries.append({"name": filename, "strength": sm,
                        "clipStrength": sc, "active": True})
    return entries

def build_prompt(wf_name: str, prompt_en: str, width: int | None, height: int | None,
                 lora_keys: list[str] | None = None,
                 strength_char: float | None = None, strength_style: float | None = None,
                 image_filename: str | None = None, denoise: float | None = None,
                 detailer: dict | None = None,
                 negative_text: str | None = None,
                 lora_bindings: list[dict] | None = None,
                 registry_revision: str | None = None,
                 seed: int | None = None,
                 fit_mode: str = "preserve",
                 crop_position: str = "center") -> dict:
    wcfg = WORKFLOWS[wf_name]
    wf = json.loads((BASE / wcfg["file"]).read_text(encoding="utf-8"))
    wf = sanitize_for_api(wf)
    if seed is None:
        seed = random.randint(1, 2**31 - 1)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 1 <= seed <= 2**63 - 1:
        raise HTTPException(400, "seed 必须是 1~2^63-1 的整数")
    fit_mode, crop_position = normalize_image_fit(fit_mode, crop_position)

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

    # LoRA Binding: 客户端只提供 key/profile/optional ID，exact tags/file/strength 重新从
    # 同 revision Registry 解析。Prompt 与 workflow 注入共享同一 binding snapshot (D39).
    effective_bindings: list[dict] = []
    if lora_bindings:
        selections = _bindings_as_selections(lora_bindings)
        effective_bindings, _, _ = resolve_lora_selections(
            selections, expected_revision=registry_revision)
    elif lora_keys:
        effective_bindings, _, registry_revision = resolve_lora_selections(lora_keys)
    if effective_bindings:
        if "lora_node" not in wcfg:
            raise HTTPException(400, f"工作流 {wf_name} 不支持 LoRA")
        lora_entries = _workflow_lora_entries(
            effective_bindings, strength_char=strength_char, strength_style=strength_style)
        set_input("lora_node", "loras", {"__value__": lora_entries})
        prompt_en = compile_lora_bindings(prompt_en, effective_bindings)

    # rating tag (safe/sensitive/questionable/explicit) 不再由关键词启发式推断。
    # 用户可在生成前编辑英文 Prompt 明确加入，后端原样保留。
    full_prompt = wcfg.get("quality_prefix", "") + prompt_en
    set_input("prompt_node", "text", full_prompt)
    if "negative_node" in wcfg:
        set_input("negative_node", "text", wcfg.get("negative_prefix", "") + wcfg.get("negative_extra", ""))
    if "seed_node" in wcfg:
        set_input("seed_node", "seed", seed)
    if width and height and "size_node" in wcfg:
        # 节点 56 的宽高原本连接到 easy int 39/47；txt2img 直接覆盖节点 56
        # 足够，但 img2img 的 Resize 31 仍读取 39/47。先同步共享上游数值，
        # 再覆盖 EmptyLatent 输入，保证两个分支使用同一请求尺寸且不切断 Resize 连接。
        size_node = wf.get(str(wcfg["size_node"])) or {}
        for field, value in (("width", width), ("height", height)):
            connection = (size_node.get("inputs") or {}).get(field)
            if isinstance(connection, list) and len(connection) == 2:
                upstream = wf.get(str(connection[0])) or {}
                upstream_inputs = upstream.get("inputs") or {}
                if upstream.get("class_type") == "easy int" and "value" in upstream_inputs:
                    upstream_inputs["value"] = value
        set_input("size_node", "width", width)
        set_input("size_node", "height", height)
    # ---- detailer 拼接 + txt2img/img2img 源切 (D32/D33) ----
    # 合并版工作流: 一份 AnimaFull.json 含 txt2img/img2img + 4 路 detailer；inpaint 已撤销。
    # build_prompt 按 detailer:{hand,nsfw,face,eyes} 删未选节点、重连, 真正的"拼接" (删的节点不执行, 省时).
    detailer_cfg = wcfg.get("detailer_nodes")
    if image_filename and "image_node" in wcfg:
        set_input("image_node", "image", image_filename)   # LoadImage: img2img 用
        resize_nodes = [
            node for node in wf.values()
            if node.get("class_type") == "ImageResizeKJv2"
        ]
        if len(resize_nodes) != 1:
            raise HTTPException(500, f"workflow {wf_name} 必须恰好包含一个 ImageResizeKJv2")
        resize_inputs = resize_nodes[0].get("inputs") or {}
        resize_inputs["keep_proportion"] = "crop" if fit_mode == "crop" else "pad_edge"
        resize_inputs["crop_position"] = crop_position if fit_mode == "crop" else "center"
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
    # 路由必须每次显式写入：原工作流节点 32 曾默认 value=2，若 txt2img 不覆盖，
    # 会误走 salt.jpg -> Resize -> VAEEncode，绕过节点 56 的尺寸并引发额外 VRAM 换入。
    if "switch_node" in wcfg:
        set_input("switch_node", "select", 2 if image_filename else 1)
    # img2img 注入: input2=VAEEncode latent + 覆盖主 KSampler denoise
    if image_filename and "image_node" in wcfg:
        if denoise is not None and "denoise_node" in wcfg:
            set_input("denoise_node", "denoise", float(denoise))
    return {"prompt": wf, "client_id": CLIENT_ID, "_seed": seed}

class ComfyUnavailable(RuntimeError):
    pass


class ComfySubmissionUncertain(RuntimeError):
    pass


class ComfyRejected(RuntimeError):
    pass


class ComfyExecutionFailed(RuntimeError):
    pass


class ComfyResultUnavailable(RuntimeError):
    pass


async def upload_image_to_comfy(image_bytes: bytes, filename: str | None = None) -> str:
    """上传图到 ComfyUI input 目录 (POST /upload/image), 返回文件名 (给 LoadImage set_input 用, 见 D26)."""
    fname = filename or f"{uuid.uuid4().hex[:12]}.png"
    try:
        r = await CLIENT.post(
            f"{COMFY}/upload/image",
            files={"image": (fname, image_bytes, "image/png")},
            data={"type": "input", "overwrite": "true"},
            timeout=30,
        )
    except httpx.RequestError as exc:
        raise ComfyUnavailable(f"无法连接 ComfyUI 上传图片: {exc}") from exc
    if r.status_code != 200:
        raise ComfyRejected(f"ComfyUI 上传图片失败: {r.status_code} {r.text[:200]}")
    return r.json()["name"]


def submission_payload(payload: dict, prompt_id: str, airpaint_job_id: str) -> dict:
    """Attach stable reconciliation identifiers to a sanitized Comfy request."""
    request = json.loads(json.dumps(payload))
    request.pop("_seed", None)
    request["prompt_id"] = str(prompt_id)
    extra = request.setdefault("extra_data", {})
    extra["airpaint_job_id"] = str(airpaint_job_id)
    return request


async def submit_to_comfy(payload: dict) -> str:
    """Submit once. A transport failure is ambiguous and must not be retried blindly."""
    expected = str(payload.get("prompt_id") or "")
    if not expected:
        raise ValueError("ComfyUI 请求缺少预分配 prompt_id")
    try:
        response = await CLIENT.post(f"{COMFY}/prompt", json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise ComfyUnavailable(f"无法连接 ComfyUI 提交任务: {exc}") from exc
    except httpx.RequestError as exc:
        raise ComfySubmissionUncertain(f"提交响应丢失，任务是否送达需要核对: {exc}") from exc
    if response.status_code != 200:
        raise ComfyRejected(f"ComfyUI 拒绝: {response.status_code} {response.text[:300]}")
    try:
        returned = str(response.json()["prompt_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ComfySubmissionUncertain("ComfyUI 已响应但没有返回有效 prompt_id") from exc
    if returned != expected:
        raise ComfySubmissionUncertain(
            f"ComfyUI 返回了不同的 prompt_id ({returned})，需要人工核对"
        )
    return returned


def _history_error(entry: dict) -> str:
    messages = (entry.get("status") or {}).get("messages") or []
    for item in reversed(messages):
        if isinstance(item, (list, tuple)) and len(item) > 1 and isinstance(item[1], dict):
            detail = item[1].get("exception_message") or item[1].get("exception_type")
            if detail:
                return f"ComfyUI 执行出错: {str(detail)[:300]}"
    return "ComfyUI 执行出错"


async def inspect_comfy_prompt(prompt_id: str) -> dict:
    """Return an evidence-based state for one known Comfy prompt id."""
    try:
        history_response = await CLIENT.get(f"{COMFY}/history/{prompt_id}", timeout=10)
        history_response.raise_for_status()
        history = history_response.json()
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                return {"state": "failed", "entry": entry, "error": _history_error(entry)}
            if status.get("completed") or "outputs" in entry:
                return {"state": "result_ready", "entry": entry}

        queue_response = await CLIENT.get(f"{COMFY}/queue", timeout=10)
        queue_response.raise_for_status()
        queue = queue_response.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError, TypeError) as exc:
        raise ComfyUnavailable(f"无法核对 ComfyUI 任务: {exc}") from exc

    for key, state in (("queue_running", "running"), ("queue_pending", "submitted")):
        for item in queue.get(key) or []:
            if isinstance(item, list) and len(item) > 1 and str(item[1]) == prompt_id:
                return {"state": state, "queue_item": item}
    return {"state": "missing"}


def find_comfy_output(entry: dict) -> dict:
    for node_id, node_out in (entry.get("outputs") or {}).items():
        for image in node_out.get("images", []):
            if image.get("type") in ("output", "temp") and image.get("filename"):
                return {
                    "node_id": str(node_id),
                    "filename": str(image["filename"]),
                    "subfolder": str(image.get("subfolder") or ""),
                    "type": str(image.get("type") or "output"),
                }
    raise ComfyResultUnavailable("ComfyUI 已结束，但历史中没有输出图片")


async def retrieve_comfy_result(prompt_id: str, destination_name: str) -> tuple[str, dict]:
    """Download an existing Comfy result atomically without resubmitting generation."""
    state = await inspect_comfy_prompt(prompt_id)
    if state["state"] == "failed":
        raise ComfyExecutionFailed(state["error"])
    if state["state"] != "result_ready":
        raise ComfyResultUnavailable(f"ComfyUI 结果尚未就绪 ({state['state']})")
    output = find_comfy_output(state["entry"])
    try:
        response = await CLIENT.get(
            f"{COMFY}/view",
            params={
                "filename": output["filename"],
                "subfolder": output["subfolder"],
                "type": output["type"],
            },
            timeout=60,
        )
        response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise ComfyResultUnavailable(f"图片已生成，但取回失败: {exc}") from exc
    data = response.content
    if not data or not (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    ):
        raise ComfyResultUnavailable("ComfyUI 返回的结果不是有效图片")
    safe_name = os.path.basename(destination_name)
    if safe_name != destination_name or not safe_name:
        raise ValueError("输出文件名无效")
    temporary = IMAGES / f".{safe_name}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, IMAGES / safe_name)
    return safe_name, output

async def submit_and_wait(wf_name: str, prompt_en: str, width, height, lora_keys: list[str] | None = None,
                          strength_char: float | None = None, strength_style: float | None = None,
                          image_filename: str | None = None, denoise: float | None = None,
                          detailer: dict | None = None,
                          negative_text: str | None = None,
                          lora_bindings: list[dict] | None = None,
                          registry_revision: str | None = None,
                          seed: int | None = None,
                          fit_mode: str = "preserve",
                          crop_position: str = "center") -> str:
    payload = build_prompt(
        wf_name, prompt_en, width, height, lora_keys,
        strength_char, strength_style, image_filename, denoise,
        detailer, negative_text, lora_bindings=lora_bindings,
        registry_revision=registry_revision, seed=seed,
        fit_mode=fit_mode, crop_position=crop_position,
    )
    pid = str(uuid.uuid4())
    payload = submission_payload(payload, pid, f"compat-{pid}")
    await submit_to_comfy(payload)

    deadline = time.time() + int(CFG.get("timeout_seconds", 300))
    while time.time() < deadline:
        await asyncio.sleep(2)
        state = await inspect_comfy_prompt(pid)
        if state["state"] in {"submitted", "running", "missing"}:
            continue
        if state["state"] == "failed":
            raise ComfyExecutionFailed(state["error"])
        if state["state"] == "result_ready":
            fname = f"{uuid.uuid4().hex[:12]}.png"
            saved, _ = await retrieve_comfy_result(pid, fname)
            return saved
    raise TimeoutError("生成超时")
