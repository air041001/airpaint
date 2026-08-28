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
- **Tailwind CDN 而非本地构建/前端框架**: 前端是薄客户端, 后端三层引擎才是主角; 零构建最贴「自己用+少量朋友」定位, 单文件内部按屏/功能分节保持可维护。CDN 需联网, 若朋友网络打不开再换本地构建。详见 D27。
- **教程重写**: 原五段是「功能说明」(LoRA 是什么), 新版改成「用法+避坑」(图生图要写完整描述非指令、denoise 0.25-0.35 微调、迭代两种模式区别), 把后端 D26 的 UX 要点传到前端。
- **移动端**: 窄屏竖排(输入置顶→图→AI 理解→参数抽屉), 参数默认折叠靠顶栏「参数」按钮呼出; 暗房天然竖排(大图→输入→横向血缘)。

### 技术坑
- **隐藏 select 必须保留**: 重写时一度删了可见的 workflow/size/lora 下拉改用卡片/宫格, 但提交逻辑(`jobsBody`/`dlgTurn`)全程靠 `$('workflow').value` 等取 id 值。删掉会全 null、出图白屏。最终在参数栏放三个 `class="hidden"` 的原生 select, 卡片/宫格点击时同步其 value, 提交逻辑零改动。
- **Tailwind CDN 默认边框色是浅灰**: `border` 类默认 `gray-200`, 暗色主题下每个卡片一圈白框; 全局 `*{border-color}` 被 CDN 注入顺序压过。最终在 `tailwind.config` 里设 `borderColor: () => ({DEFAULT:'#211e33'})` 从 preflight 根治。原生控件(textarea/range/select)还要 `color-scheme:dark` + 全局背景才不发白。
- 自查: 提取内联 JS 跑 `node --check`, 并正则校验所有 `$('id')` 引用的 72 个 id 全部在 HTML 中定义, 无遗漏。

### 验证
node --check 通过; 72 个元素 id 引用全部存在。待用户真机点一遍(生成/先看翻译/图生图上传/继续迭代/历史条)。后端未动。

## 第 25 条 2026-08-07 - 提示词格式优化: quality_prefix 官方化 + system prompt 信息分流重写

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

## 第 27 条 2026-08-08 - 修: 角色精确 tag 裸名变体去重 (防 ganyu 触发原神 logo)

### 做了什么
用户实测: char_dict 命中"甘雨"→`ganyu_(genshin_impact)` 后, 翻译结果仍含裸名 `ganyu`, 触发原神 logo(删 ganyu 即消失)。

### 根因
- **LLM 违规**: D28 规则"Do NOT repeat or rephrase known tags"只防整串相等, `ganyu` ≠ `ganyu_(genshin_impact)`, LLM 当独立泛用名输出。
- **后处理没兜**: `normalize_tag_order` 只整串去重(seen set), 裸名不在 seen, 保留。
- **logo 触发**: Anima 训练里 `ganyu`(裸名)强关联原神甘雨图(带 logo), 比 `ganyu_(genshin_impact)`(精确)更"纯原神", 是触发 logo 的充分条件。

### 怎么解 (A+B 双层)
- **A 代码兜底**: 加 `_strip_char_bare_names(new_list, char_tags)`——对每个 char_tag 提取裸名(去 `_(series)` 后缀), 删 new_list 里等于裸名的项。translate() 的 siliconflow/vision 两分支 normalize 前调用。不依赖 LLM 听话。
- **B system prompt 加硬规则**: D28 那句补"If a known tag is the precise form name_(series), do NOT also output the bare name as a separate tag"。减少 LLM 输出概率。

## 第 28 条 2026-08-09 - 修: 暗房 redo 替换意图检测 (换成X时删旧角色名防双命中)

### 做了什么
用户暗房"把图中人物换成天宫心", 结果新旧角色并存 + 1boy 乱入, 出图还是甘雨。

### 根因
- redo 实现(1162)是"delta 累加到 raw + 整体重翻译", 把"换"当追加。
- 累加后 raw 含两个角色名, char_dict 双命中, 两角色 tag 都作为 known 给 LLM, LLM 全保留。
- LLM 见两主体名误判双人, 加 1boy+1girl。
- tweak(img2img, denoise 默认0.35 只微调)不受影响——用户不会用 tweak 换主体。

### 怎么解
redo 分支(1162)累加前检测替换意图词(换成/替换/改成/换为/改为), 命中则遍历 CHAR_DICT.items() 从 raw 删旧角色名, 清逗号空格, 再 += delta 重翻译。raw 只剩新角色名, char_dict 单命中, 干净替换。tweak 未改。

### 验证
py_compile 通过; grep 确认 redo 分支替换意图检测(1162-1165)到位。待重启后端用"把图中人物换成天宫心"实测确认 ganyu 不再出现。详见 D31。

### 验证
py_compile 通过; grep 确认 `_strip_char_bare_names` 函数(622) + 两处调用(667/708) + system prompt 新规则(303) 到位。待重启后端用"甘雨"实测确认 ganyu 不再出现 + 原神 logo 消失。详见 D30。

## 第 29 条 2026-08-11 - 工作流合并: 一份 JSON 覆盖 txt2img/img2img/精修/inpaint

### 做了什么
把 3 个工作流 (快速/精修/图生图) 合并成一份 AnimaFull.json, 后端删节点拼接运行时选功能。

