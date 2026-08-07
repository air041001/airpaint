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

---

## 第 10 条 2026-07-25 - 意图识别 (氛围扩写 + 角色词典)

### 完成内容

把「中文->tag 平铺翻译」升级为「理解模糊意图并扩写」: 用户说个感觉(治愈/春天/安静)就能出完整画面; 角色名可靠命中 danbooru tag。这是项目的差异化点。

### 遇到的问题与解决方案

**问题 A: 用户只会说「想要春天的感觉」, 平铺翻译没法处理模糊氛围**
- 系统提示词升级为 prompt-engineer 角色 + few-shot, 加规则: 氛围 -> scene+lighting+style 扩写。
- 主体策略: 看输入决定 (提人物才加 1girl, 纯氛围出风景)。
- 实测氛围扩写 1-3s, 几乎不增时。

**问题 B: Qwen3-8B 认不准角色 tag**
- 规则「命名角色用精确 danbooru tag」在小模型上是空头支票: 雷电将军->`lightning general`、珊瑚宫心海->`coral_palace_himeko`(错)、甘雨->没认出是角色(读成"甜雨")。仅示例里的三月七对 (在抄)。
- 解法: 新建 `char_dict.yaml`, 命中后把 tag 作上下文喂 LLM, LLM 只扩场景。契合词典优先哲学。

**问题 C: 裸角色名(如"甘雨")触发 LLM 疯狂编场景/武器, 7.9s + 噪声 tag**
- 快速路径: 输入只是角色名时直接出 `tag, 1girl, solo` 跳过 LLM, 0s。

### 端到端验证

输入「三月七在樱花树下, 想要治愈的氛围」->
`march_7th_(honkai:_star_rail), 1girl, solo, cherry blossoms, tree, petals, spring, gentle breeze, warm sunlight, soft lighting, peaceful, calm, anime style` (4.1s)
-> 出图 `images/6f59dada02bd.png`。详见 decisions.md D12/D13。

---

## 第 11 条 2026-07-26 - 启动脚本修复 (bat 编码 + 行尾)

### 完成内容

修 `start_airpaint.bat` 双击秒退 (致网站 1033); 新增 `start_tunnel.bat` 单独补隧道。

### 遇到的问题与解决方案

**现象**: 双击 `start_airpaint.bat` 后网站 1033。排查发现后端 8000 健康 (`{"ok":true,"comfy":true}`), 但 cloudflared 进程不存在; 手动跑同一条 `cloudflared tunnel run airpaint` 却秒连 4 连接。命令没问题, 是 bat 没把 cloudflared 跑起来。

**三个叠加根因**:
1. **LF 行尾**: bat 是 Unix LF, cmd.exe 解析 .bat 需 CRLF。LF 时 `REM`/`echo` 行被切碎, `REM 检查后端是否在跑` 被当命令执行。
2. **if 块括号**: `if errorlevel 1 (...)` 块内 echo 含 `(127.0.0.1:8188)`, 扰乱 cmd 块匹配, 报 `. was unexpected at this time`, bat 中断, cloudflared 行没执行。
3. **UTF-8 编码**: bat 存 UTF-8, cmd 按 GBK(codepage 936)解析; `chcp 65001` 只改显示不改解析编码, 中文行偶发乱码报错。

**为什么之前"看着能跑"**: `start cmd /k` 启后端那行纯 ASCII 且位置靠前, 侥幸执行 -> 后端活、隧道死 -> 1033。`start_tunnel.bat` 没有子窗口, 主窗口一退就秒开秒关, 才暴露问题。

**解法**: bat 存 GBK + CRLF + `if errorlevel 1 (...)` 块改单行 + echo 行去括号 + 去 `chcp 65001`。实测双击正常起 cloudflared, 4 连接, 站点 200。详见 decisions.md D14。

### 下一步

回到意图识别拓展 (decisions.md 待决策项: 否定/歧义/构图解析)。

---

## 第 12 条 2026-07-26 - Prompt Engine 三层重构 (角色优先 + Known-tags 上下文)

### 完成内容
翻译链路从「词典命中后整段送 LLM」改成三层: 角色子串匹配、词典匹配(剩余文本)、LLM 只翻未命中。LLM 上下文显式给 Known character/attribute tags, 只输出新增 tag, 后端 prepend 已知 tag。采纳用户 v2 设计的结构, 避开开倒车部分。

### 关键改动 / 为什么
- **角色优先 + 删名再查词典**: 之前先词典后角色, 角色名不在词典走 miss, 绕。
- **Known attribute tags 喂 LLM**: 之前整段原文(含已命中的"白发")重发, LLM 重翻一遍; 现在只翻 misses, 省 token + 防重复。
- **LLM 只出新增 tag, 后端 prepend**: 已知 tag 不会被 LLM 改坏。
- **prompt 不拆 3 份**: 保留单 prompt + few-shot + 内容规则(氛围 vs 具体), 不按字数分(方案 ≤15 字归 mood 那套分不准, "白发猫耳少女" 6 字是 specific)。
- **沿用 D2 修复**: 顶层 enable_thinking + /no_think + 无 frequency_penalty (方案放 extra_body / 加 freq 0.5 会重新引入复读 bug)。

