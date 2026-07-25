# 开发日志

## 第一阶段 MVP - 2026-07-24

目标: 把本机 ComfyUI 变成一个能发给朋友用的在线 AI 绘图小屋, 中文描述直接出图,
不需要朋友装任何东西、不暴露本机端口。

### 最终架构

```
朋友浏览器
   │
   ▼
GitHub Pages (https://air041001.github.io/air/)
   │  静态 HTML, localStorage 存邀请码
   │  HTTPS + Bearer token
   ▼
Cloudflare Tunnel (https://module-gotta-parade-influence.trycloudflare.com)
   │
   ▼
本机 FastAPI (127.0.0.1:8000)
   ├─ 邀请码鉴权 + 日限流
   ├─ 中文 → danbooru tag (词典命中 + Qwen3-8B 兜底)
   ├─ 内容过滤 + 并发=1 排队
   ▼
本机 ComfyUI (127.0.0.1:8188, 不暴露公网)
   └─ AnimaStandard V7 工作流出图
```

> **注 (2026-07-25 更新)**: 隧道与前端已迁移至固定域名 `airpaint.xyz`
> (命名隧道 + FastAPI 静态托管, 弃 GitHub Pages 与临时隧道), 见下方「补丁 2026-07-25」。
> 本图保留 MVP 当时的历史架构。

### 落地成果

- 后端: `server/main.py` (FastAPI + httpx + PyYAML), 单文件约 300 行
- 前端: `web/index.html` 单文件 SPA, 无框架依赖, 已推 `air041001/air` 仓库并开 Pages
- 词典: `server/dict.yaml` ~500 条中→danbooru 常用词映射, 覆盖发型/发色/眼睛/表情/服装/配饰等
- 工作流: `server/workflows/AnimaStandardV7.json`, 后端在运行时清洗掉前端专属节点
- 隧道: `cloudflared tunnel --url http://127.0.0.1:8000` 免费临时隧道
- 邀请码: 3 个 `friend-*` 已发给朋友使用

### 主要问题与解法

1. **本地没有 GitHub CLI, git push 也被 GFW 干扰**
   - 装 gh 便携版 (`E:/comfy-web/.tools/gh/bin/gh.exe`) 到项目内, 免管理员
   - 首次上传走 gh 的 Contents API (走 api.github.com, 通), 绕开 git 协议
   - 后续在代理开启时用普通 `git push` 即可

2. **仓库命名: 用户名带数字, 主页仓库不好看**
   - `air041001.github.io` 主页仓库路径干净但仓库名难看
   - 选项目仓库 `air`, 最终 URL: `https://air041001.github.io/air/`

3. **工作流从 API 排队直接崩 (`WidgetToString` 报 `NoneType`)**
   - 根因: `WidgetToString` (KJNodes) + `Image Saver Metadata` 依赖
     `extra_pnginfo["workflow"]`, 只有 ComfyUI 前端排队才带这个字段
   - 解法: `sanitize_for_api()` 在 `build_prompt` 前把这两个节点删掉,
     把 `Image Saver Simple` 替换成内置 `SaveImage`
   - 核心生成链路 (UNETLoader → LoraLoader → KSampler → VAEDecode) 一个不动

4. **工作流节点 ID 全部对不上**
   - `config.example.yaml` 里的 `prompt_node/negative_node/seed_node/size_node`
     是猜的占位符, 和实际 `AnimaStandardV7.json` 完全错位
   - 用 Python 脚本扒 workflow JSON, 校准为:
     - `prompt_node=54` (CLIPTextEncode 正面)
     - `seed_node=6` (KSampler)
     - `size_node=56` (EmptyLatentImage)
     - 不设 `negative_node`, 工作流自带的负面词模板质量更好
   - 追加了注入模拟测试, 启动前先验证节点 ID 都对得上

5. **翻译静默降级导致出图与提示词完全无关**
   - 原代码: Google 翻译失败时 `except Exception: return t (原文)`, 中文直接送 CLIP
   - 现象: 端到端返回 `status=done`, 但图是随机的
   - 解法: 换硅基流动 Qwen3-8B, 国内直连秒回, 用 LLM 直接产 danbooru 风格 tag
     (比传统翻译更贴合场景), 失败即 502 报错不再静默降级
   - 加了进程内 LRU 缓存 (500 条), 相同中文不重复调 API

6. **YAML 里空值把后端启动搞崩**
   - `dict.yaml` 里 `脸红:` 忘填值, YAML 解析成 `None`, `.strip()` 就爆
   - 补值 + `main.py` 加固: `{... for k, v in DICT.items() if v is not None}`

7. **shell 编码坑测试用例**
   - Git Bash 在中文 Windows 上把命令行里的中文按 GBK 传给服务端, UTF-8 解析报 400
   - 不是 bug, 但会误导排查方向
   - 端到端测试脚本改用 Python (`.tools/test_e2e.py`), 字符串天然 UTF-8