### 关键改动 / 为什么
- **基础=社区 AnimaStandardV7** (有 txt2img+img2img+4路 detailer), 不是 AnimaDetailerV7 (那是图片编辑导向无 txt2img)。嫁接 inpaint 链 (ImagePadForOutpaint→VAEEncode→SetLatentNoiseMask→KSampler→VAEDecode, 复用 LoadImage 0 alpha 作 mask)。
- **后端删节点拼接** (build_prompt): 按 detailer:{face,hand,nsfw,eyes} 删未选 detailer 节点重连; 不可达节点 ComfyUI 懒执行跳过, 真正省时。弃 ImpactSwitch (它所有输入都执行, 不省 GPU)。
- **调参**: 社区默认 max_size=1536 steps=16 太慢 (3.2s/步, 全精修超时失败); 按 DEVLOG 19条调成 max_size=1024 steps=12, 全精修 95s。
- **暗房 tweak 修 bug**: wf_name "anima-img2img"→"anima" (工作流合并后删了旧名, 报未知工作流)。暗房加独立精修开关。
- **弃 AnimaLLLiteApply**: 工作流用的带 mask 版节点 (kohya 包是 _sdscripts 版无 mask) 来源未找到, 降级纯 KSampler inpaint。

### 验证
后端实测: txt2img 无精修 ~42s / 全精修(调参后) ~95s / inpaint(带mask) ~90s / 暗房 tweak+精修 出图成功。build_prompt 拼接 4 种配置节点链全对。详见 D32。

## 第 30 条 2026-08-12 - 撤销 inpaint 功能 (效果不达标)

### 为什么撤销
inpaint 局部重绘实测效果不达标: (1) 该工作流 inpaint 采样器用反转 mask 约定 (黑=重绘), 前端需反转; (2) 改发色时 mask 稍大就整头重绘成新角色, 紧贴头发丝才保脸, 但用户上手难; (3) 用户自己不用 inpaint, 网站也没几个人用, 投入产出不值。

### 撤销了什么 (保留精修合并)
- 移除 AnimaFull.json 的 inpaint 节点 (200-206)
- build_prompt/page/链 inpaint 逻辑移除 (chain_source 固定主 VAEDecode 43)
- config 移除 inpaint_source/ksampler/denoise
- 前端移除 inpaint 画布 (inpaint-section, getInpaintRGBA, 涂抹交互)
- **保留**: 精修合并 (AnimaFull 一份 JSON 覆盖 txt2img/img2img/4路 detailer + 后端删节点拼接), 这是 D32 主成果。

### 学到的 (inpaint mask 结论, 记下避免再踩)
- ComfyUI 通用约定白=重绘, 但**有些工作流/采样器反转 (黑=重绘)**, 必须实测。
- inpaint 是"区域重绘"不是"改色": 改属性 (发色) 要精准 mask 紧贴目标, 否则整区域重生。
- 改发色保同脸: denoise 0.9 + 紧贴头发 mask (不碰脸) 可行, 但 UX 难 (无缩放/撤销)。

## 第 31 条 2026-08-15 - Phase 1 Prompt IR + Compiler

### 完成内容

把 Prompt Engine 从 5 字段 breakdown + 字符串拼装推进到 12 字段 Prompt IR 协议和统一语义 Compiler。文本 LLM 现在输出单行 `IR` JSON、`TAGS`、`NL`; 后端从 IR 派生原有 breakdown, `/api/translate` 增加 `prompt_ir` 字段, 前端旧逻辑无需改动。旧 5 字段协议、无 TAGS 降级和视觉 LLM 路径保留兼容。

同时修复裸角色名快速路径引用已删除 `parts` 变量导致的运行时 500, 新增零依赖 Prompt 单测。评测脚本改为直接调用真实 `translate()` 全链路, `baseline.yaml` 保持只读, candidate 与结构比较独立输出。

### 关键取舍

- IR 在 Phase 1 负责语义记录、breakdown 派生和稳定性度量; TAGS/NL 仍是最终语义 prompt 的编译候选, 字段级 TAG/NL 策略留给 Phase 2。
- `compile_prompt()` 统一角色裸名清理、去重、count→character→general 排序和 NL 拼接; quality prefix、safety、LoRA trigger、seed 和 workflow 注入仍由 `build_prompt()` 负责。
- 视觉回归发现实时 LLM 输出会让固定 seed 图像不可复现, 因此第二层验收夹具同时固定 Prompt、seed 和尺寸。曾尝试为多角色/复杂特效增加 system prompt 护栏, 但未被实验证明有效，已在 Rendering Strategy 实验前撤回。

### 验证

- `python -m py_compile server/main.py` 通过。
- 7 个零依赖 Prompt 单测通过。
- DeepSeek-V4-Flash 两轮真实链路各 30/30 成功、30/30 12 字段 IR 完整, `compare.py --require-ir` 通过。
- 固定 Prompt + seed 的 002/012/018 均生成成功。最初 vision agent 报告 3/3，但该报告不作为人眼终审。

## 第 32 条 2026-08-15 - 人眼复检暴露渲染策略问题

### 发现

用户复检 baseline 018「两个剑士对峙」后确认：图片反复被中央闪电劈成两半，几乎没有对峙感。此前的视觉代理把“左右两个主体 + 闪电存在”误判为通过，说明结构回归和粗视觉代理都不能替代人眼语义验收。

进一步检查固定夹具发现，`lightning` 同时出现在 TAGS 和 NL，另有 `dramatic lighting`，同一高显著性元素被重复强化；这还违反 D28 的 TAG/NL 不重复原则，而 compare.py 尚未检查 TAGS↔NL 重叠。

### 方向修正

