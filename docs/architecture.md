# 架构

> 当前状态反映 2026-08-12 (工作流合并 D32 后)。
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
`translate(text)` 三层流程:
1. **角色匹配** (`match_characters`, `char_dict.yaml`): 子串扫描角色名, 返回 tag 列表 + 移除角色名后的剩余文本。
2. **词典匹配** (`dict.yaml`, 851 条): 剩余文本**子串匹配** (最长优先, len>=2), 分 hits / misses (见 D26)。
3. 全命中 (无 misses): 裸角色名 (只有角色无描述) -> `tag, 1girl, solo` 跳过 LLM; 否则 `角色tag + 词典tag` 拼接。
4. 有未命中 -> 按后端:
   - `siliconflow`: 构造上下文 (Known character tags / Known attribute tags / Remaining misses) 送 DeepSeek-V4-Flash (config `siliconflow_model`)。LLM **信息分流** (scene/composition/mood/lighting/style 给人看 + TAGS 离散属性 + NL 关系叙事, 见 D28), `_parse_structured_output` 解析; **TAGS/NL 不重复**(HARD RULE: NL 不得复述 TAGS 已有 tag, 全是 tag 则留空); 隐喻落 mood(禁字面名词), 多角色空间布局落 NL(单角色构图落 composition 带 facing/from behind/looking out 锚点), scene 强制具体; 无 TAGS 行降级为整体当 tag(不崩)。返回 `(new_tags, breakdown)`, 后端 `_strip_char_bare_names` 删 LLM 输出的已知角色裸名变体(如 `ganyu_(genshin_impact)` 时删 `ganyu`, 见 D30), 再 prepend 已知 tag, breakdown 回传前端预览。`/no_think` + 顶层 `enable_thinking:False`(config `translate_enable_thinking` 可翻, 见 D18) 关思考 (见 D2), max_tokens 400 / temp 0.4, 失败抛 502。LRU 缓存 500 (key=上下文, 值为 (prompt_en, breakdown))。
   - `google`: gtx 逐词翻 misses (本机需翻墙, 已弃用)。
   - `none`: misses 原样保留。

信息分流由单条 system prompt 处理 (How-to-decide 分流决策 + Self-check 自检 + Weight policy 权重框架 + 4 示例, 见 D28; Anima 规范: 小写+空格、主体计数在前、禁 quality/score tag、禁 realistic/3d); 正向 `quality_prefix` 走 Anima 官方 (`masterpiece, best quality, newest, absurdres`), 负面为工作流固化常量 (WAI-Anima 式 + 构图否定词 multiple views/split view/grid view/cropped/out of frame), 不随输入变。见 D12/D13/D15/D18/D28。

### Workflow Engine (工作流注入)
`build_prompt(wf_name, prompt_en, w, h, lora_keys=None, strength_char, strength_style, image_filename, denoise, detailer)`:
1. 读 `workflows/<file>.json`。
2. `sanitize_for_api(wf)`: 删 `WidgetToString` / `Image Saver Metadata` (依赖前端 `extra_pnginfo`, API 提交会崩); `Image Saver Simple` → 内置 `SaveImage`。
3. **统一 seed**: 扫描所有 int 型 `seed`/`noise_seed` 输入, 全写成同一正整数 (跳过列表型的节点连接)。修复 Impact Pack `np.random.default_rng(-1)` 崩溃 → FaceDetailer 人脸修复能正常跑。
4. **LoRA 注入** (若 `lora_keys`): 写 `lora_node.loras = {"__value__":[{name,strength,clipStrength,active:true}, ...]}` (数组多条, D29)。LoraManager 的 `text` 字段执行时被 `del` 无效, 必须走 widget; `active` 必须为 true (见 D16)。
5. 注入: `prompt_node.text = quality_prefix + safety + (trigger+", " 若有 LoRA) + prompt_en`; `size_node.width/height`; (不配 `negative_node`, 用工作流自带负面模板)。触发词取自 registry (config 优先, Civitai 自动补全次之) (LoraManager 自带触发词链已被此步覆盖节点54 text 断掉)。
6. **detailer 拼接** (若 `detailer:{face,hand,nsfw,eyes}`): 删未选 detailer 节点、重连 (删的节点不执行, 省时)。img2img: 切 ImpactSwitch select=2 + 覆盖主 KSampler denoise。
7. 返回 `{prompt, client_id, _seed}`。

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

单文件 SPA, 无框架。localStorage 存邀请码; `API` 常量硬编码 `https://api.airpaint.xyz`。
三屏: 登录(邀请码) / 工坊(主界面) / 暗房(对话迭代)。深色暖调(安灯琥珀), 无框架。
出图两步走 (翻译与生成解耦, 见 D17): 中文 -> `/api/translate` 拿 prompt_en + breakdown -> (可选预览/编辑) -> `/api/jobs` 传 prompt_en 提交。两按钮: 「生成」(翻译+提交一气呵成) 与「先看翻译」(翻译后展示「AI 理解」breakdown + 可编辑英文 textarea + 「再来一版」reroll, 改完点「确认生成」)。`/api/jobs` 不再翻译。
右侧参数: 工作流(合并后单选项) / 尺寸 / LoRA(角色+风格分组, 各自强度滑块, 预览) / 参考图上传(输入框下方全宽虚线投放区)。
轮询 `/api/jobs/{id}` 每 2s, 完成后展示图 + 入历史画廊(localStorage 缩略图, 最近 12 张)。出图后「继续迭代」进暗房: 换一版(txt2img 重抽, D31 替换意图) / 微调(img2img, 低 denoise)。

> `web/` 是独立 git 仓库 → `air041001/air`。但域名迁移后已**不再依赖 GitHub Pages**
> (前端由后端直接托管), 该仓库仅作备份, push 与否不影响线上。

## 配置 (server/config.yaml, gitignore)

关键字段: `comfy_url` `comfy_dir` `host/port` `allow_origins` `tokens` `daily_limit`
`timeout_seconds` `banned_words` `translate` `siliconflow_api_key` `siliconflow_model` `siliconflow_vision_model` `reroll_temperature`
`workflows.anima.{file,prompt_node,seed_node,size_node,lora_node,image_node,switch_node,denoise_node,detailer_nodes,sizes,quality_prefix}`;
顶层 `loras.<key>.{type,name,file,trigger,strength_model,strength_clip,description,preview}` (type=character|style; 未列出的文件启动时自动扫 Civitai 补全, D29)。

## 尚未实现 / 已知限制

- **意图解析**: 已有 LLM 结构化分解(scene/composition/mood/lighting/style) + TAGS/NL 信息分流(D28), 但仍属「翻译器」形态(语义压平为字符串), 无独立意图/语义 IR 层。方向见 `docs/PLAN-v4`。
- 用量/任务状态全内存, 重启清零 (Phase 3 计划 SQLite)。
- 单份合并工作流 AnimaFull; 加功能分支 = 改 AnimaFull.json + config 声明节点 + build_prompt 拼接逻辑 (D32)。
- 轮询取状态 (Phase 3 计划 WebSocket)。
