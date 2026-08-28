# 架构

> 当前状态反映 2026-08-28（LoRA Composition 工程扩展后）。
> 改动架构时同步本文件 (见 `CLAUDE.md` 规则 2)。

## 部署拓扑

```
访客浏览器
   │  https://airpaint.xyz            → 前端网页 (FastAPI 托管 index.html)
   │  https://api.airpaint.xyz/...     → 后端 API
   │  https://api.airpaint.xyz/images/ → 生成图片
   ▼
cloudflared 命名隧道 "airpaint" (永久固定, 重启不变)
   │  ~/.cloudflared/config.yml 路由两域名 → 127.0.0.1:8000
   ▼
FastAPI 后端  127.0.0.1:8000  (server/main.py)
   ├─ 鉴权 / 日限流 / 内容过滤
   ├─ Prompt Engine:  中文 → danbooru tag
   ├─ Workflow Engine: 注入 prompt/seed/size/LoRA, detailer 删节点拼接, 清洗前端专属节点
   ├─ 单并发队列 (asyncio.Queue, GPU 串行)
   └─ 静态托管 / + /images
   ▼
ComfyUI  127.0.0.1:8188  (不对公网开放)
   └─ AnimaFull 合并工作流 (txt2img/img2img/精修, 后端删节点拼接, D32)
```

> 前端与 API 同域不同子域: 网页在 `airpaint.xyz`, API 在 `api.airpaint.xyz`。
> 跨子域算跨域, 故 `config.yaml` 的 `allow_origins` 仍需显式列 `https://airpaint.xyz`。

> **启动**: ComfyUI 用 `run_nvidia_gpu_fast_fp16_accumulation.bat`; 后端+隧道用 `.tools/start_airpaint.bat` (双窗口);
> 隧道单独挂了用 `.tools/start_tunnel.bat` 补起 (不碰后端, 避免 8000 端口冲突)。
> bat 必须存 GBK+CRLF, 否则 cmd 解析错乱 cloudflared 行不执行 (见 decisions.md D14)。

## 后端模块 (server/main.py)

单文件, 按职责分块:

### 鉴权 & 限流
- `auth(req)` 依赖: 取 `Authorization: Bearer <token>`, 校验是否在 `TOKENS` 集合。
- 日限流: `USAGE` 字典 `token -> [date, count]`, 跨天归零, 超 `daily_limit`(30) 抛 429。
- **内存态**, 后端重启清零 (已知限制, 见 decisions.md)。

### 内容过滤
- `check_banned(text)`: `banned_words` 小写子串匹配, 命中抛 400。
- 对中文原文和翻译后英文各查一次。

### Prompt Engine (翻译)
`translate(text, reroll, image_b64, lora_selections, completion_level, concept_override)` 默认返回 `(prompt_en, breakdown, prompt_ir)`；`include_meta=True` 再返回 `prompt_ir_meta`，`/api/translate` 从中 additive 暴露 `concept`、补全/来源元数据与 LoRA binding snapshot。当前生产流程如下：