- Prompt IR 保留为内部语义表示，但不再假设固定 TAG/NL 模板会带来最好出图。
- Phase 1.5 改做固定变量的 Rendering Strategy A/B 实验：TAG-only、TAG+short NL、weighted spatial NL、NL-dominant。
- 评测加入成人 NSFW 单人/双人 case；vision agent 降级为粗筛，用户人眼作为最终判定。
- 证据出现前不继续堆生产 Compiler 规则，不提前写 PLAN-v6。

## 第 33 条 2026-08-15 - 首轮 Rendering Strategy 实验完成

### 结果

在 base Anima 上固定 workflow、尺寸、seed、quality prefix 和默认负面，比较 7 个 case 的 TAG-only、TAG+short NL、weighted spatial NL、NL-dominant，以及 R2/R4 的 semantic negative。30 张图全部生成成功，用户完成盲评。

- R6 成人 NSFW 单人选择 TAG-only，手部动作最可信。
- R3 复杂单人姿态选择带明确空间/动作关系的 hybrid。
- R5 逆光和 R7 成人双人没有证明长 NL 或权重一定更好。
- R4 五个变体全部出现上下分页或中央分隔问题；没有接受的渲染胜者，需换 seed 复测。
- 用户补充观察 `girl` 比 `female` 更适合当前二次元模型，但本轮没有隔离该词汇变量。

### 结论

不存在当前可全局套用的 Prompt 格式。IR 保留，Renderer 改为未来按语义类型选择；weighted spatial NL、semantic negative 和 `girl/female` 词汇策略都暂不固化。详见 D35。

## 第 34 条 2026-08-15 - R4 专项复测与手动编辑 fallback

### 结果

R4 使用第二个固定 seed 重跑 5 个变体：V1 产生了一张单一连续画面，V2 出现多余第三人，V3/V4/V5 仍出现分页或黑线。五张图都没有稳定表达右侧角色“后撤”。

### 决定

不再为 R4 继续堆 Prompt 规则或权重策略。base Anima 的多角色构图/动作绑定在当前 workflow 下属于已知限制；产品保留现有英文 `prompt_en` 可编辑能力，让熟悉 ComfyUI 的用户手动调整关系、姿态或删减干扰元素。R4 不再阻塞 Prompt Intelligence 主线。

## 第 35 条 2026-08-15 - Phase 2 首轮 Profile / Source 实验

### 结果

- W3 的 profile A/B 中，P04 光影和 P06 成人单人支持 tag-first；P01 反而偏好保留 NL，其余多数平局或两者同样失败。
- W4 中 Dictionary 在固定 canonical 外观、光影、NSFW tag 上更强；LLM 在咖啡道具描述中胜出；其余平局。
- W6 的两条 `girl`/`female` 对照无明显差异，未形成词汇结论。
- NSFW 结构集 8/8 通过，但 N07 暴露“浴室边缘”被 LLM 误写成 `bathroom sex`，已作为 failure taxonomy 样本。

### 决定

Phase 2 只把明确成人 NSFW、单主体、简单动作的 `tag_first` 收进 profile；不把它推广到普通 SFW。Dictionary/LLM 按语义类型继续保留候选策略，不全局固定优先级。weighted NL、semantic negative、`girl/female` 替换均不进入默认生产。复杂动作和多角色失败继续通过 taxonomy 分析，用户可编辑 `prompt_en` 辅助。

NSFW 8 条结构集通过，6 条固定视觉基线由用户确认正常；N07 的“浴室边缘”被 LLM 误读为 `bathroom sex`，作为 `semantic_misread` 样本保留。Phase 2 首轮代码与实验完成，后续只在有新证据时扩展 profile。

## 第 36 条 2026-08-15 - Phase 2.5 Prompt Expansion 启动

### 发现

Phase 2 首轮主要验证 TAG/NL 摆法、失败分类和来源对照，输入普遍是一句话短描述，没有真正验证项目最初的“简单中文 → 画师级完整画面”目标，也没有覆盖系统性的服装细节、场景细节、默认镜头和材质光影补全。

### 方向

启动 E1-E7 忠实翻译 vs 画师级补全实验。SFW/NSFW 共用构图、光影、氛围、材质补全标准，NSFW 只在服装状态、身体语言和揭示节奏上分流。E6 不在视觉 Prompt 强调“成年女性”，只保留自然二次元主体词和明确 NSFW 语义。

## 第 37 条 2026-08-16 - Phase 2.6 三路 Prompt Expansion 第一轮盲评

### 实验

执行 A1 当前短中文翻译、A2 agent 代写详细中文后走当前翻译、A3 实验脚本内画师协议补全三路对照。7 个 case 各 3 个 arm，固定 seed/尺寸/workflow/默认负面，共 21 张图全部生成成功。盲评页面每组只有 A/B/C 三个位置，位置与真实 arm 由 `review_key.json` 解码。

### 人眼结果

- E1：A2/A3 并列，A1 明显 AI 画风。
- E2：A2 胜出，A3 也不错但细节不如 A2。
- E3：A2 胜出，A1/A3 平平无奇。
- E4：A3 胜出；A2 整体不行，A1 只有剪影轮廓、人物细节不足。
- E5：A3 胜出；A1 变成只有眼睛的特写，A2 有细节但 AI 画风明显。
- E6：A1/A3 并列，色气程度好；A2 没有诱惑力。
- E7：A1/A3 并列；A2 把画面缩在很小的空间内。

### 结论

第一轮不能直接把 A3 全量落地：A2 在 E2/E3 有明确优势，A3 在 E4/E5 有明确优势，E1/E6/E7 为关键平局。按预定规则先换 seed 补测，再决定 A3 生产协议、A2 辅助写作或归档。结果详见 D37 和 `phase26_results.yaml`。