8. **FaceDetailer/Impact Pack 静默失效 (出图"很原生")**
   - 现象: 输出文件名是 `anima_00007_.png` 短名而非工作流自带的长格式, 风格原生无修复
   - 根因链:
     ① `sanitize_for_api` 把 `Image Saver Simple` 换成了内置 `SaveImage` → 文件名变短
     ② `build_prompt` 里只设置了 `seed_node` 的 seed, FaceDetailer/SEGS 等节点内部
        还是 `-1` (ComfyUI 前端的"随机"约定值)
     ③ Impact Pack 的 `onprompt` 钩子看到 `-1` → `np.random.default_rng(-1)` 抛
        `ValueError: expected non-negative integer` → 整个 Impact Pack 异常退出
     ④ FaceDetailer 人脸修复、wildcards 展开全部没跑 → 出图 = KSampler 直出原生风格
   - 解法: `build_prompt` 里统一扫描所有 int 型 `seed`/`noise_seed` 输入, 全部写成
     同一个正整数; 列表值(节点连接)用 `isinstance(int)` 跳过不动
   - 顺带把 `SaveImage` 的 `filename_prefix` 从写死的 `anima` 改成 `anima_YYYYMMDD`
     方便按日期区分。 (已确认,仅提示词太简单所以较为原始,具体Detailer节点待后续开发)

### 配置口径

- `translate: siliconflow` + `siliconflow_api_key` + `siliconflow_model: Qwen/Qwen3-8B`
- `daily_limit: 30` 每邀请码/天
- `timeout_seconds: 300` 单张图超时 (工作流带人脸修复, 偏慢)
- `banned_words: []` 内容过滤词表, 按需扩充
- `config.yaml` 已在 `.gitignore` 中, 不会误推公开仓库

### 已知限制

- Cloudflare 免费隧道每次重启换地址; 换了要同步改前端 `API` 常量和 `config.yaml` 的 `allow_origins`
- 用量计数是内存里, 后端重启清零
- 单并发排队, 一张约 15~60s, 队伍长了朋友要等
- 只有一个工作流 (AnimaStandard V7), 想加新工作流要重新导出 API JSON + 校准节点 ID
- 依赖本机三样东西同时运行: ComfyUI (bat 启动) + `python main.py` + `cloudflared`

### 端到端验证

输入: "白发蓝眼睛的猫耳少女, 微笑, 站在樱花树下"

Qwen 翻译输出:
```
1girl, white hair, blue eyes, cat ears, smile,
cherry blossoms, tree, outdoors
```

出图: `server/images/72c71389ea28.png`, 832×1216, 特征全对上。

### 下阶段候选

- [ ] `deploy.sh` 用 Contents API 更新前端 (代理不稳时的兜底方案)
- [ ] 一键启动 bat: ComfyUI + backend + cloudflared 一次拉起
- [ ] 用量持久化 (SQLite), 避免重启清零
- [ ] 前端加历史画廊 (localStorage 缓存缩略图)
- [ ] 支持局部重绘/ControlNet 工作流
- [ ] cloudflared named tunnel, 换固定域名, 不再每次重启改地址

---

## 补丁 2026-07-25 - 翻译修复 + 固定域名

### 完成内容

1. **翻译链路修复**: Qwen3-8B 翻译从「30s 超时 / 复读元词」修到「3s 干净 tag」。
2. **固定域名 airpaint.xyz**: 命名隧道替代临时隧道, 前端收进同域名, 退役 bind.sh 与 GitHub Pages 流程。

### 遇到的问题与解决方案

**问题 A: Qwen3-8B 翻译偶发复读 (`danbooru tagger, anime taglist...`), 怀疑模型太小**
- 尝试换 DeepSeek-V4-Flash -> 实测是推理模型, 翻译一句思考 27s+ (reasoning_tokens 450), 频繁超时。
- **根因**: 复读不是模型笨, 是 `enable_thinking:False` 放在了 `extra_body` 里, **硅基流动不认这个位置 -> 思考没关掉**。
- **解法**: ① `enable_thinking:False` 挪到请求顶层; ② user 消息加 `/no_think` 双保险; ③ 系统提示改 few-shot (2 个中文->tag 示范), 去掉元词; ④ 去掉 `frequency_penalty: 0.5` (它把模型推向冷门词循环)。
- **结果**: 留 Qwen3-8B (速度至上), 长句也 3s 出干净结构化 tag。详见 decisions.md D2/D3。

**问题 B: 临时隧道每次重启换 URL, 要同步改前端 + CORS + push, 维护负担重**
- 注册 `airpaint.xyz` 接入 Cloudflare, 建命名隧道 `airpaint`。
- **坑**: 旧 `cert.pem` 授权的是另一个域名 `airforchat.online`, 直接 route dns 把 `airpaint.xyz` 当子域名拼错了 (`airpaint.xyz.airforchat.online`)。
- **解法**: 备份旧 cert -> `cloudflared tunnel login` 重新授权 airpaint.xyz -> 重新建 DNS 路由 (`airpaint.xyz` + `api.airpaint.xyz` 永久 CNAME 到隧道)。
- **前端收同域名**: 后端 `GET /` 用 FileResponse 返回 `web/index.html`, 前后端同域, 改前端重启后端即生效, 不再 push。`web/` 仓库降级为备份。
- 写了一键启动 `.tools/start_airpaint.bat` (起后端 + 隧道)。
- 详见 decisions.md D4/D5。

### 后续计划

- Intent Engine: 当前是平铺「中文->tag」, 拟解析用户意图结构 (角色/动作/场景/风格/构图、否定、歧义), 见 decisions.md 待决策项。
- 用量/任务持久化 (SQLite), 见 ROADMAP Phase 3。
- 根目录纳入 git 版本管理 (目前仅 web/ 是仓库)。