### 验证
match_characters / 快速路径 / none 路径 / LLM 直连 / 端到端 全过。「三月七在樱花树下, 想要治愈的氛围」输出 `march_7th_(honkai:_star_rail), cherry blossoms, ..., peaceful, calm atmosphere` (角色 tag 不重复, 氛围扩写正确)。详见 decisions D15。


## 第 13 条 2026-07-26 - LoRA 模式 (前端可选 LoRA, 后端注入)

### 完成内容
前端下拉选 LoRA, 后端把 LoRA 写进工作流 LoraManager 节点 (节点5) 的 loras widget, 并把触发词拼进正面提示词。新增 `GET /api/loras`, `POST /api/jobs` 加 `lora` 字段, config.yaml 加 `loras` 块 + workflow `lora_node`。

### 关键改动 / 为什么
- **注入走 loras widget, 不走 text 字段**: 查 LoraManager 源码 (lora_loader.py:151 `del text`) 发现节点5 的 text 字段执行时被直接删掉, 只服务前端 autocomplete; 加载 LoRA 只认 `loras.__value__`。计划里猜的 `<lora:file:1>` 文本语法 / `[[file,sm,sc]]` 数组格式都不对 (后者是 lora_stack 输入的格式, 节点5 没该输入)。
- **__value__ 元素是对象不是数组**: `{name, strength, clipStrength, active}`, `active` 必须为 true 否则 `_collect_widget_entries` 跳过。
- **触发词手动拼 prompt**: LoraManager 自带触发词链 (节点5 output2 -> 37 TriggerWordToggle -> 46 StringConcatenate -> 48 -> 54), 但现有 `build_prompt` 的 `set_input("prompt_node","text",...)` 已覆盖节点54 text 断掉此链, 故触发词必须自己 prepend (quality_prefix 之后, 用户词之前)。
- **API 用本地 `/api/` 前缀 + Request+req.json()**: 不照搬计划里的 `/loras` `/generate` + Pydantic GenerateRequest, 跟现有 /api/jobs 一致。

### 验证
build_prompt 单测: 无 lora 时 node5.loras 保持 `{"__value__":[]}` 不动; 有 lora 时写入对象数组 (active:true) + node54.text 含触发词; 未知 lora 抛 400。ComfyUI 实际加载权重待用户用真实 lora 文件端到端验。详见 decisions D16。

### 补充 (同日, 端到端测试后)
- **LoRA 权重可调**: 加 `strength` 字段 (前端强度输入框 0~1, 后端 build_prompt 用它同时覆盖 model/clip strength, 不传用 config 默认 1.0)。原 config 写死 1.0 没法调。
- **salt 文件修正**: 原配 `salt(finale).safetensors` 是错的 (非 anima 版), 改 `salt(finale)-anima-v1.0.safetensors`。salt_milk_sailor 触发词补 `blue skirt`。前端名字保留 Finale/Milk 英文不翻译。
- **端到端验证通过**: 用户实测 LoRA 正常加载且有效果。


## 第 14 条 2026-07-27 - 提示词两步走 (可选预览/编辑)

### 完成内容
把「一键出图」拆成翻译与生成解耦: 新增 `POST /api/translate` (只翻译不排队, 不计入 image 限额); `POST /api/jobs` 改收 `prompt_en` (破坏性, 不再后端翻译)。前端两按钮: 「✨ 直接生成」(翻译+提交一气呵成, 默认 UX 不变) 与「🔍 预览提示词」(翻译后可编辑 textarea, 改完再「确认生成」)。

### 关键改动 / 为什么
- **翻译独立成 /api/translate**: 暴露自动扩写能力 (用户能看到"春天"扩成了啥), 且给 LLM 偶尔翻坏兜底 (可手改)。translate 有 LRU 缓存, 预览过再直接生成是缓存命中, 不二次花 token。
- **/api/jobs 破坏性改 prompt_en**: 唯一消费方前端 (no-cache 必刷新) + e2e 测试 (同步改), 无遗留兼容负担。
- **translate 不计 image 限额, 用 verify_token**: image 限额守 GPU; translate 只花 LLM token 不占 GPU, 不该被 30 张限额挡。新增 `verify_token` (仅校验 token 不查日限) 给非出图接口用。
- **默认仍一键**: 「直接生成」对用户是一步 (翻译后台透明完成), 现状 UX 不变; 「预览」是可选精细入口。

### 验证
main.py 语法 + 路由注册 (/api/translate, /api/jobs) + 前端 JS `node --check` 通过。端到端待用户实测。详见 decisions D17。

