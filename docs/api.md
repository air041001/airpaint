# HTTP API

> 契约源自 `server/api.py` 路由定义。改接口时同步本文件，并按 `AGENTS.md` 完成验证与文档闭环。

Base URL: `https://api.airpaint.xyz` (公网) / `http://127.0.0.1:8000` (本机)

## 鉴权

首次登录可向 `/api/auth/check` 发送请求头：

```
Authorization: Bearer <token>
```

`token` 是 `config.yaml` 里的邀请码之一。验证成功后服务端设置签名的 HttpOnly `airpaint_session` cookie；浏览器随后的请求使用 cookie，不把原始邀请码保存在 localStorage。旧客户端仍可持续使用 Authorization header。

业务表只保存由本机 `identity.key` 计算的 HMAC owner ID，不保存原始邀请码。cookie 失效或邀请码被移除 → `401 {"detail":"邀请码无效或登录已过期"}`。

日限流只在创建新任务时由 SQLite 事务检查；超 `daily_limit` → `429 {"detail":"今日已达 N 张上限"}`。翻译、历史、状态、会话、图片读取与结果恢复不受已满配额阻挡。

---

## 接口

### GET /api/health
健康检查 (无需鉴权)。

响应 `200`:
```json
{ "ok": true, "comfy": true, "database": true, "schema_version": 1 }
```
`comfy` = 本机 ComfyUI (127.0.0.1:8188) 是否可达。

### GET /api/auth/check
验证邀请码/header 或既有 cookie 并刷新 HttpOnly cookie，**不查日限、不耗 GPU 配额**。

响应 `200`: `{ "ok": true, "daily_used": 12, "daily_limit": 90 }`。

### POST /api/auth/logout

清除浏览器登录 cookie；不删除作品、任务或用量记录。

### GET /api/workflows
列出可用工作流 (需鉴权)。

响应 `200`:
```json
[
  { "name": "anima", "label": "Anima V7", "sizes": ["832x1216","896x1152","1024x1024","1344x768","1024x1536","1536x864"],
    "quality_prefix": "masterpiece, best quality, newest, absurdres, ",
    "default_negative": "worst quality, low quality, ..., watermark, artist name" }
]
```

`quality_prefix` 与 `default_negative`（P1 增补）供前端「填入默认预设/恢复默认」；`default_negative` 为 `null` 表示工作流默认负面含动态 wildcard 语法，新路径不支持预览该动态默认（需用户提供完整负面）。

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

P1 增补：可选 `workflow`（用于取质量前缀与默认负面）；响应新增 `final_prompt_en`（含质量前缀的最终主采样正向）、`final_negative`（本次将实际生效的完整负面）、`negative_source`、`prompt_mode`。前端据此展示「最终文本」并可直接编辑；`prompt_en` 仍为不含质量前缀的编译正文（旧调用方兼容）。

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
- `prompt`: 选填，≤4000 字符，经内容过滤；文字、参考图、原图至少一项。输入长度不自动决定补全档。提供 `source_image` 时，文字表示希望改变的部分，不必重新描述整张图。
- `completion_level`: 可选，`auto | faithful | free`，默认 `auto`；文本、参考图与原图理解都由同一 Composer 编译。
- `concept_override`: 可选，≤4000 字符，保持 `用户锁定：…｜模型补全：…` 结构；编辑后的中文构思作为权威蓝图重编译，不直接注入工作流。
- `reference_image`: 可选，PNG/JPEG/WebP base64 或 data URI，上限 5,000,000 字符。前端发送最长边 768、JPEG 质量 0.85 的分析图。Vision 只返回观察契约，再由 Composer 合并用户输入与 LoRA；参考图本身不送入 txt2img。
- `reference_scope`: `composition_vibe`（默认，构图/空间/光影/氛围/配色）、`composition`（只参考镜头/布局/空间关系）、`full`（再参考可见外观/服装/动作/场景）。优先级为用户明确要求 > LoRA 身份与 Profile > 所选参考范围 > 自由补全。
- `source_image`: 可选，与 `reference_image` 互斥；同样发送分析图，实际范围固定为 `full`。用于独立 Img2Img 的“原图理解＋文字改动”；此接口仍只翻译，不生成。接口上限 12,000,000 字符。
- `image`: 旧参考图字段，保留一个兼容周期，未传新字段时映射到 `reference_image`。
- `reroll`: 默认 `false`；同一补全档内换构思，不重新随机解释图片。Vision 缓存按图片字节哈希、scope、Vision 模型配置及契约版本隔离；纯角色 canonical 快路仍是同一结果。
- `lora_selections`: 可选数组。元素为 `{key, profile?, profiles?:[], mode:"auto|explicit", optional?:[], optional_by_profile?:{}, strength_model?, strength_clip?}`。同一 Asset 只保留一条 selection；`profile` 用于单选，任何多 Profile Asset 都可用 `profiles` 多选，Profile/optional 只能使用 Registry ID。角色总量按 Profile（或未决 auto Asset）计数，最多 3；风格/动作/表情不设硬上限。逐 Asset 强度范围为 0~2。向后兼容 `loras:[key]` 与 `lora:key`。

