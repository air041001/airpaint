# HTTP API

> 契约源自 `server/main.py` 路由定义。改接口时同步本文件 (见 `CLAUDE.md` 规则 2)。

Base URL: `https://api.airpaint.xyz` (公网) / `http://127.0.0.1:8000` (本机)

## 鉴权

除 `/api/health` 和静态资源外, 所有接口要求请求头:

```
Authorization: Bearer <token>
```

`token` 即 `config.yaml` 里的 `tokens` 之一 (邀请码)。无效 → `401 {"detail":"无效 token"}`。
日限流超 `daily_limit` → `429 {"detail":"今日已达 N 张上限"}`。

---

## 接口

### GET /api/health
健康检查 (无需鉴权)。

响应 `200`:
```json
{ "ok": true, "comfy": true }
```
`comfy` = 本机 ComfyUI (127.0.0.1:8188) 是否可达。

### GET /api/auth/check
只验证邀请码有效性 (需鉴权, 用 `verify_token`: **不查日限、不耗 GPU 配额**)。给前端登录门禁用, 避免用 `/api/workflows`(`auth`, 查日限)验证导致达日限的朋友登不进来。

响应 `200`: `{ "ok": true }`; 邀请码无效: `401 {"detail":"无效 token"}`。

### GET /api/workflows
列出可用工作流 (需鉴权)。

响应 `200`:
```json
[
  { "name": "anima", "label": "Anima V7", "sizes": ["832x1216","1216x832","1024x1024"] }
]
```

### GET /api/loras
列出 LoRA Asset (需鉴权)。合并 versioned Registry > legacy config > 自动 inventory，按 type 分组；unknown/incomplete 保留在 `other` 供 onboarding，不再静默隐藏。

响应 `200`:
```json
{
  "characters": [
    {
      "key": "denia", "type": "character", "name": "达妮娅 / 西格莉卡",
      "configured": true, "source": "registry", "trigger_policy": "profile",
      "provides": [], "verified": null,
      "strength_model": 1.0, "strength_clip": 1.0,
      "default_profile": "white",
      "profiles": [
        { "id": "white", "name": "达妮娅（白）", "aliases": ["达妮娅"],
          "provides": ["Denia character identity"], "verified": "curated",
          "optional": [{"id":"white_dress","name":"白裙完整细节","provides":["white dress"]}] }
      ]
    }
  ],
  "styles": [
    { "key": "shiratama_art", "type": "style", "name": "白玉画风 (Shiratama)", "description": "...", "preview": null, "configured": true, "source": "registry" }
  ],
  "other": []
}
```
- `configured=false` 表示自动 inventory 尚未形成可用 Profile/trigger；前端显示“待注册”并禁用。
- `source`: `"registry"`、`"config"` 或 `"civitai"`（自动 inventory）。
- `profiles` 只暴露语义 ID、名称、aliases、provides、verified 与 optional ID；不向前端发送 exact tags 作为可编辑真相。
- `strength_model/strength_clip` 是 Registry 默认值，前端选择 Asset 时同步到滑块。
- `preview` 可能为 `null` (前端应容错隐藏)。

### POST /api/loras/refresh
重新扫描 LoRA 目录, 查 Civitai 补全未配置的 LoRA 元数据 (需鉴权)。

响应 `200`:
```json
{ "ok": true, "scanned": 10, "new": 2, "failed": 1, "excluded": 2, "total_auto": 5 }
```

### POST /api/translate
只翻译不排队 (需鉴权, 不计入 image 限额): 中文/参考图 + 可选 LoRA selection -> LoRA-aware 英文 Prompt。前端「先看翻译」和「生成」都先调它拿 prompt_en 与 binding snapshot。

请求体:
```json
{
  "prompt": "达妮娅穿白裙站在樱花树下",
  "reroll": false,
  "lora_selections": [
    {"key":"denia","profile":"white","mode":"explicit","optional":["white_dress"]}
  ]
}
```
- `prompt`: 选填, ≤500 字符, 经内容过滤 (与 `image` 至少一项)。
- `image`: 可选, 参考图 base64 (data URI, ≤5MB)。有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23); 图不进 ComfyUI, 仍走 txt2img。
- `reroll`: 可选, 默认 `false`。`true` 时文本 LLM 高温重出一版**不同画师补全方案** (抽卡再抽, 跳过 LRU 缓存); 对视觉 LLM 路径仍是不同图像解读, 快速路径(全命中词典)仍返回同一结果。
- `lora_selections`: 可选数组。元素为 `{key, profile?, mode:"auto|explicit", optional?:[]}`；单 Profile 通常 explicit，多 Profile 可让 `mode=auto` 由模型只在 Registry 候选 ID 中选择。向后兼容 `loras:[key]` 与 `lora:key`。

翻译失败: `502 {"detail":"翻译失败, 请稍后重试 (...)"}`。