关键平局 E1/E6/E7 已复用同一组固定 Prompt，仅将 seed 增加 100，生成 9 张补测图；第二轮仍按 A/B/C 盲位评审。

第二轮结果：E1 A2/A3 继续并列；E6 A1/A3 并列，A2 过于注重场景而没有人体；E7 A3 胜出，A2 构图内容过小、A1 纯场景且孤独场景未成立。汇总 A3 对 A2 为 4 胜、1 平、2 负，达到预定 `A3 ≥ A2` 门槛，进入生产改造阶段。

生产改造后新增旧 A1 Prompt vs 新画师协议 Prompt 的 A/B 验证，选择 E1/E2/E4/E6/E7 五个 case，固定原 seed/尺寸/workflow/默认负面，10 张图全部生成成功，待用户盲评确认无明显回退。

初版 A/B 暴露生产协议仍被旧 TAGS/NL tagger 牵引，实际没有复现 A3；另发现 `woman` 子串误判为 `man`、未请求 silhouette 未被复合短句过滤等护栏 bug。改为独立 `IR + PROMPT` 画师协议并修复计数、剪影、默认风格和 NSFW 景别护栏后，v5 新旧 A/B 重新生成 10 张，vision-review 粗筛显示 E2/E4/E6/E7 的关键问题改善，最终结论仍以用户人眼为准。

## 第 38 条 2026-08-16 - Phase 2.6 收尾与产品边界

### v5 人眼结果

用户修正 case 标记后，v5 实际结果为：E1 新协议胜出、E2 新协议胜出、E4 新协议胜出、E6 平局、E7 平局，即 `3 胜 / 2 平 / 0 负`。提示词增强保留为生产能力。

### 产品结论

Phase 2.6 同时证明了两件事：自动画师协议相对旧翻译有稳定增益；但 A2 的详细中文在部分 case 仍更强，说明高质量具体视觉意图不能凭空生成。后续不继续堆自动扩写规则，也不立即建设详细输入辅助功能，先观察真实使用反馈。

## 第 39 条 2026-08-18 - Phase 3 Character Knowledge 精简实现启动

### 取舍

否决结构化 `char_dict` 迁移和全量审计。当前正式词典只有 156 条，HotDict 热更新让用户自己加一行的成本极低；Phase 3 真正要解决的是未知角色名第一次出现时的 canonical tag 与可绘制性判断。

### 实现方向

生产画师协议使用 `IR.subject` 记录未知角色候选 tag，解析器兼容可选 `CHAR: 用户名 => 候选 tag` 行但不强制增加输出行；后端查询 Danbooru `tags.json` exact tag，使用角色分类和 `post_count` 判断 `likely_supported/weak/absent`。只有 likely_supported 写入独立平铺 `characters_auto.yaml`，正式 `char_dict.yaml` 优先；lookup 失败或低覆盖只缓存结果，不阻断翻译、不污染正式知识库。网络 unavailable 不缓存，等待代理恢复后重试。

补充修正：IR fallback 不再把已知角色命中后的剩余动作/场景短语当作人名；正式词典删除了未使用且会与“长门有希”冲突的 `长门: nagato_(azur_lane)` 条目。明确 NSFW 输入缺少 `nude` 时增加代码 safety marker，避免 workflow safety 误判为 safe。

长门有希与御坂美琴定向查询成功，分别得到 `nagato_yuki`（9254）和 `misaka_mikoto`（10778）并写入 auto cache；长门固定 Prompt 出图经用户确认，Phase 3 验收完成。

进一步修正：LLM 对未知角色有时输出空格形式（`yukinoshita yukino`）有时下划线形式（`yukinoshita_yukino`），检测统一归一化为下划线 canonical 后查询。Danbooru 不可达时（代理未设/网络波动）降级使用归一化 LLM 候选补入本次 Prompt，不写 auto cache；确认了 Danbooru 是验证层（防止 OC/幻觉/拼错 tag 污染缓存），而不是 tag 生成层。

## 第 40 条 2026-08-19 - 清理未验证的 baseline 回归资产

### 背景

`baseline.yaml` 是上个 agent 跑旧 `translate()` 生成的 30 条 TAGS/breakdown，未经过任何生图人眼验证，却在交接文件里被列为高优先"必过"项。Phase 2/3 反复以"30/30 IR 完整"作为质量门槛，实际上 IR 解析完整性并不等于图像质量；连本 agent 也在 Phase 3 跑了多次 30 条 batch 追结构目标，烧掉无谓的 API 调用。

### 处置

删除 `.tools/eval_set/` 下的 `baseline.yaml`、`cases.yaml`、`run_baseline.py`、`compare.py` 及本地 `candidate_*.yaml`。结构不变量（char 命中/裸名剥离/排序/safety marker/角色 lookup）改由 23 个零依赖单测确定性覆盖；图像质量只由人眼对生成图确认。NSFW safety 验证（`nsfw/validate.py` + 8 条 explicit）保留。AGENTS.md 增加"结构性测试 ≠ 质量结论"原则，防止后续 agent 重建批量结构门槛。

## 第 41 条 2026-08-22 - PLAN-LORA v2：语义选择与 exact trigger 解耦

### 复核发现

重新读取 BUILDHANDOFF、旧 PLAN-LORA、当前 LoRA config/cache、`server/main.py`、API/前端数据流和 `workflow-anatomy.md` 后，确认旧计划抓住了“先选 LoRA 再翻译”的方向，但不能原样实现：嵌套 registry 无法复用 HotDict；LLM 逐字复制 trigger 既脆弱又会被快速路径绕过；translate/jobs 没有 binding 握手；deepseek_maid 实际已在 cache，只因 type=unknown 被 `/api/loras` 隐藏。