Composer 必须返回 `CONCEPT + 精确 12 字段 IR + CHAR + [LORA] + PROMPT`；有 active LoRA 时 `LORA` 行必需。协议错误自动修复一次，仍不合法则失败，不把原始响应当 Prompt。Vision 的观察字段也由代码规范成十二字段，但不要求模型重复输出被排除的空字段；未知字段或没有有效观察仍失败。

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
- `concept`: Composer 的中文构思控制面，结构为 `用户锁定：…｜模型补全：…`。文本与图像路径均返回；纯角色 canonical 快路返回“模型补全：无”，非 Reasoning legacy 路径可能为 `null`。
- `prompt_en`: 已编译的英文 Anima 正向 Prompt，可由 canonical tag、英文短句、自然语言或三者混合组成，不设固定 tag 数。用户仍可在生成前直接编辑。
- `breakdown`: 供前端预览展示「AI 理解」的 5 个维度 (scene/composition/mood/lighting/style)。文本 LLM 路径由 `prompt_ir` 派生；旧协议/快速路径没有时为 `null`。
- `prompt_ir`: 十二字段字符串数组；文本与图像 Composer 均返回，canonical 快路可能为 `null`。IR 不是节点、文件名或工作流数值。
- `reference_contract`: 有图时为 `{scope, fields, source_model}`；`fields` 是十二字段观察对象，不等于最终 IR。被范围排除的字段为空数组；不得当作指令或 LoRA 身份来源。
- `reference_scope`: 实际采用的范围；原图理解为 `full`，没有图片时为 `null`。
- `prompt_ir_meta`: additive 来源与补全元数据。`mode=visual_composer` 表示新文本协议；`completion_level` 是本次档位；`concept_override_applied` 表示是否按编辑后的构思重编译；`repetition_collapsed` 表示后端是否折叠了完整 Prompt 的机械重复；`reroll_strategy=new_visual_concept` 表示 reroll 会在同一档位换构思。旧客户端可忽略此字段。
- `character_lookup`: 本次文本翻译触发的未知角色查询结果；`likely_supported` 才会进入独立 auto cache，`weak`/`absent`/`unavailable` 不会污染正式 `char_dict.yaml`。Danbooru 不可达时（`unavailable`）会将 LLM 归一化候选 tag 补进本次 `prompt_en`，但不会写入任何缓存。
- `lora_bindings`: 本次翻译解析出的 Asset/Profile/optional/逐 Asset 强度 snapshot；同一 Asset 多 Profile 仍只有一个 binding，`prompt_en` 已由代码幂等合入各 Profile 的 Registry exact tags。
- `lora_warnings`: default Profile、未知 optional 等可恢复提醒。
- `registry_revision`: versioned Registry 内容 hash。客户端提交 job 时一并回传；Registry 改动后旧 binding 返回 409，要求重新翻译。

`prompt_ir_meta.mode` 的图像路径为 `visual_composer_reference` / `visual_composer_source`。参考图不再绕开 Composer 或走字典全命中快路；图像理解和增量修改需要 Reasoning Model，不能降级成旧翻译器。`google`/`none` 仅保留普通文本 legacy 行为。

### POST /api/jobs
提交生图任务 (需鉴权)。**接收已翻译的 `prompt_en`** (前端先用 `/api/translate` 翻译, 可在「预览提示词」里编辑后再提交), 后端不再翻译。

文本状态与最终正负 (P1)：

- `prompt_mode`: `assisted`(缺省) | `manual`。`manual` 表示用户英文原文即最终文本——系统不调用翻译、不加质量前缀、不注入 trigger（LoRA 仍按服务端可信 binding/revision 加载，文本标签不加载任何文件）。
- `prompt_state`: `body` | `final` | 缺省(旧路径)。
  - `final` = `prompt_en` 已是最终文本，后端只校验与持久化、**不再组装**（用户删除的前缀/trigger 不会被补回）；
  - `body` = `prompt_en` 为待组装正文，由服务端唯一最终化步骤加质量前缀并幂等注入 trigger；
  - 缺省(不传) = 旧客户端兼容路径：做一次最终化，但**不覆盖工作流默认负面**（保留 wildcard 行为）。