1. **角色 canonical knowledge** (`match_characters`, `char_dict.yaml`)：子串扫描正式词典和已验证自动缓存，返回精确角色 tag 与移除角色名后的文本。只有纯角色名、无 active LoRA、无 `concept_override` 的 SiliconFlow 请求走确定性快路，返回 `角色tag, 1girl, solo` 与“模型补全：无”。
2. **文本 Composer 路由**：SiliconFlow 普通文本把完整用户意图与角色 canonical tag 交给 Reasoning Model，ordinary `dict.yaml` 不再抢先删除词语，也不能因全命中绕过 LLM。`dict.yaml` 及 `match_dict_words()` 仍保留给参考图和 `google`/`none` legacy 降级路径；它没有被删除或宣布为无效知识。
3. **Visual Composer** (`PAINTER_SYSTEM_PROMPT`)：补全程度由显式 `auto | faithful | free` 控制，不根据字数猜测。`auto` 按语义覆盖度补重要空位，`faithful` 只补成图必需项，`free` 在保留用户锁定后自由设计完整插画。稀疏输入会形成一个具体主视觉，详细输入保持用户决定，不强行加入年龄词、rating、质量前缀、负面词或 LoRA exact trigger。用户明确写出的服装状态、暴露程度、可见身体细节、行为与镜头属于硬锁，Composer 必须用直接可绘制的 Anima 表达保留，不得委婉、遮挡或裁出画面；未说明的服装/暴露仍是补全空间，不从性行为本身推导裸体或遮盖。用户明确覆盖 active LoRA 服装时只覆盖受影响部位，其余 LoRA 概念继续保留。
4. **严格文本协议与定向语义修复**：SiliconFlow 文本必须输出 `CONCEPT`、精确 12 字段单行 `IR`、有 active LoRA 时的 `LORA` JSON、`PROMPT`，且没有其他行。`CONCEPT` 固定为 `用户锁定：…｜模型补全：…`；`concept_override` 必须保持该结构并作为用户编辑后的权威蓝图。协议错误、确定的画面容量冲突或角色 LoRA 发色/瞳色越权都只修复一次，第二次仍失败则 502 fail closed，绝不把错误响应直接当 Prompt。参考图 Vision 与非 SiliconFlow legacy 路径继续使用原有兼容协议，不受文本 Composer 定向护栏约束。
5. **自由 Anima 表达**：`PROMPT` 可以是 canonical tag、英文短句、自然语言或混合，不设固定 tag/句子/单词/字符数量。关系句可有意义地绑定少量 tag，但禁止机械复述整个 Prompt。5 个前端 breakdown 字段继续由 IR 的 scene/composition/mood/lighting/style 派生。
6. **确定性 Compiler / 可画性护栏**：代码补主体计数、清理精确角色的裸名变体、折叠完整逗号序列的机械复读；用户明确写全身/完整可见时移除互斥近景。模型补全若同时发明多个手部/服装操作，或用 `upper body/close-up` 承诺裙摆、髋部、大腿等画外交互，会退回 Composer 改为牛仔镜头/四分之三身或删减动作。新文本路径仍不继承旧 `_prepare_painter_tags()` 的自动裸体、固定景别或画风删除。未知角色继续从 `IR.subject` 经 Danbooru exact 验证后写 auto cache。
7. **缓存与模型调用**：LRU 缓存上限 500，key 是完整 Composer 上下文，因此包含补全档、`concept_override`、LoRA selection 与 registry revision；reroll 跳过缓存并在同一补全档内换构思。Reasoning Model `max_tokens=1800`；普通温度为 faithful 0.35 / auto 0.7 / free 0.8，reroll 使用配置的高温。`/no_think` 与 `enable_thinking:false` 默认关闭思考，失败抛 502。

正向 `quality_prefix` 由工作流代码统一提供 (`masterpiece, best quality, newest, absurdres`)；rating 只保留用户在英文 Prompt 中的明确输入。负面 Prompt 是 `AnimaFull.json` 节点 4 的固定常量，不随输入变化，包含 WAI-Anima 质量项、构图否定词，以及 `bad hands / missing fingers / extra fingers / fused fingers / extra arms / extra legs / bad feet / malformed feet` 的人体防御项。它只能降低部分常见失败概率，不代表人体问题已解决。见 D44/D46。

Active LoRA 时，Reasoning/Vision Model 只看 Asset/Profile 的 `provides` 与允许选择的 ID，不看文件名、强度或 exact trigger。严格文本协议要求 `LORA` JSON 语义选择，代码再解析 Profile/optional ID 并确定性注入 exact binding；Binding Compiler 同时排除兄弟 Profile 的 exact trigger 与身份裸名复述。文本 Composer 把角色 LoRA 身份外观视为闭集：Profile 的 `black/white/swim` 只表示已登记形态，不能推断发色或瞳色；只有用户原文/权威构思明确锁定的发色、瞳色可以进入 IR/PROMPT，越权项触发一次语义修复。该规则不猜角色真实发色，Registry 未声明时让角色 LoRA 自身提供。翻译缓存 key 包含 selection 与 registry revision，避免不同 LoRA/Profile 共享 Prompt。

