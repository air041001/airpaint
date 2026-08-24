# 架构

> 当前状态反映 2026-08-23（LoRA Context / Binding 首版完成后）。
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
`translate(text, reroll, image_b64, lora_selections)` 三层流程，默认返回 `(prompt_en, breakdown, prompt_ir)`；`/api/translate` 通过 `include_meta=True` additive 返回 `prompt_ir_meta` 与 LoRA binding snapshot：
1. **角色匹配** (`match_characters`, `char_dict.yaml`): 子串扫描角色名, 返回 tag 列表 + 移除角色名后的剩余文本。
2. **词典匹配** (`dict.yaml`, 当前约 1044 条): 剩余文本**子串匹配** (最长优先, len>=2), 分 hits / misses (见 D26)。
3. 全命中且无 active LoRA: 裸角色名 (只有角色无描述) -> `tag, 1girl, solo` 跳过 LLM; 否则 `角色tag + 词典tag` 拼接。active LoRA 会覆盖此快速路径。
4. 有未命中 -> 按后端:
    - `siliconflow`: 构造上下文 (Known character tags / Known attribute tags / Remaining misses) 送 DeepSeek-V4-Flash (config `siliconflow_model`)。生产文本 LLM 输出单行 12 字段 `IR` + `PROMPT`，未知角色候选优先从 `IR.subject` 取得，解析器兼容可选 `CHAR` 行；`PROMPT` 是约 20 个元素以内的紧凑最终画师 Prompt；`_parse_structured_output` 仍兼容旧 `IR + TAGS + NL` 和旧 5 字段协议。5 个前端 breakdown 字段由 IR 派生。画师协议优先保证主体可读性、动作/姿态、场景、构图、光影/材质，并由代码层补主体计数、抑制未请求剪影、默认风格污染和 NSFW close-up；未知角色查询 Danbooru exact tag + category/post_count，`likely_supported` 才写独立平铺 auto cache，正式 `char_dict` 优先；`unavailable` 不缓存以便下次重试；`/api/translate` 以 additive `prompt_ir_meta` 标注补全和 lookup 来源；reroll 使用新的补全方案。`compile_prompt` 统一做角色裸名清理、去重、count→character→general 排序和 Prompt 拼接；`build_prompt` 再负责 quality/LoRA/workflow。rating tag 不由 LLM 或关键词启发式推断，用户可在生成前编辑英文 Prompt 手动加入。`/no_think` + 顶层 `enable_thinking:False`(config `translate_enable_thinking` 可翻, 见 D18) 关思考, max_tokens 550 / temp 0.4, 失败抛 502。LRU 缓存 500 (key=上下文, 值为 `(prompt_en, breakdown, prompt_ir)`)。
   - `google`: gtx 逐词翻 misses (本机需翻墙, 已弃用)。
   - `none`: misses 原样保留。

生产文本 LLM 使用 `PAINTER_SYSTEM_PROMPT` 单次生成 `IR + PROMPT`；旧 `IR + TAGS + NL` 与 5 字段协议只保留解析降级。正向 `quality_prefix` 走 Anima 官方 (`masterpiece, best quality, newest, absurdres`), 负面为工作流固化常量 (WAI-Anima 式 + 构图否定词 multiple views/split view/grid view/cropped/out of frame), 不随输入变。见 D12/D13/D15/D18/D28/D37。

Active LoRA 时，文本快速路径也强制进入 LoRA-aware painter；Reasoning/Vision Model 只看 Asset/Profile 的 `provides` 与允许选择的 ID，不看文件名、强度或 exact trigger。模型输出可选 `LORA` JSON 语义选择，代码再解析 Profile/optional ID。翻译缓存 key 包含 selection 与 registry revision，避免不同 LoRA/Profile 共享 Prompt。

### LoRA Registry / Binding

- `server/lora_registry.yaml` 是版本化人工知识：Asset → Profile → required/default/optional tags、`provides`、默认强度、source/verified。`HotLoraRegistry` 保留嵌套结构，YAML 半写或校验失败时继续使用 last-good snapshot；canonical 内容 hash 作为 `registry_revision`。
- `get_lora_registry()` 合并顺序为 versioned registry > 未迁移 legacy config > 自动 inventory。unknown/incomplete 保留在 API `other`，前端显示“待注册”但不可直接选择；Wan/视频资产在 SHA/network 前排除。
- `resolve_lora_selections()` 只接受 registry key/Profile/optional ID；explicit 锁定，auto 由 LLM 在候选 ID 中选择，失败只可使用显式 default。返回 immutable-style `lora_bindings + warnings + revision`。
- `compile_lora_bindings()` 将 registry exact tags 幂等合入 Prompt；客户端回传的文件名、tags 或强度不作为真相。`jobs` 根据 key/profile/optional 重新解析，并在 revision 变化时返回 409。
- text、vision、reroll、jobs、dialog redo/tweak/vibe 与 `start-image` 都携带同一 binding snapshot。角色别名同时进入 Character Knowledge 去重，避免 LoRA 人物又被当未知角色查询。
- SiliconFlow/Vision 实际调用失败时请求以 502 fail closed，不使用缺少 LoRA-aware 语义规划的 Prompt 继续生成；`none/google` 配置降级路径只注入确定性 binding 并向前端显示 warning。首版通过 context 约束避免身份/服装/风格冲突，不实现独立 semantic conflict detector。
- 本地 onboarding 入口为 `.tools/start_lora_onboard_agent.bat`（或 `python .tools/register_lora.py --agent`）：先尝试刷新 LoRA Manager 增量索引，再让维护者粘贴作者说明。Reasoning Model 只生成候选 Profile/provides；代码固定本地文件名、candidate 状态，从原文恢复 exact trigger 转义并提取明确的单一推荐强度。候选支持自然语言修订，只有双重确认后才原子写 Registry。
- onboarding Agent 只从 gitignored `config.yaml` 读取现有 `siliconflow_api_key/model`，不复制或打印 key；作者说明按不可信输入处理。它不自动把 Civitai/trainedWords 提升为正式知识，真实出图前保持 `verified: candidate`，用户验收后再提升。

