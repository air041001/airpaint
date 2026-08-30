# HTTP API

> 契约源自 `server/api.py` 路由定义。改接口时同步本文件，并按 `AGENTS.md` 完成验证与文档闭环。

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
  { "name": "anima", "label": "Anima V7", "sizes": ["832x1216","896x1152","1024x1024","1344x768","1024x1536","1536x864"] }
]
```

### GET /api/loras
列出可用 LoRA Asset (需鉴权)。合并 versioned Registry > 尚未迁移的 legacy config，按 type 分组；未注册本地文件不进入生产 API，由 onboarding 工具直接枚举。

响应 `200`:
```json
{
  "characters": [
    {
      "key": "denia", "type": "character", "name": "达妮娅 / 西格莉卡",
      "configured": true, "source": "registry", "trigger_policy": "profile",
      "provides": [], "verified": null,
      "strength_model": 1.0, "strength_clip": 1.0,
      "default_profile": "white", "allow_multiple_profiles": true,
      "profiles": [
        { "id": "white", "name": "达妮娅（白）", "aliases": ["达妮娅"],
          "provides": ["Denia character identity"], "verified": "curated",
          "optional": [{"id":"white_dress","name":"白裙完整细节","provides":["white dress"]}] }
      ]
    }
  ],
  "styles": [
    { "key": "shiratama_art", "type": "style", "name": "白玉画风 (Shiratama)", "description": "...", "preview": "/lora-previews/shiratama_art.webp?v=...", "configured": true, "source": "registry" }
  ],
  "other": []
}
```
- `configured` 对当前生产列表恒为 `true`；字段暂保留供旧前端兼容。
- `source`: `"registry"` 或 `"config"`（尚未迁移兼容项）。
- `profiles` 只暴露语义 ID、名称、aliases、provides、verified 与 optional ID；不向前端发送 exact tags 作为可编辑真相。
- `allow_multiple_profiles` 为旧前端兼容字段：只要 Asset 暴露多个 Profile 就返回 `true`。Registry 中同名旧字段不再限制选择，也不是多人出图质量证明。
- `styles` 同时承载 `style/action/expression` 类型，作为风格/细节叠加区；无法分类的条目仍在 `other`。
- `strength_model/strength_clip` 是 Registry 默认值，前端选择 Asset 时同步到该 Asset 的滑块；请求可逐 Asset 覆盖为 0~2。
- `preview` 是可直接用于 `<img>` 的 URL。Registry 显式值优先；未填写时后端按安全 Asset key 查找 `server/lora_previews/<key>.webp|png|jpg|jpeg` 并附加 mtime 版本。没有受控资源时为 `null`，前端必须显示文字占位而不能阻断选择。

### POST /api/translate
只编译不排队 (需鉴权, 不计入 image 限额)：中文/参考图 + 补全程度 + 可选 LoRA selection -> LoRA-aware 英文 Prompt。前端「先看构思」和「生成」都先调用它；文本 Composer 同时返回可编辑中文 `concept` 与 binding snapshot。

请求体:
```json
{
  "prompt": "达妮娅穿白裙站在樱花树下",
  "completion_level": "auto",
  "concept_override": "用户锁定：达妮娅、白裙｜模型补全：樱花河岸、回眸动作、清晨逆光",
  "reroll": false,
  "lora_selections": [
    {"key":"denia","profile":"white","mode":"explicit","optional":["white_dress"],"strength_model":0.8,"strength_clip":0.8},
    {"key":"blue_archive_style","mode":"explicit","strength_model":0.6,"strength_clip":0.6}
  ]
}
```
- `prompt`: 选填, ≤4000 字符, 经内容过滤 (与 `image` 至少一项)。输入长度不自动决定补全档。
- `completion_level`: 可选，`auto | faithful | free`，默认 `auto`。仅控制 SiliconFlow 普通文本 Composer：`auto` 按语义覆盖度补重要空位，`faithful` 只补成图必需项，`free` 在保留明确锁定后自由设计未指定部分。
- `concept_override`: 可选, ≤4000 字符，必须保持 `用户锁定：…｜模型补全：…` 结构。用于把用户编辑后的中文构思作为权威蓝图重新编译；它不是直接注入工作流的 Prompt，也不是跨轮 PromptState。当前只对普通文本 Composer 有效。
- `image`: 可选, 参考图 base64 (data URI, ≤5MB)。有图走视觉 LLM 提氛围, 不走文本 LLM (③, 见 D23); 图不进 ComfyUI, 仍走 txt2img。
- `reroll`: 可选, 默认 `false`。`true` 时文本 LLM 在同一 `completion_level` 内高温重出一版**不同构思** (跳过 LRU 缓存)；对视觉 LLM 路径仍是不同图像解读。纯角色 canonical 快路不调用 LLM，因此仍返回同一结果。
- `lora_selections`: 可选数组。元素为 `{key, profile?, profiles?:[], mode:"auto|explicit", optional?:[], optional_by_profile?:{}, strength_model?, strength_clip?}`。同一 Asset 只保留一条 selection；`profile` 用于单选，任何多 Profile Asset 都可用 `profiles` 多选，Profile/optional 只能使用 Registry ID。角色总量按 Profile（或未决 auto Asset）计数，最多 3；风格/动作/表情不设硬上限。逐 Asset 强度范围为 0~2。向后兼容 `loras:[key]` 与 `lora:key`。

SiliconFlow 文本必须返回 `CONCEPT + 精确 12 字段 IR + [LORA] + PROMPT`；有 active LoRA 时 `LORA` 行必需。协议错误自动修复一次，仍不合法则失败，不会把原始响应当 Prompt。翻译失败: `502 {"detail":"翻译失败, 请稍后重试 (...)"}`。

响应 `200`:
```json
{
  "concept": "用户锁定：达妮娅、白裙、樱花树｜模型补全：河岸回眸、清晨逆光与花瓣前景",
  "prompt_en": "1girl, white dress, cherry blossoms, looking back over her shoulder, morning backlight traces the petals and dress hem, ...",
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
    "mode": "visual_composer",
    "source": {
      "user_intent": "remaining_input",
      "character_tags": "dictionary",
      "attribute_tags": null,
      "default_completion": "visual_composer"
    },
    "expansion_applied": true,
    "completion_level": "auto",
    "concept": "用户锁定：达妮娅、白裙、樱花树｜模型补全：河岸回眸、清晨逆光与花瓣前景",
    "concept_override_applied": true,
    "repetition_collapsed": false,
    "reroll": false,
    "reroll_strategy": null,
    "prompt_ir_available": true,
    "character_lookup": [],
    "lora_aware": true
  },
  "lora_bindings": [
    {"key":"denia","type":"character","file":"denia_lorav4-000005.safetensors",
     "profile":"white","profiles":["white"],"optional":["white_dress"],"optional_by_profile":{"white":["white_dress"]},"resolved_by":"explicit",
     "injected_tags":["denia \\(wuthering waves\\)","white dress"],
     "provides":["Denia character identity","white dress"],
     "strength_model":1.0,"strength_clip":1.0}
  ],
  "lora_warnings": [],
  "registry_revision": "16-char-content-hash"
}
```
- `concept`: 文本 Composer 的中文构思控制面，结构为 `用户锁定：…｜模型补全：…`。纯角色 canonical 快路也会返回“模型补全：无”；当前 Vision/legacy 路径可能为 `null`。
- `prompt_en`: 已编译的英文 Anima 正向 Prompt，可由 canonical tag、英文短句、自然语言或三者混合组成，不设固定 tag 数。用户仍可在生成前直接编辑。
- `breakdown`: 供前端预览展示「AI 理解」的 5 个维度 (scene/composition/mood/lighting/style)。文本 LLM 路径由 `prompt_ir` 派生；旧协议/快速路径没有时为 `null`。
- `prompt_ir`: 12 字段 Prompt IR。每个字段都是字符串数组；文本 LLM 成功解析时返回，快速路径和当前视觉 LLM 旧协议路径为 `null`。IR 是语义计划，不是可直接注入工作流的文件名、节点 ID 或数值。
- `prompt_ir_meta`: additive 来源与补全元数据。`mode=visual_composer` 表示新文本协议；`completion_level` 是本次档位；`concept_override_applied` 表示是否按编辑后的构思重编译；`repetition_collapsed` 表示后端是否折叠了完整 Prompt 的机械重复；`reroll_strategy=new_visual_concept` 表示 reroll 会在同一档位换构思。旧客户端可忽略此字段。
- `character_lookup`: 本次文本翻译触发的未知角色查询结果；`likely_supported` 才会进入独立 auto cache，`weak`/`absent`/`unavailable` 不会污染正式 `char_dict.yaml`。Danbooru 不可达时（`unavailable`）会将 LLM 归一化候选 tag 补进本次 `prompt_en`，但不会写入任何缓存。
- `lora_bindings`: 本次翻译解析出的 Asset/Profile/optional/逐 Asset 强度 snapshot；同一 Asset 多 Profile 仍只有一个 binding，`prompt_en` 已由代码幂等合入各 Profile 的 Registry exact tags。
- `lora_warnings`: default Profile、未知 optional 等可恢复提醒。
- `registry_revision`: versioned Registry 内容 hash。客户端提交 job 时一并回传；Registry 改动后旧 binding 返回 409，要求重新翻译。

严格 `CONCEPT + IR + [LORA] + PROMPT` 只约束 SiliconFlow 普通文本 Composer。参考图 Vision 和 `google`/`none` legacy 路径继续保留旧协议与 ordinary `dict.yaml` 行为；纯角色名且无 LoRA/构思覆盖时保留 deterministic canonical 快路。

### POST /api/jobs
提交生图任务 (需鉴权)。**接收已翻译的 `prompt_en`** (前端先用 `/api/translate` 翻译, 可在「预览提示词」里编辑后再提交), 后端不再翻译。

请求体:
```json
{
  "workflow": "anima",
  "prompt_en": "1girl, white hair, blue eyes, cat ears, smile",
  "prompt": "白发蓝眼睛的猫耳少女, 微笑, 站在樱花树下",
  "concept": "用户锁定：白发蓝眼猫耳少女、微笑｜模型补全：樱花树下的站姿与柔和日光",
  "completion_level": "auto",
  "size": "832x1216",
  "lora_selections": [
    {"key":"denia","profile":"white","mode":"explicit","optional":["white_dress"],"strength_model":0.8,"strength_clip":0.8},
    {"key":"blue_archive_style","mode":"explicit","strength_model":0.6,"strength_clip":0.6}
  ],
  "lora_bindings": [
    {"key":"denia","profile":"white","profiles":["white"],"optional":["white_dress"],"strength_model":0.8,"strength_clip":0.8}
  ],
  "registry_revision": "原样回传 /api/translate 的 revision",
  "detailer": {"face": true, "hand": true, "nsfw": false, "eyes": true},
  "image": "(可选, base64, 图生图模式)",
  "denoise": 0.35
}
```
- `prompt_en`: 必填，已编译英文 Prompt（可经用户编辑），≤6000 字符，经内容过滤。LoRA required/default tags 由后端重新绑定后，最终编译 Prompt 上限为 8000 字符。
- `prompt`: 可选，原始中文（仅存档展示），≤4000 字符；不传则 `prompt_raw` 同 `prompt_en`。
- `concept`: 可选，≤4000 字符，保持 `用户锁定：…｜模型补全：…` 结构；用于在 job、状态与暗房之间追踪本次生成蓝图，不直接写入 ComfyUI 正向 Prompt。
- `completion_level`: 可选，`auto | faithful | free`，默认 `auto`；与 `concept` 一起保存，供后续暗房迭代沿用。
- `size`: 可选, 必须是该工作流 `sizes` 之一; 不传取第一个。后端把该尺寸写入 txt2img 的 EmptyLatent 节点，并显式选择 txt2img 分支；所有尺寸共用配置项 `timeout_seconds`。
- `lora_selections`: 新客户端的选择真相，契约同 `/api/translate`：角色最多 3 个语义 Profile，风格/细节不设硬上限；任何多 Profile Asset 都可多选。
- `lora_bindings` + `registry_revision`: 推荐原样回传 translate 结果。后端不信任客户端的 file/tags，而是从 binding 的 key/profile(s)/optional 重新解析；逐 Asset 强度会重新校验，revision 过期返回 409。
- `loras` / `lora`: 旧客户端兼容入口，内部转为 selection；不传或空表示不用 LoRA。
- `strength_char` / `strength_style`: 旧客户端兼容字段，各自 0~1；存在时会覆盖对应角色或风格/动作/表情组。新客户端应使用 selection/binding 内逐 Asset 的 `strength_model/strength_clip`（0~2）。旧 `strength` 单字段已废弃。
- `image`: 可选, base64 (data URI 或纯 base64)。图生图模式: 后端上传到 ComfyUI input -> 注入 LoadImage + ImpactSwitch select=2 + denoise。工作流需配 `image_node`/`switch_node`/`denoise_node` (见 D26)。
- `denoise`: 可选, 0.1~0.9。图生图重采样强度: 低=接近原图(微调), 高=大改。默认 0.35。

- `detailer`: 可选, `{face,hand,nsfw,eyes}` 布尔, 控制 4 路精修 (默认全关=快速; 全开约 95s)。后端删未选节点重连。

校验失败: `400` (未知工作流 / prompt_en 空或过长 / 非法尺寸 / 命中禁词 / 未知 LoRA / 未知精修类型 / 工作流不支持 LoRA / LoRA 强度非法 / 角色超过 3 个 / 同一物理文件以冲突强度重复加载)。

响应 `200`:
```json
{ "id": "cbf274b7e5", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "lora_bindings": [], "lora_warnings": [], "registry_revision": null }
```

### GET /api/jobs/{job_id}
查询任务状态 (需鉴权)。

任务不存在 → `404`。

响应随状态变化:

queued (排队中):
```json
{ "id": "...", "status": "queued", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima", "position": 2 }
```
running (生成中):
```json
{ "id": "...", "status": "running", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima" }
```
done (完成):
```json
{ "id": "...", "status": "done", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima", "image": "/images/4f2fdd03f1e5.png" }
```
failed (失败):
```json
{ "id": "...", "status": "failed", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima", "error": "生成超时" }
```

`image` 是相对路径, 拼接 Base URL 取图。任务状态同时返回 `concept/completion_level/lora_bindings/lora_warnings/registry_revision`，便于诊断构思、Prompt 与实际权重。

### POST /api/dialog/turn
⑤ 对话迭代: 每轮一次出图 (需鉴权, 计入日限)。显式路由不猜意图: `action` 由前端按钮决定 (见 D25)。

请求体:
```json
{ "session_id": "可选, 首轮省略", "action": "start|start-image|redo|vibe|tweak", "prompt": "首轮中文描述", "delta": "可选改动", "completion_level": "auto", "workflow": "anima", "size": "832x1216", "lora_selections": [{"key":"denia","mode":"auto","strength_model":0.8,"strength_clip":0.8}] }
```
- `start`: 建会话 + 首图。`prompt` 必填且 ≤4000 字符；`completion_level` 可选并沿用到后续重翻译。
- `delta`: 可选改动，≤2000 字符。
- `redo` (换一版): `delta` 有则累积重翻译, 无则复用当前 prompt_en 换 seed。`delta` 含替换意图(换成/替换/改成/换为/改为)时, 先删原 raw 里的旧角色名再重翻译, 防 char_dict 双命中(D31)。
- `tweak` (微调/img2img): 上一张图上传 ComfyUI -> 合并工作流 `anima` (img2img 由 image_filename 触发, D32) + 低 denoise。`delta` 有则累积重翻译, 无则复用 current_en。`denoise` 控制偏离度 (默认 0.35)。
- `start` 时解析 selection 并把 binding/revision 保存进 session；`redo/vibe/tweak` 重翻译继续使用该 selection，不能从页面当前全局选择重新猜。
- `start-image` 从已完成源 job 复制 binding snapshot、`concept` 与 `completion_level`，不重新生成首图。

响应 `200`:
```json
{ "session_id": "...", "job_id": "..." }
```

### GET /api/dialog/{session_id}
返回会话线程 (需鉴权, 校验 token 归属)。

响应 `200`:
```json
{ "session_id": "...", "raw": "累积中文描述", "current_en": "最新 prompt_en", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto",
  "lora_bindings": [], "lora_warnings": [], "registry_revision": null,
  "turns": [ { "action": "start", "delta": "", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "status": "done", "image": "/images/x.png", "error": null } ] }
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