## 第 15 条 2026-07-28 - Anima 提示词规范 + LLM 结构化意图分解

### 完成内容
治翻译三病(场景误读/隐喻字面化/构图丢失)。LLM 层从「直接吐扁平 tag」改为「先结构化分解(scene/composition/mood/lighting/style)再吐 TAGS 行」; `translate()`/`siliconflow_translate()` 返回 `(prompt_en, breakdown)`, `/api/translate` 回传 breakdown, 前端预览展示「🤖 AI 理解」。同时规范化 Anima 提示词: `quality_prefix` 改 Anima 官方(`score_7, safe` 等); 工作流固化负面补构图否定词; LLM 禁输出 quality/score/realistic tag。

### 关键改动 / 为什么
- **结构化分解治扁平 tag 表达力不足**: 扁平 tag 袋天生表达不了空间关系(看向窗外)和隐喻(未来的方向)。强制模型对 scene/composition/mood 分别表态, 隐喻落 mood 不再字面, 空间关系落 composition 带锚点(facing/from behind/looking out)。
- **不换模型先重构**: 用户选项。先隔离变量(提示词 vs 模型), 实测 3 句未见过的输入泛化正确, 证明 8B + 结构化够用, 暂不升模型。
- **thinking 仍关 + config 开关**: 结构化字段本身是强制表态, 不依赖 CoT, 保住 D2 延迟/复读安全; `translate_enable_thinking` 留作隐喻仍弱时的 A/B 开关。
- **否定解析弃用**: 联网查证 Anima 负面是极简常量(WAI-Anima 式), 不随输入变; config 本就不配 negative_node。原 Intent Engine 的否定语义解析无必要, 砍掉。
- **TAGS 行降级**: 8B 偶发格式不稳, 无 TAGS 行则整体当 tag(等同旧行为), 不崩。

### 验证
main.py `py_compile` 通过; 前端内联 JS `node --check` 通过; 实测 `translate()` 3 句未见过的输入(天台夜景/雨天公交站/神社樱花): 场景正确、构图锚点齐全、隐喻非字面、`realistic` 被禁。详见 decisions D18。

## 第 16 条 2026-07-29 - 抽卡 re-roll + Anima tag 顺序规范化

### 完成内容
两个轻量灵感(inspiration ①⑥)。① 抽卡 re-roll: 同一句中文, LLM 高温重出一版不同结构化分解, 朋友在预览区点「🎲 再来一版」探索不同风格, 不用重打字。⑥ tag 顺序规范化: 按 Anima 期望序 `count -> character -> general` 重排 prompt_en。

### 关键改动 / 为什么
- **re-roll 跳过缓存读写**: reroll 是探索性, 写回缓存会顶掉正常翻译的首版。跳过读写 = 每次重抽新鲜, 正常预览仍命中首版, 互不污染 (D19)。
- **re-roll = 高温 + 发散指令**: temp 0.4 -> 0.9 (config `reroll_temperature`), user content 前置"给一版不同创意解读"指令; `/no_think` 仍是首 token, 不破坏 D2 思考关闭。
- **re-roll 仅 LLM 路径**: 快速路径(全命中词典, breakdown=null)无 LLM 可变, 前端隐藏按钮不报错。
- **normalize_tag_order 零风险重排**: 只把 count(1girl/solo)从末尾提到 character 前, 不增删不去重; quality 仍由 build_prompt 外层 prepend (D20)。`translate()` 四条返回路径统一走它。
- **NL 追加本次不做**: Anima 编码器支持末尾自然语言, 但改出图大需单独验证, ⑥ 只做排序收尾, NL 留待 ③/⑤。

### 验证
main.py `py_compile` 通过; 前端内联 JS `node --check` 通过; 本地实测 `normalize_tag_order` + `translate()` 各路径(快速全命中 / siliconflow / none): prompt_en 顺序为 `count -> character -> general` (裸角色从 `raiden_shogun, 1girl, solo` 修正为 `1girl, solo, raiden_shogun`); `reroll=True` 在快速路径不报错(无 LLM 可变, 返回同结果)。airpaint.xyz 实跑确认: 「天宫心坐在桌前望着窗外 看起来心事重重」reroll 两版分别落到 classroom/golden hour/minimalistic 与 bedroom/soft daylight/contemplative, melancholic (scene/lighting/mood 全变), 均为 `1girl, amamiya_kokoro_(hololive), ...` = count->char->general 序; 天宫心走 char_dict 命中、心事重重落 mood 非字面。详见 decisions D19/D20。

## 第 17 条 2026-07-29 - 修: 手机访问 /api/jobs 400 未知工作流

