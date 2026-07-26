# 设计决策 (ADR)

> 记「为什么这么决定」, 不只记结果。新增取舍时追加 (见 `CLAUDE.md` 规则 3)。
> 防止以后忘了当初的权衡, 或被「为什么不这样」反复纠结。

## D1. 翻译后端: SiliconFlow Qwen3-8B, 弃 Google Translate

**背景**: 最初用 Google gtx 免费端点逐词翻译。
**问题**: ① 本机需翻墙, GFW 下频繁超时; ② 原代码失败时 `except: return 原文` 静默降级, 中文直接喂 CLIP → 出图与提示词完全无关, 还返回 `done` 误导排查。
**决定**: 换硅基流动 Qwen/Qwen3-8B (国内直连秒回), 让 LLM 直出 danbooru 风格 tag (比传统翻译更贴合画面元素)。失败即抛 502, 不再静默降级。
**代价**: 依赖外部 API key (免费额度); 多一次网络往返 (~3s)。
**详见**: DEVLOG 第 5 条。

## D2. 翻译模型: 留 Qwen3-8B, 弃 DeepSeek-V4-Flash

**背景**: Qwen3-8B 偶发复读 (`danbooru tagger, anime taglist...`), 怀疑模型太小读不懂长句, 试换 DeepSeek-V4-Flash。
**实测**: DeepSeek-V4-Flash 是推理模型, 翻译一句思考 27s+ (reasoning_tokens 450), 且 `max_tokens` 管不住思考, 频繁超 30s 超时。
**根因(关键)**: 复读不是模型笨, 是 **`enable_thinking:False` 放在了 `extra_body` 里, 硅基流动不认这个位置 → 思考没关掉**。放顶层 + user 消息加 `/no_think` 后, Qwen3-8B 处理长句也 3s 干净输出。
**决定**: 留 Qwen3-8B (速度至上, 翻译不应与生图等耗时), 修参数位置 + few-shot 系统提示。
**教训**: 换模型前先排除参数配错。小模型的「笨」常常是配置假象。
**详见**: DEVLOG 第 9 条。

## D3. 系统提示词: few-shot + 不提元词

**背景**: 旧 prompt 反复出现 "danbooru tags / tagger" 等元词, 小模型易抓错重点, 把「tag 这个概念」本身复读出来。
**决定**: 改 few-shot (2 个中文→tag 示范) 约束格式; 去掉 `frequency_penalty: 0.5` (它把模型推向冷门词循环); 元词不进 prompt。
**效果**: 输出稳定为 `1girl, ...` 结构化 tag。

## D4. 内网穿透: cloudflared 命名隧道 (airpaint.xyz), 弃 trycloudflare 临时隧道

**背景**: 免费临时隧道每次重启换 URL, 要同步改前端 `API` 常量 + 后端 CORS + push 前端, 维护负担重。
**决定**: 注册 `airpaint.xyz` 接入 Cloudflare, 建命名隧道 `airpaint`, DNS 路由 `airpaint.xyz` 和 `api.airpaint.xyz` 永久 CNAME 到隧道。重启不变地址。
**代价**: 需域名 + 重新 `cloudflared tunnel login` 授权 (会覆盖旧 cert, 旧域名隧道因此退役, 已确认不用)。
**收益**: 彻底退役 `bind.sh` 和 GitHub Pages push 流程。
**详见**: DEVLOG 第 9 条。

## D5. 前端收进同域名 (FastAPI 静态托管), 弃 GitHub Pages

**背景**: 前端原放 `air041001.github.io/air`, 改前端要 push, GFW 下还不稳。
**决定**: 后端 `GET /` 直接 FileResponse 返回 `web/index.html`, 前后端同域。
**代价**: 后端要管静态文件 (一行 FileResponse, 几乎无成本)。
**收益**: 一个域名搮定全部, 改前端重启后端即生效, 不再 push。`web/` 仓库降级为备份。
**注意**: 网页在 `airpaint.xyz`、API 在 `api.airpaint.xyz`, 跨子域仍需 CORS 显式放行 (已配)。

