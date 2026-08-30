"""ComfyUI workflow 清洗、注入、提交与结果获取。"""
import asyncio
import json
import random
import time
import uuid

from fastapi import HTTPException

from server.lora import (
    _bindings_as_selections,
    compile_lora_bindings,
    resolve_lora_selections,
)
from server.runtime import CLIENT, CLIENT_ID, IMAGES
from server.settings import BASE, CFG, COMFY, WORKFLOWS


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
                 registry_revision: str | None = None) -> dict:
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
                          negative_text: str | None = None,
                          lora_bindings: list[dict] | None = None,
                          registry_revision: str | None = None) -> str:
    payload = build_prompt(
        wf_name, prompt_en, width, height, lora_keys,
        strength_char, strength_style, image_filename, denoise,
        detailer, negative_text, lora_bindings=lora_bindings,
        registry_revision=registry_revision,
    )
    payload.pop("_seed")
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