### 完成内容
朋友手机访问, `POST /api/translate 200` 但 `POST /api/jobs 400 未知工作流`。根因: `loadWorkflows()` 只在页面加载 / 点「保存」时跑(用当时的令牌), 而 `doTranslate()` 是**调用时实时读令牌**。朋友页面加载时无令牌(或过期) -> 工作流下拉框空, 事后填了令牌直接点生成 -> 翻译过(实时令牌)、但 `$('workflow').value===""` -> jobs 400。手机上不点保存直接生成必踩。

### 关键改动 / 为什么
- `submitJob()` 提交前 `if (!workflows.length) await loadWorkflows()` 补拉一次(用当前令牌), 仍空才报错提示。保证不管令牌何时填, 提交时下拉框一定有值。未改后端(400 检查本身正确), 纯前端补拉。
- 非 ①⑥ 改动引入, 是既有的令牌/工作流加载时序耦合坑。

### 验证
前端 `node --check` 通过; 后端 `FileResponse` 现读 + `/` no-cache, 无需重启, 朋友刷新即生效。

## 第 18 条 2026-07-29 - 词典热更新 (char_dict / dict.yaml 存盘即生效)

### 完成内容
char_dict.yaml / dict.yaml 原先启动时一次性载入, 加角色/词条要重启后端(中断朋友远程会话)。封装 `HotDict` 类: `.get()` / `.items()` 时按 mtime 判断, 变了才重载, 存盘后下一句翻译即生效。config.yaml 不动。

### 关键改动 / 为什么
- **mtime 轮询而非 watchdog**: 每次访问一个 stat(微秒级), 无依赖, 契合"翻译时才用词典"的访问模式; 后台线程+依赖对单文件过重 (D21)。
- **整体重建再赋值**: `self._d = {...}` 先建后绑, 读端不会见半成品。
- **坏 YAML 兜底**: 解析失败保留旧词典 + 打印 `[HotDict] 重载 ... 失败` 警告, 不崩翻译(旧代码启动时坏文件直接崩)。
- **调用处零改动**: HotDict 暴露 .get/.items, `DICT.get` / `CHAR_DICT.items` 不用动; DICT key 小写, CHAR_DICT key 不小写。

### 验证
`py_compile` 通过; 临时文件测试: 加词条后 `.get` 立即命中, 坏 YAML 保留旧值+打印警告不崩; 真实词典加载正常 (char 157 / dict 929 条)。详见 D21。

## 第 19 条 2026-07-30 - Face+Hand detailer 精修工作流 + 双工作流选择

### 完成内容
启用工作流里 MUTE 的 FaceDetailer+HandDetailer 作出图后精修(修脸+手细节)。拆成两个工作流: `anima`(快速默认, 原版) + `anima-detailer`(精修, ~90s), 前端下拉选。detailer 调参把 186s 降到 90s。剥掉 Image Comparer 展示节点(会干扰后端取图)。

### 关键改动 / 为什么
- **子 agent 查源码+磁盘确认零代码改动**: detailer 的 seed 是连 rgthree `Seed` 节点34(widget=-1), 运行时随机成正整数, 不触发 Impact Pack `default_rng(-1)` 崩; sanitize 不碰 detailer 组; 输出链 `6->43->27(Hand)->29(Face)->13` 自通; 检测器 swap 到磁盘有的 face_yolov8m.pt / hand_yolov8s.pt。
- **Image Comparer 必剥**: 67/69 是展示节点, 继承 PreviewImage(OUTPUT_NODE), 中间预览图进 /history; `submit_and_wait` 取"第一个有图的节点"会误取中间图而非最终 SaveImage。本身不崩(extra_pnginfo=None 只 save_images), 但干扰取图 -> `sanitize_for_api` 加 `Image Comparer (rgthree)` 到剔除集。
- **detailer 调参**: max_size 1536->1024、steps 16->12。病根是裁剪区被放大到 1536px(比主图还大)再采样, 每步 2.87s; 降到 1024+12步, 186s->90s(手14/手12/脸15s)。12步是下限, 再降不起作用(denoise 已低 0.26-0.4)。
- **拆两工作流而非动态拨组**: 后端 /prompt 用 API 导出 JSON(只含活跃节点), 不能动态切 MUTE 组; 换功能=换 JSON 文件。两工作流=两份 JSON, 复用前端既有下拉(/api/workflows), 默认快速。以后 ControlNet/inpaint 同套路。注入节点两版相同(54/6/56/5)。
- **EditDetailerPipe 提示词作者已填**: Hand=`[CONCAT] hand, perfect hands`, Face=`[CONCAT] {face|face,detailed face}`, concat 叠加在主提示词上, 不用自己填。

### 验证
实测: 快速版出图正常, 精修版 ~90s(脸/手细节明显改善), 前端下拉默认快速、两个工作流都能出图。详见 decisions D22 / workflow-anatomy 启用查证。

## 第 20 条 2026-07-31 - ③ 参考图理解 (视觉 LLM 提氛围, 走 txt2img)