### LoRA Registry / Binding

- `server/lora_registry.yaml` 是版本化人工知识：Asset → Profile → required/default/optional tags、`provides`、默认强度、source/verified。`HotLoraRegistry` 保留嵌套结构，YAML 半写或校验失败时继续使用 last-good snapshot；canonical 内容 hash 作为 `registry_revision`。
- `get_lora_registry()` 只合并 versioned Registry 与尚未迁移的 legacy config；新文件不会由服务启动扫描或 Civitai trainedWords 自动升格。`.tools/register_lora.py --agent` 直接枚举本地未注册文件，先验收 LoRA Manager 已索引目标，再由维护者确认候选并原子写入 Registry。
- `resolve_lora_selections()` 只接受 registry key/Profile/optional ID；explicit 锁定，auto 由 LLM 在候选 ID 中选择，失败只可使用显式 default。角色按语义 Profile 计数、最多 3 个；风格/动作/表情不设硬上限。同一 Asset 多 Profile 必须由 `selection.allow_multiple_profiles` 显式允许，并合并为一条 immutable-style binding。
- `compile_lora_bindings()` 将 registry exact tags 幂等合入 Prompt；客户端回传的文件名/tags 不作为真相，逐 Asset 强度则作为用户参数重新校验。`jobs` 根据 key/profile(s)/optional 重新解析，并在 revision 变化时返回 409。
- text、vision、reroll、jobs、dialog redo/tweak/vibe 与 `start-image` 都携带同一 binding snapshot。角色别名同时进入 Character Knowledge 去重，避免 LoRA 人物又被当未知角色查询。
- SiliconFlow/Vision 实际调用失败时请求以 502 fail closed，不使用缺少 LoRA-aware 语义规划的 Prompt 继续生成；`none/google` 配置降级路径只注入确定性 binding 并向前端显示 warning。首版通过 context 约束避免身份/服装/风格冲突，不实现独立 semantic conflict detector。
- 本地 onboarding 入口为 `.tools/start_lora_onboard_agent.bat`（或 `python .tools/register_lora.py --agent`）：先尝试刷新 LoRA Manager 增量索引，再让维护者粘贴作者说明。Reasoning Model 只生成候选 Profile/provides；代码固定本地文件名、candidate 状态，从原文恢复 exact trigger 转义并提取明确的单一推荐强度。候选支持自然语言修订，只有双重确认后才原子写 Registry。
- onboarding Agent 只从 gitignored `config.yaml` 读取现有 `siliconflow_api_key/model`，不复制或打印 key；作者说明按不可信输入处理。它不自动把 Civitai/trainedWords 提升为正式知识，真实出图前保持 `verified: candidate`，用户验收后再提升。