### 新决定

用户确认采用 D39：LoRA Asset + Semantic Profile；选中的 LoRA/Profile 在翻译前进入 Reasoning/Vision Model 上下文，LLM 只选择允许的 profile ID，代码通过 Binding Compiler 编译 exact trigger。translate/jobs/dialog 共用 binding snapshot 与 registry revision，避免 Prompt、用户预览和实际 workflow 权重来自不同语义或不同版本。

### 计划边界

`PLAN-LORA.md` 已重写为 Step 0-10：先做 registry/loader/scanner/legacy adapter，再做 Binding Compiler、LLM context、API/session、前端、onboarding 和真实 A/B。registry 人工知识纳入版本控制，自动 cache 继续忽略。没有真实多人 LoRA 文件与人眼验证前，只让 schema 支持多 Profile，不宣称完成多人 composition；不引入 PromptState、Workflow Intelligence 或新的 Phase 2 批量结构门槛。

## 第 42 条 2026-08-23 - LoRA Context / Binding 首版完成

### 实现

- 新增 versioned `server/lora_registry.yaml`、`HotLoraRegistry` last-good/revision、Asset/Profile/optional schema 与 legacy key adapter；人工知识优先，自动 cache 继续 gitignore。
- 新增 Selection Resolver 与幂等 Binding Compiler。Reasoning/Vision Model 只见 `provides` 与允许的 Profile/optional ID；exact trigger、文件名和默认强度由代码决定。
- active LoRA 覆盖文本快速路径，并贯通 reference image、reroll、translate/jobs/dialog/start-image；binding snapshot 与 registry revision 防止预览、暗房和工作流实际加载串线。
- scanner 在 SHA/network 前排除 Wan，保留 unknown/incomplete inventory，优先读取本地 `.metadata.json` 与 `.civitai.info`；新增 `register_lora.py` inspect/validate/onboarding 工具。
- 前端支持 Profile 自动判断/显式锁定、per-asset 默认强度、provides/verified/待注册状态；LoRA 改变后旧 Prompt 失效并要求重翻译。

### 验证与人眼结果

- `41 prompt unit tests passed`；registry validate、Python compile、前端内联 JS 和 81 个 DOM id 引用检查通过。
- 5 组真实 LoRA fixed-condition A/B 最终为 aware `1 胜 / 4 平 / 0 负`。服装 Profile 组 aware 胜出；其余身份、角色变体、风格/光影与 DeepSeek 组均平局，没有场景/构图回退。
- Blue Archive “明亮午后”补测暴露旧词典把午后固定成 golden/lazy；新增 clear daylight/high sun/crisp shadows 后，两张光线均被用户确认正常。
- DeepSeek 旧 Illustrious 资产换成 Anima 专用 LoRA，沿用同作者身份/女仆装语义和 0.85 强度。LoRA Manager 首次失败的根因是持久 cache 未索引新文件，完整扫描后 3/3 正常生成；用户确认图书馆 aware/legacy 平局且 Anima 比 IL 更好。旧 IL 本地文件送入回收站。

### 边界

首版完成的是 LoRA Context、Profile/Trigger Binding 与角色×1 + 风格×1 的状态一致性。没有真实跨文件多人 LoRA 资产，不宣称完成自由多人 composition；不启动 PromptState、Workflow Intelligence 或推荐系统。

## 第 43 条 2026-08-23 - 本地 LoRA 入库 Agent 与三项候选资产

为避免每次新增复杂 LoRA 都占用主开发对话上下文，`register_lora.py` 增加 `--agent` 与双击启动入口。工具复用 `config.yaml` 中现有 Reasoning Model/API key，自动尝试刷新 LoRA Manager 增量索引，接收多行作者说明并生成可自然语言修订的 Registry 候选；只有 `write` + 最终确认才原子写入。

真实 Remi description dry-run 暴露并修复了三类不能只靠模型保证的问题：明确推荐 0.7 被过度保守退回 1.0、逗号 tag 被合并、转义括号被改写。最终由代码提取作者明确单值强度、拆分 tag、恢复 exact trigger，并过滤可能因普通颜色描述误选 Profile 的裸 `white/black/白/黑` alias。Civitai URL 候选分支原有不可达缩进错误同时修复。

新增 `remielle_dan` candidate（base/white/black/swim，默认 0.7）、`dolphro_kun_style` candidate（`@dropkun`）与无 trigger 的 `light_style` candidate。两个无作者视觉说明的风格只写保守 provides，不虚构特征；所有新项等待真实生图验收。

## 第 44 条 2026-08-23 - PLAN-LORA 最终一致性审计与验收关闭

用户确认 Remielle Dan、Dolphro-kun 与 Light 三项新增 LoRA 均实际生效并通过验收，Registry 中相应 Profile/Asset 从 candidate 提升为 verified。默认 strength 只负责初始化网站滑块，用户调整值仍优先，不作为工程验收门槛。

逐项复核 PLAN-LORA 与代码后确认 Step 0-10 和核心成功标准已完成；同时修正文档中三处过度表述：SiliconFlow/Vision 调用失败实际为 502 fail closed；首版通过 LoRA context 抑制冲突但没有独立 semantic conflict detector；前端展示 provides/Profile/verified，不额外展开 minimal tags。三项均不是未完成开发任务，跨文件多人 composition 继续属于明确排除的证据触发边界。