### 完成内容
朋友上传参考图, Qwen3-VL-8B-Instruct 提取氛围/配色/构图/场景/光影转 tag, 与文本主体合并后走正常 txt2img (图不进 ComfyUI, 与未来 img2img 不冲突)。前端加参考图上传(缩到768px), 后端加视觉 LLM 路径。

### 关键改动 / 为什么
- **VL 模型选型**: Qwen3.5-4B(免费)做不了视觉(超时, 疑似纯文本); Qwen3-VL-8B-Instruct 能用(1.1s, 已确认识图)但收费 $0.18/M输入 + $0.68/M输出 ≈ $0.0007/次, 月几毛封顶, 可忽略。50万免费额度仅够 ~230 次(3周用完)+ 要切换平台, 不值。config `siliconflow_vision_model` 可调。
- **图是氛围参考, 非图生图**: 视觉 LLM 提取 mood/color/lighting/composition, 不照搬图主体(除非文本指定); 图只用于提 tag, **不进 ComfyUI**, 走现有 txt2img。与 ⑤ img2img(LoadImage->VAEEncode->二采)正交, 不冲突。
- **merge**: 有图时 char_dict+dict 仍从文本预匹配(可靠), 视觉 LLM 拿图+上下文(Known tags+Remaining)出结构化 breakdown+TAGS, `normalize_tag_order` 拼接。视觉 LLM 替代文本 LLM(一次调用处理图+文)。
- **Qwen3-VL 不接受 enable_thinking**(会 400), 视觉调用不带该参数(文本 LLM 的 Qwen3-8B 仍带)。
- **normalize 加保序去重 + dict 多 tag 拆分**: 重复根因有二: ① VL 偶发复读, normalize_tag_order 加 `seen` 去重(保留首次); ② **dict 值可能是多 tag("少女"->"girl, young, cute, innocent"), 原 `hits.append` 当成一个 blob, 跟 VL/LLM 同名 tag 撞出伪重复**(normalize 按整元素去重逮不到)。修法: dict 命中按逗号拆开 `hits.extend`。②是老 bug, 文本路径也有, ③ 撞上才暴露。
- **前端**: 参考图上传 canvas 缩到 768px/JPEG 0.85 再 base64(省 token+加速); prompt+image 至少一项; reroll 对视觉也生效。

### 验证
py_compile + node --check 通过; 实测 translate("少女, 樱花树下", image=红图) -> `1girl, under cherry blossom tree, serene, peaceful, gentle, soft lighting, warm lighting, flat shading` + breakdown 五字段齐全; 文本主体(少女/樱花)+图氛围正确合并; 去重生效。详见 D23。

## 第 21 条 2026-07-31 - 前端改版: 登录门禁 + 公告/教程下拉面板 + 移动端

### 完成内容
邀请码作登录唯一手段(暂时), 全屏登录页验证后才进主面板; 标题下方加「☰ 公告/教程」按钮, 点开下拉面板(全宽居中, 桌面/移动同布局; 更新公告带日期 latest-first + 简版教程: 翻译/LoRA/参考图/工作流/re-roll)。

### 关键改动 / 为什么
- **登录门禁**: 全屏 #login 遮罩, 「进入」调新增 `/api/auth/check`(verify_token, 不查日限不耗配额)验证 -> 200 进主面板, 401 提示无效。页面加载有存 token 自动验证, 无则显登录。主卡片移除原 token 输入框(token 改隐藏 input, api() 不用改); header 加「退出」。
- **为何新加 /api/auth/check 而非用 /api/workflows**: /api/workflows 用 auth(查日限), 朋友达日限时用它验登录会 429 登不进; /api/auth/check 用 verify_token 只验 token (D24)。
- **下拉面板(非侧拉抽屉)**: 标题下方居中按钮行, 点开向下展开全宽面板(max-680, 居中)。选下拉而非侧拉: 侧拉在桌面只占右侧一小条要凑过去看, 下拉全宽居中桌面/移动同款不用适配。两 tab: 更新公告(带日期, latest first)/ 教程(简述, 不手把手)。
- **标题居中**: 按钮挪到标题下方居中(toolbar), 不抢标题居中位置; 下拉面板全宽居中, 桌面/移动都不挤。
- **loadWorkflows 改抛异常**: 原来内部 try/catch 写 #status, 登录流程需它抛(enterApp 才能 catch 401); 改抛后 submitJob 等调用方自己 catch。

### 验证
py_compile + node --check 通过; /api/auth/check 逻辑简单(verify_token 复用)。详见 D24。

## 第 22 条 2026-08-01 - ⑤ 对话迭代 MVP (骨架 + 换一版/保氛围)

### 完成内容
新增「对话迭代」模式(与单张生成切换)。会话线程: 首图 start -> 每轮 [换一版] / [保氛围] + 可选 delta 改动。后端 SESSIONS 内存会话 + /api/dialog/turn + /api/dialog/{sid}, 复用 _enqueue 入队(worker 不动)。