### Workflow Engine (工作流注入)
`build_prompt(wf_name, prompt_en, w, h, lora_keys=None, ..., lora_bindings=None, registry_revision=None)`:
1. 读 `workflows/<file>.json`。
2. `sanitize_for_api(wf)`: 删 `WidgetToString` / `Image Saver Metadata` (依赖前端 `extra_pnginfo`, API 提交会崩); `Image Saver Simple` → 内置 `SaveImage`。
3. **统一 seed**: 扫描所有 int 型 `seed`/`noise_seed` 输入, 全写成同一正整数 (跳过列表型的节点连接)。修复 Impact Pack `np.random.default_rng(-1)` 崩溃 → FaceDetailer 人脸修复能正常跑。
4. **LoRA binding 重解析**：有 snapshot 时只取 key/profile/optional，按同一 `registry_revision` 从当前 Registry 重建；旧 `lora_keys` 走 legacy adapter。随后由 Binding Compiler 补回被编辑删除的 required/default exact tags。
5. **LoRA workflow 注入**：写 `lora_node.loras = {"__value__":[{name,strength,clipStrength,active:true}, ...]}`。文件名与默认强度来自 Registry，角色/风格滑块只允许 0~1 覆盖；LoraManager 的 `text` 字段执行时会被 `del`，不能依赖它加载权重。
6. 注入 `prompt_node.text = quality_prefix + compiled prompt` 与尺寸；`safe/sensitive/questionable/explicit` 等 rating tag 仅保留用户手动编辑结果，不自动推断。不再把 Civitai 全量 trainedWords 在生成阶段盲拼。负面继续使用工作流固化模板。
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
出图两步走 (翻译与生成解耦, 见 D17): 中文 + `lora_selections` -> `/api/translate` 拿 prompt_en/breakdown/prompt_ir + binding/revision -> (可选预览/编辑) -> `/api/jobs` 回传 binding/revision。切换 LoRA/Profile 会使当前翻译过期，确认生成前必须重新翻译，避免 Prompt 与实际权重串线。
成像设置栏：当前工作流 / 文生图与图生图 / 精修 / 尺寸 / LoRA（角色+风格分组、Profile 自动判断/显式锁定、各自默认强度与滑块、provides/verified 展示、待注册禁用）。参考图入口保留在画面描述区。尺寸为点击展开的画幅选择器，标准档与高分辨率实验档分组；选择后自动收起。当前开放标准 `832x1216 / 896x1152 / 1024x1024 / 1344x768`，高分辨率 `1024x1536 / 1536x864`。
轮询 `/api/jobs/{id}` 每 2s, 完成后展示图 + 入历史画廊(localStorage 缩略图, 最近 12 张)。出图后「继续迭代」进暗房: 换一版(txt2img 重抽, D31 替换意图) / 微调(img2img, 低 denoise)。

> `web/` 是独立 git 仓库 → `air041001/air`。但域名迁移后已**不再依赖 GitHub Pages**
> (前端由后端直接托管), 该仓库仅作备份, push 与否不影响线上。

## 配置 (server/config.yaml, gitignore)

关键字段: `comfy_url` `comfy_dir` `host/port` `allow_origins` `tokens` `daily_limit`
`timeout_seconds` `banned_words` `translate` `siliconflow_api_key` `siliconflow_model` `siliconflow_vision_model` `reroll_temperature`
`workflows.anima.{file,prompt_node,seed_node,size_node,lora_node,image_node,switch_node,denoise_node,detailer_nodes,sizes,quality_prefix}`;
`submit_and_wait()` 统一使用 `timeout_seconds` 作为单次 ComfyUI deadline。当前 1024×1536 无 detailer 实测约 82 秒；此前 300 秒并非高分辨率正常开销，而是 txt2img 误走占位图 VAE 分支，已由显式 switch 路由修复（D43）。
人工 LoRA 真相在 `server/lora_registry.yaml`；`config.yaml` 顶层 `loras` 只作未迁移 legacy 兼容。未注册文件进入 gitignored `server/lora_cache.json` inventory，本地 `.metadata.json`/`.civitai.info` 优先，Civitai hash lookup 次之。

## 尚未实现 / 已知限制

- **意图解析**: Phase 1 已有 12 字段 Prompt IR + `compile_prompt`，但 IR 目前主要用于结构化记录、breakdown 派生和回归度量；TAG/NL 细分策略、字段级知识解析和结构化增量修改仍分别留给 Phase 2/4。路线见 `docs/PLAN-v5`。
- **多角色构图限制**: base Anima 对复杂双人对峙可能产生分页、黑线或动作绑定错误；当前不做针对单一 case 的自动化特判，用户可在 `/api/translate` 返回的 `prompt_en` 编辑后再提交。
- **LoRA composition 边界**：首版支持多 Profile 与角色×1 + 风格×1；没有真实跨文件多人 LoRA 资产与人眼验证，不宣称完成自由多角色 composition。
- 用量/任务状态全内存, 重启清零 (Phase 3 计划 SQLite)。
- 单份合并工作流 AnimaFull; 加功能分支 = 改 AnimaFull.json + config 声明节点 + build_prompt 拼接逻辑 (D32)。
- 轮询取状态 (Phase 3 计划 WebSocket)。