LoRA 显示名称的单一真相为 `server/lora_registry.yaml` 的 Asset/Profile `name`，前端只渲染 `/api/loras` 返回值，不按 key 硬编码中文别名。LoRA 工程至此关闭，下一项工作转向网站 MVP 细节优化。

## 第 45 条 2026-08-23 - 画布优先布局与分级分辨率

根据用户 1920×950 实际截图重排工坊：桌面改为确定的一屏工作台，画布占据释放出的垂直空间；AI 理解栏从 320px 收窄到 288px，输入改为紧凑指令条，参考图入口不再横跨整行，最近作品缩成 64×80 的接触印样带。相同高度下画布约从 418px 增至 538px，输入区约从 222px 降至 155px。移动端保持画布→AI 理解→输入的纵向顺序，参数按钮可完整展开。

尺寸从三个平铺按钮改为点击展开的画幅选择器，分为四个约 1MP 标准档（`832x1216 / 896x1152 / 1024x1024 / 1344x768`）和两个高分辨率实验档（`1024x1536 / 1536x864`），选中后自动收起；删除 `1216x832`。高分辨率显示“更慢/显存压力高”，暂不开放 `1152x1536` 与 `1536x1536`。

本机 RTX 4060 Laptop 8GB、无 detailer 实测 `1024x1536` 最终成功并生成 `anima_20260823_00014_.png`，峰值显存约 7.75GB；耗时略超原统一 300 秒，使网站先误报超时。新增按像素面积伸缩的 `generation_timeout_seconds()`，该档 deadline 为 450 秒、上限 900 秒；前端高分辨率状态改为 2～5 分钟。浏览器完成宽屏/普通桌面/390px 移动端、菜单展开/选择/收起验证。

## 第 46 条 2026-08-23 - 分辨率路由修复与第 45 条验证纠错

用户检查实际文件后发现网站多数结果仍为 832×1216，且第 45 条所谓 1024×1536 样本也不例外。Comfy history 复核确认：`build_prompt()` 虽已把节点 56 写成 1024×1536，但 txt2img 没有覆盖 ImpactSwitch；工作流节点 32 的旧默认值 2 使采样器走 `input2`，实际链路为 `salt.jpg -> Resize(832×1216) -> VAEEncode -> sampler`，完全绕过节点 56。日志中的 `SELECTED: input2` 正是直接证据。

修复后 `build_prompt()` 每次显式路由：txt2img=`select=1`，img2img=`select=2`；工作流节点 32 的安全默认值也改为 1。撤销基于错误性能结论加入的像素面积 timeout，恢复统一 `timeout_seconds`。前端尺寸徽标改为读取成品图片的 `naturalWidth/naturalHeight`，请求尺寸与实际尺寸不一致时给出提示。

RTX 4060 Laptop 8GB、无 LoRA/detailer 重新端到端测试：请求 1024×1536，输出 `1529ed18e206.png` 经 PIL 确认为 1024×1536；Comfy history 为 `select=1`、节点 56=`1024x1536`，Comfy 执行约 82.3 秒、网站调用端到端 84.16 秒。旧任务约 309.7 秒中的 47 秒进度条只覆盖采样循环，前置长等待来自误走的图片加载/缩放/VAE 编码及动态显存换入，不能归因于高分辨率。第 45 条保留为历史记录，其性能与输出尺寸结论由本条纠正。

## 第 47 条 2026-08-24 - rating 控制权回归用户与 DeepSeek 条件配方修正

复核一次 DeepSeek 女仆失败请求后确认，`safe` 并非由未成年词触发，而是 `build_prompt()` 对所有未命中固定英文 NSFW 关键词的 Prompt 默认追加；`exposing crotch` 等改写会被错误归到 `safe`。现已删除自动 `safe/explicit` 分类，Reasoning/Vision Model 也被明确禁止自行推断 rating。用户在生成前编辑英文 Prompt 加入的 rating tag 仍原样生效。明确成人内容的语义保真与构图护栏继续存在，但不再冒充 rating 分类器。

用户提供的 DeepSeek LoRA 作者说明按视角分为正面身份、正面全身、正面腰上、纯背面和侧面。旧 Registry 错把前两段合并成所有请求都注入的 30 余个默认 tag。现在默认只保留 `deepseek_whale_girl` 与 `deepseek_maid_outfit` 两个 exact trigger，五类长清单改成显式视角才启用的 optional 配方。由于现有三张实测验证的是旧正面全量绑定，新最小默认绑定标记为 candidate，等待固定条件真实图片比较；确定性测试只证明路由、白名单和手动 rating 透传正确，不宣称画质提升。

## 第 48 条 2026-08-24 - 三栏工作台与纸本/石墨双主题迁移

在独立原型经用户确认后，将视觉方案迁移到真实 `web/index.html`，没有带入原型假数据，也没有改动后端。生产工坊改为固定的描述跨栏、Prompt 左、图片中、参数右、历史下方；首次进入不显示空画布，首次翻译显示跨栏 Prompt 检查，有图后再次翻译只更新左侧 Prompt，图片保持原位。图片下载与继续迭代移到独立工具栏，成图余量使用低透明模糊背景。

新增纸本画室/石墨暗房日夜切换。纸本材质使用暖纸底与墨绿 `#334b51` 统一品牌、选中、焦点和主操作；石墨材质保留暖黑与安灯橙。主题写入 localStorage 并在工坊/暗房同步。移动端改为描述、图片、Prompt/参数页签、历史的稳定纵向顺序；暗房为图片、控制、脉络。GSAP 仅承担 opacity/translate 过渡，不动画表单和网格几何。