### 关键改动 / 为什么
- **显式路由不猜意图**: 每张图挂操作按钮, 用户点按钮决定路由(A 换一版 / D 保氛围), delta 只作提示词增量。Qwen3-8B 猜意图又慢又不准, 别让它干 (D25)。
- **A 换一版**: delta 有则 `session.raw += delta` 重翻译; 无则复用 `current_en` 换 seed(免 LLM 调用)。
- **D 保氛围**: 上一张图 -> iterate 视觉全量提取(锁主体+氛围)再变体。与 ③ 的 vibe-only 不同: ③ 是用户参考图禁抄主体, D 要锁住实际出图。新增 VISION_ITERATE_SYSTEM_PROMPT + mode 参数。
- **_enqueue 抽取**: create_job 的入队段(USAGE+1/JOBS/QUEUE)抽成共享 helper, create_job 与 dialog 共用, worker 零改动。
- **前端**: 模式切换分段控件 + 对话视图(线程/操作按钮/delta 输入/轮询)。workflow/size/lora 复用主卡片选择。

### 验证
py_compile + node --check 通过; 本地模拟: start translate+入队 ✓, redo 无delta复用current_en ✓, redo带delta("换成白天"->daytime,bright)✓, vibe iterate 全量提取图主体+氛围(blue hair, red horns, white blouse, kitchen...)✓。B(img2img)下阶段。
**实跑发现并修**: 保氛围报"还没有已生成的图" -- turn 记录里 image 字段恒 None(worker 把图写进 JOBS), 原查 `turn["image"]` 找不到; 改为从 JOBS 按 job_id 找最新出图。

## 第 23 条 2026-08-02 - img2img (B) + 对话微调 + 保氛围删除

### 完成内容
⑤ 的 B 路由(img2img 微调)落地。子 agent 查 img2img 链路(ImpactSwitch 路由/LoadImage 格式/denoise), 确认无硬阻塞。第三份工作流 `anima-img2img`(拨 Load Image 组导出), 后端 config 驱动注入(image/switch/denoise)。单张模式选 img2img 工作流即可上传图改; 对话迭代加「微调」按钮(上一张图 -> img2img + denoise)。保氛围(vibe)删了(跟换一版重叠 + 内在矛盾)。

### 关键改动 / 为什么
- **子 agent 查源码确认**: ImpactSwitch 42 select(1/2)可 set_input 覆盖(替换连接, lazy evaluation 确保未选链路不执行); LoadImage 0 要文件名字符串(非 base64, 放 ComfyUI input 目录); KSampler 6 denoise 是连接(1.0), 可 set_input 覆盖为低值。
- **upload_image_to_comfy**: POST /upload/image 上传 -> 拿 filename -> set_input。单张模式图走 /api/jobs(不走 /api/translate); 对话微调从 JOBS 读上一张图。
- **build_prompt 扩展**: image_filename+denoise 参数; config 有 image_node 时 set select=2+image+denoise。_enqueue 共用(create_job + dialog), worker 全链路传递。
- **保氛围(vibe)删了**: 实测发现跟换一版高度重叠(都是文字驱动), 且 reference(保氛围换主体) vs iterate(锁主体)内在矛盾, 一个 mode 搞不定两种。后端 vibe action 休眠保留, 前端按钮删。
- **UX 修正(实跑发现)**: img2img prompt 要**完整画面描述**非指令("穿蓝色连衣裙的少女在海边" 不是 "改变穿搭"); denoise 越高越偏离(默认降到 0.35); img2img 模式 placeholder 改"描述完整画面"。

### 验证
py_compile + node --check 通过; build_prompt 注入验证: img2img 模式 select=2/image/denoise 全覆盖 ✓, txt2img 模式保持原样 ✓。详见 D26。
**dict 子串匹配修复**: 原 dict 精确匹配逗号段, NSFW 词嵌在短语("裸足少女")不命中 -> 走 LLM -> Qwen3 双层安全过滤(搜到 CSDN: 拦成人色情)丢词。改为 `match_dict_words` 子串匹配(最长优先, len>=2 防误伤), dict 词嵌在任何位置都命中 -> 直接拼进 prompt_en -> 绕过 LLM。实测 `match_dict_words("猫耳少女白发蓝眼睛")` 全命中。

## 第 24 条 2026-08-03 - 前端大改: 工坊 / 暗房三屏重写 (Tailwind)

### 完成内容
把 `web/index.html` 从 783 行「居中窄卡片 + 两个平级 tab」整个重写为三屏结构, **后端零改动**。落了 ROADMAP 挂着的「Tailwind CSS 重写界面」。先在 `web/prototype.html` 用假数据和用户磨了三轮布局/视觉, 定稿后接真实逻辑。原文件备份 `index.html.bak`。