### Workflow Engine (工作流注入)
`build_prompt(wf_name, prompt_en, w, h, lora_keys=None, ..., lora_bindings=None, registry_revision=None)`:
1. 读 `workflows/<file>.json`。
2. `sanitize_for_api(wf)`: 删 `WidgetToString` / `Image Saver Metadata` (依赖前端 `extra_pnginfo`, API 提交会崩); `Image Saver Simple` → 内置 `SaveImage`。
3. **统一 seed**: 扫描所有 int 型 `seed`/`noise_seed` 输入, 全写成同一正整数 (跳过列表型的节点连接)。修复 Impact Pack `np.random.default_rng(-1)` 崩溃 → FaceDetailer 人脸修复能正常跑。
4. **LoRA binding 重解析**：有 snapshot 时只取 key/profile(s)/optional 与逐 Asset 强度，按同一 `registry_revision` 从当前 Registry 重建；旧 `lora_keys` 走 legacy adapter。随后由 Binding Compiler 补回被编辑删除的 required/default exact tags。
5. **LoRA workflow 注入**：写 `lora_node.loras = {"__value__":[{name,strength,clipStrength,active:true}, ...]}`。逐 Asset 强度可在 0~2 覆盖 Registry 默认值；旧角色/风格分组字段仍以 0~1 兼容。同一 safetensors 最多生成一条 Loader 记录；若不同 binding 对同一文件给出冲突强度则 400 fail closed。LoraManager 的 `text` 字段执行时会被 `del`，不能依赖它加载权重。
6. 注入 `prompt_node.text = quality_prefix + compiled prompt` 与尺寸；`safe/sensitive/questionable/explicit` 等 rating tag 仅保留用户手动编辑结果，不自动推断。不再把 Civitai 全量 trainedWords 在生成阶段盲拼。负面继续使用工作流固化模板，并包含常见手指、手臂、腿脚畸形的紧凑防御词。
7. **生成分支与 detailer**：每次构建都显式写 ImpactSwitch：txt2img=`input1`（节点 56 EmptyLatent，使用请求宽高），img2img=`input2`（节点 33 VAEEncode）并覆盖主 KSampler denoise。若有 `detailer:{face,hand,nsfw,eyes}`，删未选 detailer 节点并重连（删掉的节点不可达，不执行）。
8. 返回 `{prompt, client_id, _seed}`。

> 扩展其他节点注入 (ControlNet / 图生图 等) 前, 先看 `CLAUDE.md` 的「ComfyUI 节点注入准则」-- 必须查本机节点源码定 input 格式, 不靠猜; 实例见 D16 (LoRA)。

### ComfyUI 客户端
`submit_and_wait(...)`:
- POST `/prompt` 提交 → 拿 `prompt_id`。
- 轮询 `/history/{id}` (每 2s, 超时 `timeout_seconds`=300)。
- 完成后 GET `/view` 取图, 存 `images/<uuid>.png`, 返回文件名。

### 队列
- `asyncio.Queue` + 单 `worker()` 协程, **并发 = 1** (单卡串行)。
- 任务状态: `queued`(带 position) → `running` → `done`(image) / `failed`(error)。
- `JOBS` 字典存全部任务, **内存态** (重启丢失)。

### 静态托管
- `GET /` → `web/index.html` (FileResponse)。
- `/images` → StaticFiles 挂载 `server/images/`。

## 前端 (web/index.html)

单文件 SPA，无框架。localStorage 存邀请码与主题；线上使用 `https://api.airpaint.xyz`，localhost/127.0.0.1 自动使用当前 origin 便于本机 smoke。
三屏：登录（邀请码）/ 工坊（主界面）/ 暗房（对话迭代）。同一功能层提供两套材质：纸本画室（日间，暖纸底 + 墨绿操作色）与石墨暗房（夜间，暖黑底 + 安灯橙），工坊和暗房同步切换。
桌面工坊使用固定三栏骨架：画面描述跨左/中两栏，结果态为 `Prompt 检查 324px / 图片 flexible / 成像设置 360px`，最近作品位于左/中两栏下方。首次进入只显示画面描述与成像设置；首次翻译显示跨两栏 Prompt 检查；已有图片后重翻译不移走图片。图片操作位于独立工具栏，媒体余量使用当前图的模糊背景，不覆盖成图。暗房对应为控制左 / 当前图中 / 迭代脉络右。
移动端按描述 → 图片 → Prompt/参数页签 → 历史纵向排列；暗房按图片 → 控制 → 脉络排列。视图过渡只动画 opacity/translate，`prefers-reduced-motion` 下直接切换，不动画表单或网格尺寸。
出图两步走 (翻译与生成解耦, 见 D17/D46)：中文 + 补全模式 + `lora_selections` -> `/api/translate` 拿 concept/prompt_en/breakdown/prompt_ir + binding/revision -> 可选编辑中文构思或英文 Prompt -> `/api/jobs` 回传 concept/completion/binding/revision。中文构思编辑后以 `concept_override` 重新调用翻译，不能直接把中文送入工作流；原文、补全模式、构思或 LoRA/Profile 改变都会使当前翻译过期，确认生成前必须应用或重翻译，避免新意图配旧 Prompt。
描述区提供 `自动 / 忠于描述 / 自由补全` 三档；Prompt 检查区在五项 breakdown 上方显示可编辑的 `用户锁定｜模型补全` 中文构思。成像设置栏保留当前工作流 / 文生图与图生图 / 精修 / 尺寸 / LoRA。LoRA 使用角色与风格/细节两个连续多选菜单和“当前叠加栈”：角色最多 3 个语义 Profile，风格/动作/表情不设硬上限；允许组合的同一 Asset 可多选 Profile，每个 Asset 有独立 0~2 强度、provides/verified 展示与移除操作。参考图入口保留在画面描述区。尺寸为点击展开的画幅选择器，标准档与高分辨率实验档分组；选择后自动收起。当前开放标准 `832x1216 / 896x1152 / 1024x1024 / 1344x768`，高分辨率 `1024x1536 / 1536x864`。
轮询 `/api/jobs/{id}` 每 2s, 完成后展示图 + 入历史画廊(localStorage 缩略图, 最近 12 张)。出图后「继续迭代」进暗房: 换一版(txt2img 重抽, D31 替换意图) / 微调(img2img, 低 denoise)。

