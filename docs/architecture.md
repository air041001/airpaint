# 架构

> 当前状态反映 2026-07-25 (airpaint.xyz 命名隧道迁移后)。
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
   ├─ Workflow Engine: 注入 prompt/seed/size, 清洗前端专属节点
   ├─ 单并发队列 (asyncio.Queue, GPU 串行)
   └─ 静态托管 / + /images
   ▼
ComfyUI  127.0.0.1:8188  (不对公网开放, 用 run_nvidia_gpu_fast_fp16_accumulation.bat 启动)
   └─ AnimaStandard V7 工作流 (含 FaceDetailer 人脸修复)
```

> 前端与 API 同域不同子域: 网页在 `airpaint.xyz`, API 在 `api.airpaint.xyz`。
> 跨子域算跨域, 故 `config.yaml` 的 `allow_origins` 仍需显式列 `https://airpaint.xyz`。

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
`translate(text)` 流程:
1. 按逗号切分, 逐段查 `dict.yaml` (851 条中→danbooru)。
2. 全命中 → 直接拼接返回 (零 API 调用, 最快)。
3. 有未命中 → 按 `translate` 配置选后端:
   - `siliconflow`: 整段中文送 Qwen3-8B (上下文完整质量更好), `/no_think` + 顶层 `enable_thinking:False` 关思考, 失败抛 502 不静默降级。LRU 缓存 500 条。
   - `google`: gtx 端点逐词翻译 (本机需翻墙, 已基本弃用)。
   - `none`: 未命中部分原样保留。

siliconflow 路径含**意图扩写**: `detect_characters()` 扫 `char_dict.yaml` 命中角色名 (Qwen3-8B 认不准角色 tag, 走词典); 裸角色名直接出 `tag, 1girl, solo` 跳过 LLM; 否则把角色 tag 作 `[Character: ...]` 上下文喂 LLM, LLM 扩写氛围/场景, 纯氛围输入不强制加人物。详见 decisions.md D12/D13。

### Workflow Engine (工作流注入)
`build_prompt(wf_name, prompt_en, w, h)`:
1. 读 `workflows/<file>.json`。
2. `sanitize_for_api(wf)`: 删 `WidgetToString` / `Image Saver Metadata` (依赖前端 `extra_pnginfo`, API 提交会崩); `Image Saver Simple` → 内置 `SaveImage`。
3. **统一 seed**: 扫描所有 int 型 `seed`/`noise_seed` 输入, 全写成同一正整数 (跳过列表型的节点连接)。修复 Impact Pack `np.random.default_rng(-1)` 崩溃 → FaceDetailer 人脸修复能正常跑。
4. 注入: `prompt_node.text = quality_prefix + prompt_en`; `size_node.width/height`; (不配 `negative_node`, 用工作流自带负面模板)。
5. 返回 `{prompt, client_id, _seed}`。

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
轮询 `/api/jobs/{id}` 每 2s, 完成后展示图 + 入历史画廊(localStorage 缩略图, 最近 12 张)。

> `web/` 是独立 git 仓库 → `air041001/air`。但域名迁移后已**不再依赖 GitHub Pages**
> (前端由后端直接托管), 该仓库仅作备份, push 与否不影响线上。

## 配置 (server/config.yaml, gitignore)

关键字段: `comfy_url` `host/port` `allow_origins` `tokens` `daily_limit`
`timeout_seconds` `banned_words` `translate` `siliconflow_api_key` `siliconflow_model`
`workflows.<name>.{file,prompt_node,seed_node,size_node,sizes,quality_prefix}`。

## 尚未实现 / 已知限制

- **Intent Engine**: 当前是「中文 → tag」的平铺翻译, 无意图解析 (谁/什么/风格/构图、否定、歧义)。是迈向「理解用户意图」核心目标的方向, 见 decisions.md。
- 用量/任务状态全内存, 重启清零 (Phase 3 计划 SQLite)。
- 单工作流, 加新工作流需导出 API JSON + 校准节点 id。
- 轮询取状态 (Phase 3 计划 WebSocket)。