浏览器验收覆盖 1920×1080、390×844、首次预览、确认生成、有图后重翻译、LoRA 多 Profile、日夜材质、移动端页签与暗房。内联 JS、122 个 DOM ID、重复/缺失引用检查通过；真实本机服务的鉴权/工作流/LoRA 接口返回 200，一次完整翻译→提交→轮询任务完成并输出 832×1216 PNG。

## 第 49 条 2026-08-25 - 双主题迁移后 LoRA 与标题组件修复

用户对照日夜截图后发现三项生产缺陷：品牌/区块标题标记在石墨主题使用圆点/竖条、纸本主题使用横线，同一组件语义不一致；纸本主题的普通 `.chip` 样式以更高选择器优先级盖掉 LoRA Profile 选中态，导致白底融入背景；角色/风格菜单固定向下展开，会被右侧 sticky 滚动栏裁切。

现统一品牌与区块标题使用横线几何，主题只替换橙色/墨绿色视觉 token；为纸本 `.chip.active` 明确恢复墨绿底、浅色字和选中边框；新增按菜单高度、触发器位置、视口与参数栏交集计算的展开方向，空间不足时向上翻转，关闭/切换时清理方向状态。浏览器实测 1920×950、1365×760 和 390×844：日夜标记尺寸一致，Remielle 自动/显式 Profile 均清晰可见，桌面短视口菜单完整向上展开，窄屏有空间时正常向下展开；干净重载无运行时错误。`web/` 提交 `95fa803` 已推送。

## 第 50 条 2026-08-25 - 直接生成 Prompt 同步与竖图首屏约束

用户真实生成时发现：不先点“先看翻译”而直接点“生成”，后端翻译虽已用于任务提交，左侧英文 Prompt 与五项拆解却保持空白/旧值；同时 896×1152 竖图会把 `canvasMedia` 从设计高度反撑到原图 1152px，24 寸常见桌面视口无法完整看到图片。根因分别是直接生成路径遗漏 UI 渲染调用，以及 `flex-1` 画布在不定高父容器中被图片固有尺寸扩张。

新增统一 `renderTranslation()`，由直接生成、先看翻译、reroll 和 LoRA stale 重翻译共同调用；直接生成仍保持“静默翻译后提交”的产品语义，但结果左栏始终显示本次实际 Prompt。画布改为不参与 flex 伸展，桌面高度使用 `320px～690px` 的视口约束，图片显式 `max-width/max-height:100%` + `object-fit:contain`。浏览器 mock 完整执行 translate→jobs→poll：1920×950 下同一张 896×1152 图完整显示为约 429×552，舞台底部 933px；1920×1080、1365×720、390×844 均无裁切，“先看翻译”回归保持当前图片。静态 JS/DOM、Python compile 与运行日志检查通过；`web/` 提交 `99bb15c` 已推送。

## 第 51 条 2026-08-27 - Visual Composer 生产接入

### 事实与方向修正

用户从 Civitai/Pixiv 样图与 Anima 实际 Prompt 中指出，旧生产画师协议的前提已经不成立：高质量 Anima Prompt 可以是 tag、关系短句、完整自然语言或混合；固定约 20 个元素、预设 TAG/NL 形态和 ordinary `dict.yaml` 全命中短路，会把详细构思压成普通翻译。此前测试里自动强调年龄、裸体或通用画风也不符合本地 Anima 的真实输入习惯。用户认可正常插画和角色+画风 LoRA 定向图片后，同意把新方案接入生产；不再重启无方向的大批量 A/B。

### 实现

- SiliconFlow 普通文本改为三档 Visual Composer：`auto` 按语义覆盖度补空位，`faithful` 只补成图必需项，`free` 在保留锁定项后自由完成画面；档位由用户显式选择，与输入字数无关。
- 新协议严格为 `CONCEPT + 精确 12 字段 IR + [LORA] + PROMPT`。`CONCEPT` 固定为 `用户锁定：…｜模型补全：…`，可在生成前编辑并通过 `concept_override` 重新编译；协议失败只修复一次，仍失败则 502，不把原始响应直接当 Prompt。
- `PROMPT` 不设 tag 数、句数、词数或字符数目标，可按 Anima 需要自由混合 canonical tag、英文短句和自然语言。普通文本不再让 `dict.yaml` 删除词语或全命中绕过 Reasoning Model；`char_dict` 的角色 canonical、纯角色快路和 LoRA Registry exact binding 继续由代码确定。
- 新文本路径移除旧自动裸体、强制三分之四景别、画风删除与年龄/rating 注入。确定性护栏只保留主体计数、角色裸名去重、整段重复折叠，以及用户明确要求全身时删除互斥近景词。
- 输入边界按用途放宽为原始中文/构思各 4000、客户端英文 Prompt 6000、LoRA 编译后 8000、对话 delta 2000；Reasoning Model `max_tokens=1800`。固定 negative 增加常见坏手、缺/多/粘连手指、多余手臂/腿与坏脚词，但不宣称能解决人体随机失败。
- 前端在既有三栏工作台内增加 `自动 / 忠于描述 / 自由补全` 与可编辑构思。原文、档位、LoRA/Profile 或构思变化都会令旧翻译失效；直接生成也会先同步本次构思/Prompt，再提交带 `concept` 和 `completion_level` 的 job。dialog/start-image 同步继承这两个字段。

### 验证与边界