> `web/` 是独立 git 仓库 → `air041001/air`。但域名迁移后已**不再依赖 GitHub Pages**
> (前端由后端直接托管), 该仓库仅作备份, push 与否不影响线上。

## 配置 (server/config.yaml, gitignore)

关键字段: `comfy_url` `comfy_dir` `host/port` `allow_origins` `tokens` `daily_limit`
`timeout_seconds` `banned_words` `translate` `siliconflow_api_key` `siliconflow_model` `siliconflow_vision_model` `reroll_temperature`
`workflows.anima.{file,prompt_node,seed_node,size_node,lora_node,image_node,switch_node,denoise_node,detailer_nodes,sizes,quality_prefix}`;
`submit_and_wait()` 统一使用 `timeout_seconds` 作为单次 ComfyUI deadline。当前 1024×1536 无 detailer 实测约 82 秒；此前 300 秒并非高分辨率正常开销，而是 txt2img 误走占位图 VAE 分支，已由显式 switch 路由修复（D43）。
人工 LoRA 真相在 `server/lora_registry.yaml`；`config.yaml` 顶层 `loras` 只作未迁移 legacy 兼容。旧 gitignored `server/lora_cache.json` 已不再读取；未注册文件只在 onboarding 工具中枚举，本地 `.civitai.info` 只作维护者候选证据，不直接决定正式 trigger/Profile。

## 尚未实现 / 已知限制

- **构思不是 PromptState**：Visual Composer 已有三档补全、12 字段 IR 与单轮可编辑 CONCEPT，但 session 仍保存编译后的字符串；它不能做到“只改 clothing、其余字段永久锁定”的结构化历史。字段级增量修改仍留给真实暗房使用触发的 Phase 4。
- **多角色构图限制**: base Anima 对复杂双人对峙可能产生分页、黑线或动作绑定错误；当前不做针对单一 case 的自动化特判，用户可在 `/api/translate` 返回的 `prompt_en` 编辑后再提交。
- **LoRA composition 边界**：选择、binding、逐 Asset 强度、同文件去重和 workflow 注入已支持最多 3 个语义角色及不限风格/细节；结构/API/浏览器验证不等于多人画质验证。base Anima 的多人物空间关系、动作绑定和属性防串仍需固定条件出图与人眼判断。
- 用量/任务状态全内存, 重启清零 (Phase 3 计划 SQLite)。
- 单份合并工作流 AnimaFull; 加功能分支 = 改 AnimaFull.json + config 声明节点 + build_prompt 拼接逻辑 (D32)。
- 轮询取状态 (Phase 3 计划 WebSocket)。