响应 `200`:
```json
{
  "prompt_en": "1girl, white hair, blue eyes, cat ears, smile, ...",
  "breakdown": {
    "scene": "outdoors, cherry blossoms",
    "composition": "standing, looking at viewer",
    "mood": "cheerful",
    "lighting": "soft daylight",
    "style": "anime style"
  },
  "prompt_ir": {
    "subject": ["1girl"], "appearance": ["white hair", "blue eyes"],
    "clothing": [], "action": [], "pose": [], "interaction": [],
    "scene": ["outdoors", "cherry blossoms"], "composition": ["standing"],
    "lighting": ["soft daylight"], "mood": ["cheerful"],
    "style": ["anime style"], "constraints": []
  },
  "prompt_ir_meta": {
    "mode": "painter_expansion",
    "source": {
      "user_intent": "remaining_input",
      "character_tags": "dictionary",
      "attribute_tags": "dictionary",
      "default_completion": "painter"
    },
    "expansion_applied": true,
    "reroll": false,
    "reroll_strategy": null,
    "prompt_ir_available": true,
    "character_lookup": [],
    "lora_aware": true
  },
  "lora_bindings": [
    {"key":"denia","type":"character","file":"denia_lorav4-000005.safetensors",
     "profile":"white","optional":["white_dress"],"resolved_by":"explicit",
     "injected_tags":["denia \\(wuthering waves\\)","white dress"],
     "provides":["Denia character identity","white dress"],
     "strength_model":1.0,"strength_clip":1.0}
  ],
  "lora_warnings": [],
  "registry_revision": "16-char-content-hash"
}
```
- `prompt_en`: 翻译后的英文 danbooru tag。
- `breakdown`: 供前端预览展示「AI 理解」的 5 个维度 (scene/composition/mood/lighting/style)。文本 LLM 路径由 `prompt_ir` 派生；旧协议/快速路径没有时为 `null`。
- `prompt_ir`: 12 字段 Prompt IR。每个字段都是字符串数组；文本 LLM 成功解析时返回，快速路径和当前视觉 LLM 旧协议路径为 `null`。IR 是语义计划，不是可直接注入工作流的文件名、节点 ID 或数值。
- `prompt_ir_meta`: additive 来源与补全元数据。`mode=painter_expansion` 表示当前文本 LLM 使用画师级默认补全；`source` 区分用户剩余输入、词典命中和默认补全；`reroll_strategy=new_painter_plan` 表示 reroll 会换一套补全方案。旧客户端可忽略此字段。
- `character_lookup`: 本次文本翻译触发的未知角色查询结果；`likely_supported` 才会进入独立 auto cache，`weak`/`absent`/`unavailable` 不会污染正式 `char_dict.yaml`。Danbooru 不可达时（`unavailable`）会将 LLM 归一化候选 tag 补进本次 `prompt_en`，但不会写入任何缓存。
- `lora_bindings`: 本次翻译解析出的 Asset/Profile/optional snapshot；`prompt_en` 已由代码幂等合入 Registry exact tags。
- `lora_warnings`: default Profile、未知 optional 等可恢复提醒。
- `registry_revision`: versioned Registry 内容 hash。客户端提交 job 时一并回传；Registry 改动后旧 binding 返回 409，要求重新翻译。

文本 LLM 当前生产输出协议为 `IR + PROMPT`；旧 `IR + TAGS + NL` 和旧 5 字段响应仍保留解析降级，但不再与画师协议同时要求模型输出两套最终 Prompt。

### POST /api/jobs
提交生图任务 (需鉴权)。**接收已翻译的 `prompt_en`** (前端先用 `/api/translate` 翻译, 可在「预览提示词」里编辑后再提交), 后端不再翻译。

请求体:
```json
{
  "workflow": "anima",
  "prompt_en": "1girl, white hair, blue eyes, cat ears, smile",
  "prompt": "白发蓝眼睛的猫耳少女, 微笑, 站在樱花树下",
  "size": "832x1216",
  "lora_selections": [
    {"key":"denia","profile":"white","mode":"explicit","optional":["white_dress"]}
  ],
  "lora_bindings": [
    {"key":"denia","profile":"white","optional":["white_dress"]}
  ],
  "registry_revision": "原样回传 /api/translate 的 revision",
  "strength_char": 1.0, "strength_style": 0.8,
  "detailer": {"face": true, "hand": true, "nsfw": false, "eyes": true},
  "image": "(可选, base64, 图生图模式)",
  "denoise": 0.35
}
```
- `prompt_en`: 必填, 已翻译英文 tag (可经用户编辑), ≤800 字符, 经内容过滤。
- `prompt`: 可选, 原始中文 (仅存档展示, ≤500); 不传则 prompt_raw 同 prompt_en。
- `size`: 可选, 必须是该工作流 `sizes` 之一; 不传取第一个。
- `lora_selections`: 新客户端的选择真相。支持角色×1 + 风格×1；Profile/optional 只能使用 Registry ID。
- `lora_bindings` + `registry_revision`: 推荐原样回传 translate 结果。后端不信任客户端的 file/tags/strength，而是从 binding 的 key/profile/optional 重新解析；revision 过期返回 409。
- `loras` / `lora`: 旧客户端兼容入口，内部转为 selection；不传或空表示不用 LoRA。
- `strength_char` / `strength_style`: 可选, 各自 0~1 (1=满), 按选中 LoRA 的类型(character/style)分别生效; 不传用 config 各自默认值。旧 `strength` 单字段已废弃。
- `image`: 可选, base64 (data URI 或纯 base64)。图生图模式: 后端上传到 ComfyUI input -> 注入 LoadImage + ImpactSwitch select=2 + denoise。工作流需配 `image_node`/`switch_node`/`denoise_node` (见 D26)。
- `denoise`: 可选, 0.1~0.9。图生图重采样强度: 低=接近原图(微调), 高=大改。默认 0.35。