三屏:
- **登录**: 渐变标题 + 价值主张 + 邀请码卡片 (原邀请码门禁逻辑不变)。
- **工坊(主界面, 全功能铺开)**: 三栏占满宽, 不再小卡片留白。左中大画布结果 + 右侧「AI 理解」面板(scene/composition/mood/lighting/style 英文 tag + 可编辑英文 prompt, 自适应填满高度); 右栏参数 inspector: 工作流卡片(快速/精修/图生图标时长)、尺寸宫格(只留后端支持的 832×1216 / 1216×832 / 1024²)、LoRA 下拉+强度+预览; 下方输入框 + 历史缩略图条(localStorage 持久化最近 12 张)。结果图上「下载」「继续迭代」。
- **暗房(从结果图进入)**: 对话迭代不再和单张平级。大图在上 → 改动输入+换一版/微调分段控件(全宽紧贴图下) → 右侧竖向血缘演化条(初版→v2→当前, 标 action/denoise)。参数从工坊继承不重设。

### 关键改动 / 为什么
- **两个模式形态确实不同, 不该平级**: 单张=无状态设置先行的工坊; 对话=以上一张图为基准、有血缘的暗房。用户点「看到这张图好看才有了别的点子」才是迭代的真实触发, 故迭代从结果图进入, 不作首页 tab。详见 D27。
- **AI 理解面板长在结果旁**: 原「先看翻译」要点按钮才露出结构化分解, 朋友感知不到「AI 真理解了中文」这个核心卖点。新版 breakdown 常驻图右侧, 翻译/编辑流程两段式对齐原逻辑: 输入栏「生成」=静默翻译直接提交; 「先看翻译」=填进右侧面板, 面板内「确认生成/再来一版/取消」(在哪编辑在哪确认)。
- **历史条正式设计**: 原 `addToGallery()` 已有但只是裸 div 塞页底像补丁; 改为画布下方横滑条 + localStorage 持久化(ROADMAP「历史画廊」项)。点旧图只放大/下载, 不做「基于旧图进暗房」(后端无 start-from-image, 且判定没人用)。
- **工作流/尺寸从下拉改卡片/宫格**: 三个工作流可视化选择 + 时长提示, 比下拉更体现多工作流工作量; 尺寸宫格只渲染后端 `workflow.sizes` 返回值。
- **img2img 联动保留**: 选图生图时重绘幅度块才出现、上传按钮文案/placeholder 切换、图走 /api/jobs 不走 translate。沿用原 `updateImg2ImgUI()` 逻辑。
- **Tailwind CDN 而非本地构建/前端框架**: 前端是薄客户端, 后端三层引擎才是简历/毕设主角; 零构建最贴「自己用+少量朋友」定位, 单文件内部按屏/功能分节保持可维护。CDN 需联网, 若朋友网络打不开再换本地构建。详见 D27。
- **教程重写**: 原五段是「功能说明」(LoRA 是什么), 新版改成「用法+避坑」(图生图要写完整描述非指令、denoise 0.25-0.35 微调、迭代两种模式区别), 把后端 D26 的 UX 要点传到前端。
- **移动端**: 窄屏竖排(输入置顶→图→AI 理解→参数抽屉), 参数默认折叠靠顶栏「参数」按钮呼出; 暗房天然竖排(大图→输入→横向血缘)。

### 技术坑
- **隐藏 select 必须保留**: 重写时一度删了可见的 workflow/size/lora 下拉改用卡片/宫格, 但提交逻辑(`jobsBody`/`dlgTurn`)全程靠 `$('workflow').value` 等取 id 值。删掉会全 null、出图白屏。最终在参数栏放三个 `class="hidden"` 的原生 select, 卡片/宫格点击时同步其 value, 提交逻辑零改动。
- **Tailwind CDN 默认边框色是浅灰**: `border` 类默认 `gray-200`, 暗色主题下每个卡片一圈白框; 全局 `*{border-color}` 被 CDN 注入顺序压过。最终在 `tailwind.config` 里设 `borderColor: () => ({DEFAULT:'#211e33'})` 从 preflight 根治。原生控件(textarea/range/select)还要 `color-scheme:dark` + 全局背景才不发白。
- 自查: 提取内联 JS 跑 `node --check`, 并正则校验所有 `$('id')` 引用的 72 个 id 全部在 HTML 中定义, 无遗漏。

### 验证
node --check 通过; 72 个元素 id 引用全部存在。待用户真机点一遍(生成/先看翻译/图生图上传/继续迭代/历史条)。后端未动。

## 第 25 条 2026-08-07 - 提示词格式优化: quality_prefix 官方化 + system prompt 信息分流重写

## 第 26 条 2026-08-07 - LoRA 工程: 自动扫描 + 多 LoRA + 分类