- `negative_prompt`: 三态。缺省/`null` = 使用工作流默认负面；`""` = 清空；非空 = 完整覆盖。新路径下最终负面**字面写入负面节点**，之后改 seed 不会暗中改变已确认文字。
- 响应/历史新增 `prompt_mode`、`prompt_state`、`final_prompt_en`、`final_negative`、`negative_source`；旧客户端不传新字段时行为不变。

请求体:
```json
{
  "workflow": "anima",
  "client_request_id": "浏览器为本次点击生成的稳定 UUID",
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
  "source_image": "(可选, base64, 图生图模式)",
  "fit_mode": "preserve",
  "crop_position": "center",
  "seed": 12345,
  "denoise": 0.35
}
```
- `prompt_en`: 必填，已编译英文 Prompt（可经用户编辑），≤6000 字符，经内容过滤。LoRA required/default tags 由后端重新绑定后，最终编译 Prompt 上限为 8000 字符。
- `client_request_id`: 可选 8～128 位安全标识；也可放在 `Idempotency-Key` header。省略时服务端生成。相同 owner、相同 key、相同请求返回原任务且不重复扣次；相同 key 用于不同请求返回 409。
- `prompt`: 可选，原始中文（仅存档展示），≤4000 字符；不传则 `prompt_raw` 同 `prompt_en`。
- `concept`: 可选，≤4000 字符，保持 `用户锁定：…｜模型补全：…` 结构；用于在 job、状态与暗房之间追踪本次生成蓝图，不直接写入 ComfyUI 正向 Prompt。
- `completion_level`: 可选，`auto | faithful | free`，默认 `auto`；与 `concept` 一起保存，供后续暗房迭代沿用。
- `size`: 可选, 必须是该工作流 `sizes` 之一; 不传取第一个。后端把该尺寸写入 txt2img 的 EmptyLatent 节点，并显式选择 txt2img 分支；所有尺寸共用配置项 `timeout_seconds`。
- `lora_selections`: 新客户端的选择真相，契约同 `/api/translate`：角色最多 3 个语义 Profile，风格/细节不设硬上限；任何多 Profile Asset 都可多选。
- `lora_bindings` + `registry_revision`: 推荐原样回传 translate 结果。后端不信任客户端的 file/tags，而是从 binding 的 key/profile(s)/optional 重新解析；逐 Asset 强度会重新校验，revision 过期返回 409。
- `loras` / `lora`: 旧客户端兼容入口，内部转为 selection；不传或空表示不用 LoRA。
- `strength_char` / `strength_style`: 旧客户端兼容字段，各自 0~1；存在时会覆盖对应角色或风格/动作/表情组。新客户端应使用 selection/binding 内逐 Asset 的 `strength_model/strength_clip`（0~2）。旧 `strength` 单字段已废弃。
- `source_image`: 可选，PNG/JPEG/WebP base64/data URI，上限 12,000,000 字符；前端提交最长边 1536、JPEG 质量 0.92 的生成图，不复用 768 分析图。上传 ComfyUI 后选择 img2img 分支。旧 `image` 字段保留一周期，新字段优先。
- `denoise`: Img2Img 默认 0.35，范围 0.1–0.9；前端预设 0.35/0.55/0.75。没有原图却提交 denoise 返回 400。
- `fit_mode`: `preserve`（默认，节点 31 `pad_edge`，保留完整图后扩边）或 `crop`。`crop_position`: `center/top/bottom/left/right`，preserve 时规范为 center。不覆盖节点 31 的宽高连接。
- `seed`: 可选，1–2^63−1 整数；省略生成随机正整数。实际值在入队前保存并返回，与 workflow 使用值一致。
- `prompt_ir`: 可选，完整十二字段字符串数组；保存供增量修改。旧客户端缺省时不伪造 IR。
- `prompt_edited`: 默认 false；用户修改最终英文 Prompt 后设 true，后续修订以该 Prompt 为权威。此时快照中的 IR 是编辑前状态，直到下一次 Composer 修订才重建，不代表已经完成字段级同步。
- `reference_contract/reference_scope`: 可回传翻译结果，留在状态快照中，不直接写入 ComfyUI。

- `detailer`: 可选, `{face,hand,nsfw,eyes}` 布尔, 控制 4 路精修 (默认全关=快速; 全开约 95s)。后端删未选节点重连。