## D6. 不配 negative_node, 用工作流自带负面模板

**背景**: `AnimaStandardV7.json` 自带一个高质量负面词节点。
**决定**: config 不设 `negative_node`, 不覆盖工作流自带负面词。
**原因**: 实测覆盖反而更差; 自带模板是该工作流调优过的。`negative_node` 字段保留为可选, 将来其他工作流需要时再配。

## D7. 统一所有 seed 为同一正整数

**背景**: ComfyUI 前端用 `seed=-1` 表示随机, Impact Pack 的 onprompt 钩子负责替换。走 `/prompt` API 时这些钩子拿到的是我们拼的 JSON, 残留 `-1` → `np.random.default_rng(-1)` 抛 ValueError → Impact Pack 整个异常退出 → FaceDetailer 人脸修复没跑 → 出图「很原生」。
**决定**: `build_prompt` 扫描所有 int 型 `seed`/`noise_seed`, 全写成同一正整数 (跳过列表型节点连接, 不破坏图结构)。
**详见**: DEVLOG 第 8 条。

## D8. sanitize_for_api: 删前端专属节点

**背景**: `WidgetToString` (KJNodes) / `Image Saver Metadata` 依赖 `extra_pnginfo["workflow"]`, 只有前端排队才带, API 提交直接崩 (`NoneType` 报错)。
**决定**: 提交前删这两个节点; `Image Saver Simple` (依赖上面的 metadata) 换成内置 `SaveImage`。核心生成链路一个不动。
**详见**: DEVLOG 第 3 条。

## D9. 状态全内存, 不上 SQLite (MVP 权衡)

**背景**: `JOBS` / `USAGE` 都是进程内字典。
**决定**: MVP 阶段接受「重启清零」, 不引入 DB。
**原因**: 单机 + 单并发 + 朋友小规模用, 内存态足够; 引 DB 增加部署复杂度, 不符合 MVP 轻量原则。
**何时 revisit**: 用量上去 / 需要历史回溯 / 邀请码管理 → Phase 3 上 SQLite (见 ROADMAP)。

## D10. 单并发队列

**背景**: 单 GPU, 并发生图只会互相抢显存, 慢且易 OOM。
**决定**: `asyncio.Queue` + 单 worker, 串行执行。前端显示排队位置。
**代价**: 高峰期要排队, 一张 15~60s。

## D11. 词典优先翻译

**背景**: 常用词 (发型/发色/眼睛/表情) 词典命中零 API 调用, 最快最稳。
**决定**: `translate()` 先按逗号切分逐段查 `dict.yaml`, 全命中直接返回; 未命中部分才送 LLM。
**收益**: 省 API 配额、降延迟、降外部依赖。词典是高质量人工映射, 比模型更可控。

## D12. 角色 tag 走词典, 不靠 LLM

**背景**: 意图扩写 prompt 初版含规则「命名角色用精确 danbooru tag」。实测 Qwen3-8B 认不准: 雷电将军->`lightning general`(字面翻译+发色都错)、珊瑚宫心海->`coral_palace_himeko`(似是而非)、甘雨->根本没认出是角色(读成"甜雨"做下雨场景)。仅示例中出现的三月七对, 是在抄示例。
**决定**: 新建 `char_dict.yaml` (中文名->danbooru tag), `detect_characters()` 子串扫描输入; 命中后把 tag 作为 `[Character: ...]` 上下文喂给 LLM, LLM 只管场景/氛围扩写, 不再自己产角色 tag。
**收益**: 角色 tag 可靠 (词典 vs 模型幻觉)。契合 D11 词典优先哲学。用户可随时扩词典。
**代价**: 词典覆盖有限, 未收录角色仍可能出错 (此时 LLM 退回按特征描述)。

