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