- `detailer`: 可选, `{face,hand,nsfw,eyes}` 布尔, 控制 4 路精修 (默认全关=快速; 全开约 95s)。后端删未选节点重连。

校验失败: `400` (未知工作流 / prompt_en 空或过长 / 非法尺寸 / 命中禁词 / 未知 LoRA / 未知精修类型 / 工作流不支持 LoRA / LoRA 强度非法)。

响应 `200`:
```json
{ "id": "cbf274b7e5", "prompt_en": "...", "lora_bindings": [], "lora_warnings": [], "registry_revision": null }
```

### GET /api/jobs/{job_id}
查询任务状态 (需鉴权)。

任务不存在 → `404`。

响应随状态变化:

queued (排队中):
```json
{ "id": "...", "status": "queued", "prompt_raw": "...", "prompt_en": "...", "workflow": "anima", "position": 2 }
```
running (生成中):
```json
{ "id": "...", "status": "running", "prompt_raw": "...", "prompt_en": "...", "workflow": "anima" }
```
done (完成):
```json
{ "id": "...", "status": "done", "prompt_raw": "...", "prompt_en": "...", "workflow": "anima", "image": "/images/4f2fdd03f1e5.png" }
```
failed (失败):
```json
{ "id": "...", "status": "failed", "prompt_raw": "...", "prompt_en": "...", "workflow": "anima", "error": "生成超时" }
```

`image` 是相对路径, 拼接 Base URL 取图。任务状态同时返回 `lora_bindings/lora_warnings/registry_revision`，便于诊断 Prompt 与实际权重。

### POST /api/dialog/turn
⑤ 对话迭代: 每轮一次出图 (需鉴权, 计入日限)。显式路由不猜意图: `action` 由前端按钮决定 (见 D25)。

请求体:
```json
{ "session_id": "可选, 首轮省略", "action": "start|start-image|redo|vibe|tweak", "prompt": "首轮中文描述", "delta": "可选改动", "workflow": "anima", "size": "832x1216", "lora_selections": [{"key":"denia","mode":"auto"}], "strength_char": 1.0 }
```
- `start`: 建会话 + 首图。
- `redo` (换一版): `delta` 有则累积重翻译, 无则复用当前 prompt_en 换 seed。`delta` 含替换意图(换成/替换/改成/换为/改为)时, 先删原 raw 里的旧角色名再重翻译, 防 char_dict 双命中(D31)。
- `tweak` (微调/img2img): 上一张图上传 ComfyUI -> 合并工作流 `anima` (img2img 由 image_filename 触发, D32) + 低 denoise。`delta` 有则累积重翻译, 无则复用 current_en。`denoise` 控制偏离度 (默认 0.35)。
- `start` 时解析 selection 并把 binding/revision 保存进 session；`redo/vibe/tweak` 重翻译继续使用该 selection，不能从页面当前全局选择重新猜。
- `start-image` 从已完成源 job 复制 binding snapshot，不重新生成首图。

响应 `200`:
```json
{ "session_id": "...", "job_id": "..." }
```

### GET /api/dialog/{session_id}
返回会话线程 (需鉴权, 校验 token 归属)。

响应 `200`:
```json
{ "session_id": "...", "raw": "累积中文描述", "current_en": "最新 prompt_en",
  "lora_bindings": [], "lora_warnings": [], "registry_revision": null,
  "turns": [ { "action": "start", "delta": "", "prompt_en": "...", "status": "done", "image": "/images/x.png", "error": null } ] }
```
`image` 在对应 job 完成后才有值 (worker 写回)。

### 静态资源 (无需鉴权)
- `GET /` → 前端网页 `index.html`
- `GET /images/{filename}` → 生成的 PNG

---

## 状态机

```
        POST /api/jobs
              │
              ▼
          queued ──(轮询带 position)──┐
              │                        │
              ▼                        │
           running                     │
           │        │                  │
         done    failed ◄──────────────┘
         │        │
    返回 image  返回 error
```

前端每 2s 轮询 `GET /api/jobs/{id}` 直到 `done`/`failed`。

## 错误码汇总

| 码 | 含义 |
|---|---|
| 400 | 参数校验 (未知工作流 / 提示词空或过长 / 非法尺寸 / 命中禁词) |
| 401 | 无效 token |
| 404 | 任务不存在 |
| 409 | LoRA Registry revision 已变化，需重新翻译 |
| 429 | 当日已达上限 |
| 500 | 服务器内部错误 / 未知 translate 后端 |
| 502 | 翻译失败 (LLM/Google 返回异常或超时) |