## D13. 意图扩写: 氛围->场景, 裸角色名快速路径

**背景**: 朋友常常"不知道画什么, 只能说个感觉"(如"想要春天的感觉")。平铺翻译无法处理这种模糊意图。
**决定**: 系统提示词升级为 prompt-engineer 角色, 加规则: 氛围/心情 -> 扩写为 scene+lighting+style; 纯氛围输入(无人)出不强制人物的风景; 角色命中的 tag 原样保留。
**裸角色名快速路径**: 输入只是个角色名(无其他描述)时直接返回 `tag, 1girl, solo`, 跳过 LLM。原因: LLM 对裸角色名会疯狂编场景/武器 (实测 7.9s + sword/combat/archer 噪声 tag)。
**主体策略**: 看输入决定 -- 提了人物/角色才加计数 tag (1girl/1boy); 纯氛围/场景不加, 出风景。
**实测**: 氛围扩写 1-3s, 裸角色名 0s, 混合(角色特征+氛围)能保特征并扩场景。总耗时 3s+36s生图约 40s, 在 1 分钟预算内。

## D14. .bat 启动脚本: GBK + CRLF, 不用 UTF-8/chcp

**背景**: 双击 `start_airpaint.bat` 后网站报 1033。排查: 后端 8000 健康, 但 cloudflared 进程不存在; 手动跑同一条 `cloudflared tunnel run airpaint` 却秒连 4 连接。命令本身没问题, 是 bat 没把 cloudflared 跑起来。三个叠加根因: ① bat 是 Unix LF 行尾, cmd.exe 解析 .bat 需 CRLF, LF 时 `REM`/`echo` 行被切碎 (`REM 检查后端是否在跑` 被当命令执行); ② `if errorlevel 1 (...)` 块内 echo 含括号 `(127.0.0.1:8188)`, 扰乱 cmd 块匹配, 报 `. was unexpected at this time`, bat 中断, cloudflared 行没执行; ③ bat 存 UTF-8, cmd 按 GBK(codepage 936)解析, `chcp 65001` 只改显示不改解析编码, 中文行偶发乱码报错。`start_airpaint.bat` 之前"看着能跑", 是因 `start cmd /k` 启后端那行纯 ASCII 且位置靠前, 侥幸执行 -> 后端活、隧道死 -> 1033。
**决定**: .bat 存 **GBK** 编码 (中文 Windows 原生 codepage, cmd 解析无歧义) + **CRLF** 行尾 + `if errorlevel 1 (...)` 块改单行 `if errorlevel 1 echo ...` + echo 行不带括号 + 去 `chcp 65001` (GBK 文件不需要, 且 chcp 对 cmd 解析无效)。新增 `start_tunnel.bat`: 后端已在跑、隧道挂了时单独补隧道, 不碰后端 (避免 8000 端口冲突)。
**收益**: 双击即正常起 cloudflared, 无乱码报错; 隧道单独挂了能一键补起, 不必重跑整个 `start_airpaint.bat`。
**代价**: cloudflared 日志里的 UTF-8 中文 (如"以太网"、路径里的用户名) 在 GBK 控制台显示为乱码, 纯装饰性, 不影响功能 (关键日志 `Registered tunnel connection` 是 ASCII)。bat 文件不能用普通 UTF-8 编辑器直接改, 改完需重新存 GBK, 略增维护成本。

## 待决策 / 方向

- **Intent Engine**: 当前是平铺的「中文→tag」, 无意图解析。迈向「理解用户意图」核心目标的下一步是
  解析用户输入的结构 (角色 / 动作 / 场景 / 风格 / 构图、否定语义、歧义消解), 而非仅追加更多工作流。
  符合 CLAUDE.md 规则 6。**部分已实现** (D13: 氛围扩写 + D12: 角色词典); 完整意图解析 (否定/歧义/构图) 仍待做。