### 做了什么
1. **config.yaml 分类**: loras 加 `type: character|style` 字段; 新增 BlueArchiveStyleB1 (风格, trigger `@BlueArchStyle`) + denia_lorav4 三个角色变体 (denia_white/denia_sigrika/denia_black, 同一文件三个触发词)。
2. **自动扫描 + Civitai lookup**: 后端启动时后台扫 `comfy_dir/models/loras/`, 对 config 未覆盖的 `.safetensors` 按 SHA256 查 Civitai API 取 trainedWords/modelName/tags。结果缓存到 `server/lora_cache.json`。SHA256 优先读 LoraManager `.metadata.json` (已有, 不重算)。`/api/loras/refresh` 可手动触发重扫。
3. **多 LoRA 注入**: `build_prompt` 的 `lora_key: str` -> `lora_keys: list[str]`, loras widget 数组注入多条, trigger 全部拼进 prompt。`/api/jobs` 和 `/api/dialog/turn` 接收 `loras: list[str]` (向后兼容旧 `lora: str` 单选)。
4. **`/api/loras` 改为分组**: 返回 `{characters: [...], styles: [...], other: [...]}`, 每条带 `configured` (是否有触发词) + `source` (config/civitai)。

### 遇到什么 / 怎么解
- **LoraManager TriggerWord Toggle 显示 "no triggerwords detected"**: 调研发现 LoraManager 的 civitai 元数据同步没工作 (`.metadata.json` 里 `civitai` 字段一直空, scan 也不回填 `.civitai.info`)。即使修好, Civitai `trainedWords` 很多作者不填, 且只含基础触发词不含服装变体。结论: 不依赖 LoraManager 自动检测, 自己直接读 Civitai API + config 手动维护。
- **Civitai trainedWords 不可靠**: denia_lorav4 和 BlueArchiveStyleB1 的 trainedWords 都是空, 但实际有触发词 (在 HTML description 里)。不解析 description (格式不统一, 误判更糟), 改为: 有 trainedWords 的自动可用, 没的标记 `configured: false` 让用户手动配 config。
- **SHA256 计算大文件慢**: BlueArchiveStyleB1 137MB。优先读 LoraManager 已算好的 `.metadata.json` 里的 sha256, 没有再现算。

### 下一步
前端需配合: LoRA 选择器从单选改为分组多选 (角色最多1 + 风格最多1); 显示 `configured: false` 标记; 可选加刷新按钮调 `/api/loras/refresh`。见 D29。

### 完成内容
两件事, 都围绕"喂给 Anima 的提示词格式"。

**(A) quality_prefix 官方化**: 查证 tungsten.run 上 Comfy Org 官方 Anima checkpoint 说明 + 社区指南, 发现原前缀用 Illustrious/Animagine 体系(score_7, very aesthetic), 非 Anima 官方推荐。改为官方明确支持的 `masterpiece, best quality, newest, absurdres`(human score + period + meta)。改 config.yaml 三处 + 同步 config.example/README/architecture。

**(B) system prompt 信息分流重写**: 用户反馈 LLM 输出 TAGS 后又用 NL 重复翻译输入。根因: 旧示例的 NL 全是 TAGS 句子化重写, 教会 LLM 重复。重写 SILICONFLOW_SYSTEM_PROMPT 为信息分流模型。详见 D28。

### 关键改动 / 为什么
- **示例与规则自相矛盾是根因**: 规则第8条写"别重复", 但 3 示例的 NL 全是 TAGS 的句子化重写。LLM 跟示例走不跟规则走。治本=重写示例。
- **从"分解报告"转"信息分流"**: 人写高级提示词是"每信息点选 tag/NL/权重一种形式", 不是"填5字段+汇总TAGS+写NL描述"三重表达。新 prompt 明确: 5字段=给人看(不进anima); TAGS+NL=喂模型, 不许重复。
- **HARD RULE 可机械判定**: "NL 不得复述 TAGS 已有 tag, 全是 tag 则留空" 替代模糊的"别重复"。
- **加 How-to-decide + Self-check + Weight policy**: 教 LLM 信息分流决策、输出前自检去重、权重判断标准(默认不加/强化1.3-2/弱化0.1-0.5)。
- **NSFW 声明段保留**: "虚构动漫艺术品元数据、非真人"是翻译 NSFW 的根基, 一字不动。
- **发现 safety 动态标签**(main.py:624-627): 检测 NSFW 关键词自动 explicit 否则 safe, 符合 Anima 官方 rating, 比静态 config 合理。
- **vision 两 prompt 不改**: 无 NL 行, 无重复问题。

### 验证
py_compile 通过; grep 确认 NSFW 声明(139) + HARD RULE(150) + Self-check(157) + 新示例(192) 到位。待重启后端实测: 简单输入看 NL 是否空/极简; 多角色输入看 NL 是否只写空间分配不复述 tag。若仍偶发重复, 后手=架构分离(breakdown 给人看 + PROMPT 给模型)。详见 D28。