校验失败: `400` (未知工作流 / prompt_en 空或过长 / 非法尺寸 / 命中禁词 / 未知 LoRA / 未知精修类型 / 工作流不支持 LoRA / LoRA 强度非法 / 角色超过 3 个 / 同一物理文件以冲突强度重复加载)。

响应 `200`:
```json
{ "id": "cbf274b7e5", "status": "queued", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "lora_bindings": [], "lora_warnings": [], "registry_revision": null, "seed": 12345 }
```

任务创建、查询与 dialog 响应还包含 `seed`、`width/height`、`prompt_ir`、`parent_job_id`、`generation_mode`、`denoise`、`fit_mode/crop_position`、`change_fields`、`reference_contract/reference_scope`、`state_snapshot`。`generation_mode` 为 `txt2img/img2img/redo/tweak/vibe`。快照深拷贝本轮原始意图、构思、IR、可编辑 Prompt、LoRA、尺寸/seed 与完成方式；不会随后续分支修改。

`image_warnings` 在源图比例偏差超过 3% 时提示适配方式和最近画幅（目前后端识别 PNG/JPEG；前端识别所有支持格式），不静默改尺寸。重绘不是局部锁定，preserve 也不保证边缘或人体完全不变。

### GET /api/jobs/{job_id}
查询任务状态（需鉴权并校验任务归属，不受当日图片限额阻挡）。

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
{ "id": "...", "status": "done", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima", "image": "/api/images/4f2fdd03f1e5.png" }
```
failed (失败):
```json
{ "id": "...", "status": "failed", "prompt_raw": "...", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "workflow": "anima", "error": "生成超时" }
```

除四个基础状态外还会返回 `waiting_for_comfy / dispatching / submitted / result_pending / reconcile_pending / result_ready`，语义见下方状态机与 `docs/operations.md`。等待超时不等于 GPU 失败；`reconcile_pending` 不会盲目重发；`result_ready` 只需重新取图。

`image` 是受归属保护的相对路径，浏览器必须携带登录 cookie。任务状态还返回 `concept/completion_level/lora_bindings/lora_warnings/registry_revision`、生命周期时间、`comfy_prompt_id`、`error_kind/error_message`，便于区分连接、执行和取图错误。

若一个 `done` 任务引用的本地输出文件已被人工删除，服务端会在查询时确认文件缺失、隐藏该任务并返回 `404`；不会继续返回只剩 Prompt/参数的作品空壳。该判断只由服务端文件系统完成，不依据浏览器图片加载失败，因此网络中断和登录失效不会被误记为删除。

### POST /api/jobs/{job_id}/recover

重新核对现有 ComfyUI prompt ID，或重新下载已经生成的结果。需校验任务归属，不创建任务、不重复扣次。`done` 幂等返回；没有可恢复状态的明确失败任务返回 409。

### GET /api/history

服务端历史（需鉴权），按任务创建时间倒序并校验 owner。参数：`limit` 为 1～50，`cursor` 使用响应的 opaque `next_cursor`。

响应含 `items`、`next_cursor`、`daily_used`、`daily_limit`。每项是公开任务快照并附带 `session_ids`，前端可在刷新/重新登录后继续有效历史分支。已确认本地输出文件不存在的完成任务不会出现在 `items`；其参数也不再通过会话接口展示，但已消耗额度不返还。

### GET /api/requests/{client_request_id}

浏览器在 POST 响应丢失后按稳定请求 ID 找回任务。服务器没有落盘该请求返回 404，此时才可安全按原点击重新创建；找到则继续查询原任务。若原任务的作品文件后来已删除，返回 410，旧请求不会自动重新生成。

### POST /api/dialog/turn
⑤ 对话迭代: 每轮一次出图 (需鉴权, 计入日限)。显式路由不猜意图: `action` 由前端按钮决定 (见 D25)。

请求体:
```json
{ "session_id": "可选, 首轮省略", "client_request_id": "稳定 UUID", "action": "start|start-image|redo|vibe|tweak", "prompt": "首轮中文描述", "delta": "可选改动", "completion_level": "auto", "workflow": "anima", "size": "832x1216", "lora_selections": [{"key":"denia","mode":"auto","strength_model":0.8,"strength_clip":0.8}] }
```
- `start`: 建会话 + 首图。`prompt` 必填且 ≤4000 字符；`completion_level` 可选并沿用到后续重翻译。
- `delta`: 可选改动，≤2000 字符。
- `source_job_id`: 继续时可选，必须是本会话内已完成且属于当前用户的任务；缺省取最近完成任务。生成任务的 `parent_job_id` 指向它，允许从任意历史节点分支。
- `redo`（换一版）：无 delta 时完整复用源 Prompt 状态，不调用模型，只换 seed；旧客户端带 delta 时走同一 IR 增量修订，而不是累积 raw。
- `tweak`（界面名「基于此图重绘」，旧称微调）：用所选源图像素进入 Img2Img；有 delta 时 Composer 读取源快照并输出 `CHANGE_FIELDS`，只修改既有 IR 中相关字段，再编译完整 Prompt。未声明字段被改写则修复一次，失败不入队。无 delta 时复用源 Prompt，仍会重新采样。IR 增量约束不保证图像只改指定部分；人物、发型和构图也可能变化。接口 action 不随界面改名。
- `seed_strategy`: `inherit/random/fixed`。微调默认 inherit；换一版默认 random。fixed 必须给 `seed`；单独给 seed 默认 fixed。尺寸缺省继承源图，显式尺寸仍需属于工作流可选项。
- `denoise`、`fit_mode`、`crop_position`: 同 jobs；微调 denoise 默认 0.35，适配方式缺省继承源图。`detailer` 缺省继承源图，显式 `{}` 表示关闭。
- `vibe`: 只保留旧接口兼容，不再提供独立前端按钮；源图经 `composition_vibe` 观察后交 Composer，默认新 seed、txt2img。
- 同一迭代链固定 LoRA Asset/Profile/optional/强度；传入不同选择返回 409，必须回工坊开启新链。原有 Registry revision 校验不变。
- `start-image`: 用 `job_id`（兼容 `source_job_id`）将已完成图片纳入新会话，不重新生成首图、不扣配额。
- `client_request_id` 与 jobs 相同；网络重试返回原轮次，不重复追加 turn 或扣次。主动换一版必须使用新 ID。
- 每次生成任务与一次用量、可选会话 turn 在同一事务写入；翻译/校验失败不会污染原状态。每轮 delta 单独保存，`prompt_raw/raw` 保留原始意图，不再拼接历史中文。

响应 `200`:
```json
{ "session_id": "...", "job_id": "..." }
```

### GET /api/dialog/{session_id}
返回会话线程 (需鉴权, 校验 token 归属)。

响应 `200`:
```json
{ "session_id": "...", "raw": "原始中文描述", "current_en": "最新 prompt_en", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto",
  "lora_bindings": [], "lora_warnings": [], "registry_revision": null,
  "turns": [ { "action": "start", "delta": "", "prompt_en": "...", "concept": "用户锁定：…｜模型补全：…", "completion_level": "auto", "status": "done", "image": "/api/images/x.png", "error": null } ] }
```
`image` 在对应 job 完成后才有值 (worker 写回)。

`turns` 的每项含 `job_id`、`parent_job_id`、`action/delta` 和完整公开任务字段/快照；显示历史时以各节点状态为准，不用顶层 `current_en` 覆盖旧图。任务与会话从 SQLite 读取，重启后仍可继续有效分支；仅 Vision/Composer 缓存留在内存。输出文件已确认删除的 turn 会被过滤；若会话已无可见 turn，返回 404，不留下参数空壳。

### 资源
- `GET /` → 前端网页 `index.html`
- `GET /api/images/{filename}` → 需鉴权并校验图片属于当前 owner 的输出图；响应使用 `private, no-store`，文件不存在时返回 404 并同步隐藏对应历史
- `GET /lora-previews/{filename}` → 无需鉴权的受控 LoRA 预览资源

---

## 状态机

```
        POST /api/jobs
              │
              ▼
 queued / waiting_for_comfy
              │
              ▼
         dispatching ── 请求证据不确定 ──► reconcile_pending
              │                                  │
              ▼                                  └─ recover: 只核对 prompt_id
      submitted / running
          │             │
          │             └─ 等待超时 ──► result_pending
          ▼
     result_ready ── 下载成功 ──► done
          │
          └─ 下载失败后 recover: 只重新取图

 ComfyUI 明确拒绝/执行失败或输入丢失 ──► failed
```

前端每 2s 查询 `GET /api/jobs/{id}`。连接中断会显示恢复查询，不制造虚假百分比，也不把任务重新 POST。

## 错误码汇总

| 码 | 含义 |
|---|---|
| 400 | 参数校验 (未知工作流 / 提示词空或过长 / 非法尺寸 / 命中禁词) |
| 401 | 邀请码无效或登录 cookie 过期 |
| 404 | 任务不存在 |
| 409 | 幂等 key 与请求冲突 / LoRA revision 变化 / 任务状态不可恢复 |
| 429 | 当日已达上限 |
| 500 | 服务器内部错误 / 未知 translate 后端 |
| 502 | 翻译失败 (LLM/Google 返回异常或超时) |