`49 prompt unit tests passed`，Python 编译、AnimaFull JSON、当前本机用户维护的 10 项 Registry 校验、前端 2 段内联 JS、132 个唯一 DOM ID 与 109 个静态引用检查通过。该 Registry 修改仍由用户持有，未混入本阶段提交。真实 SiliconFlow 定向 smoke 覆盖普通 auto、编辑构思重编译、角色+画风 LoRA；浏览器 mock 覆盖 1365×720 与 390×844、档位传递、构思 dirty 阻断/应用、job 字段、LoRA 双选择和原文变更 stale 阻断，console 无错误。

这些检查证明协议、状态和绑定闭环，不证明画质。当前画质依据仅是用户已经确认的正常二次元插画与角色+画风 LoRA 图片；后续从真实使用收集可复现失败，避免用 Prompt 长度、IR 完整度或重复跑批代替人眼判断。

## 第 52 条 2026-08-27 - 祀 LoRA 主触发与基础造型候选

用户新增 `si_(arknights)-v2.safetensors` 时因作者页面未写触发词而按 no-trigger 入库，后从作者样图找到了完整 Prompt。复核作者 Prompt 与 safetensors 内嵌训练元数据后确认：`si_(arknights)` 是主触发词；绿色长发、侧发、蓝眼、尖耳、角、龙尾及白裙基础造型是 53 张训练图的固定标注。Registry 因而改为单一 `base` Profile，由代码确定性注入主触发与这些默认标签；坐姿、蝴蝶、竹林以及作者组合使用的其他风格/增强 LoRA 均排除。当前仍为 candidate，等待 AirPaint 实图验收。

本次实际生成错误与 trigger 无关。ComfyUI history 显示两次都在节点 5 `Lora Loader (LoraManager)` 报 `ModelMMAP allocation failed for si_(arknights)-v2.safetensors`。进一步对照 LoRA Manager 第六点、节点源码与时间线后确认：报错时该文件尚未进入 Manager SQLite 索引，`get_lora_info_absolute()` 未命中便返回原始相对文件名，随后 Aimdo 无法打开它；文件本身可被 safetensors 和 ComfyUI 普通加载路径完整读取（840 tensors，约 91.9 MB）。用户手动访问 `/api/lm/loras/scan` 后，22:26 生成 `.metadata.json`，SQLite 与 `/api/lm/loras/list` 均返回完整绝对路径，故无需把问题归因于动态显存或要求重启 ComfyUI。

修复前的入库 Agent 确实会在 `--agent` 模式开头请求增量 scan，但它是 best-effort：非 Agent 模式不调用；请求异常只打印提示并继续；HTTP 200 后不检查响应 `status`，也不验证目标文件是否已出现在 Manager 列表。因此当时“执行过注册脚本”不等于“目标 LoRA 已完成 Manager 索引”。

验证：`registry valid: 12 assets`、`51 prompt unit tests passed`、`python -m py_compile server/main.py` 通过；运行中 `/api/loras` 已热加载 `si_arknights_v2/base`，Binding Compiler 输出主触发及 15 个基础标签且无 warning。结构验证不等于图片验收。

## 第 53 条 2026-08-27 - LoRA 入库索引由 best-effort 改为目标验收

`.tools/register_lora.py` 现在先确定待注册文件，再调用 LoRA Manager 增量 scan；只有响应 `status=success` 且 `/api/lm/loras/list` 的分页结果能按文件名或完整路径命中目标，才继续生成候选和写 Registry。HTTP 200 但 scan 被取消、列表格式异常、请求失败或目标缺失都视为失败，不再显示假成功。

增量未命中时，工具会说明生成阶段必然在 Loader 失败，并由用户决定是否执行一次 `full_rebuild=true`；默认不自动全量哈希。索引仍未就绪则在 LLM 调用前终止，Registry 不修改。普通手工新增路径也执行同一验收；已有 Asset 的编辑不重复扫描。`--no-manager-scan` 保留为显式离线准备开关，不再描述成普通 Agent 模式的默认降级。

新增确定性测试覆盖 scan+目标命中、200/cancelled 拒绝、scan success 但目标缺失拒绝，以及索引失败时在 LLM 前中止。验证为 `13 lora onboarding agent tests passed`、`51 prompt unit tests passed`、相关 Python 编译与 `registry valid: 12 assets` 通过；并以当前 `si_(arknights)-v2.safetensors` 调用真实 Manager 增量 scan，确认目标列表命中。

## 第 54 条 2026-08-28 - Composer 构图容量与角色 LoRA 外观越权修复

真实 Remielle Dan `black` 使用暴露设计时，Composer 同时给出上半身聚焦、提裙摆和抚发，导致不同画幅仍呈现意外裁切；修正为有限画面预算后，同尺寸 1024×1024 结果让手、裙摆交互和人物周围留白完整，用户确认观感没有问题。代码只拦截近景与画外下半身动作、完整下肢冲突，以及模型补出的多个手部/服装操作，不固定审美镜头。

随后复现 `black` Profile 被错误扩写为 `long black hair / red eyes`。Registry 没有这些身份事实，因此没有伪造正确发色；改为角色 LoRA 外观闭集：Profile 名/服装色不能推导发色、瞳色，只有用户原文或权威构思明确锁定的改色可进入 IR/PROMPT。首轮越权会携具体原因修复一次，仍越权则失败关闭。Binding Compiler 同步清理兄弟 Profile trigger 与身份复述。

验证：`53 prompt unit tests passed`、`python -m py_compile server/main.py`、`registry valid: 12 assets`。真实 SiliconFlow 以 Remielle Dan `black` + Fymrie 重跑原输入，IR/PROMPT 均不含 `black hair/red eyes`，保留黑色形态服装、裙摆动作、三分之四身构图且 binding 无 warning。
