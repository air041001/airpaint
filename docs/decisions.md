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

> **状态: 已反转 (2026-08-13)** — config `siliconflow_model` 现为 `deepseek-ai/DeepSeek-V4-Flash`, 翻译模型已换回 DS, 本文「弃 DS」的结论不再生效。

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

## D15. Prompt Engine 三层: 角色优先 + Known-tags 上下文, LLM 只出新增 tag

**背景**: 用户提供 v2 设计(三层架构 + 意图三分类)。对照代码 70% 已实现(D12/D13); 真正新增价值是「角色优先删名再查词典」和「词典命中作 Known attribute tags 喂 LLM, LLM 只翻 misses」。但 v2 代码有几处开倒车: enable_thinking 放 extra_body、加 frequency_penalty 0.5、prompt 无 few-shot、全命中路径丢 1girl/solo、按字数分意图。
**决定**: 采纳结构改进(角色优先 / Known-tags 上下文 / LLM 只出新增 tag 后端 prepend); 但: ① prompt 不拆三份, 保留单 prompt + few-shot + 内容规则; ② 意图不按字数分, 用内容规则(氛围 vs 具体); ③ 沿用 D2 修复(顶层 enable_thinking + /no_think + 无 freq penalty); ④ 保留裸角色名快速路径(返 1girl, solo)。
**收益**: LLM 只翻未命中, 省 token + 已知 tag 不被改坏 + 防重复。char-first 顺序更清晰。
**代价**: translate/siliconflow_translate 重构, siliconflow_translate 不再自检角色(上移到 translate); 调用方只有 translate, 安全。

## D16. LoRA 注入: 走 loras widget `__value__`, 触发词手动拼

**背景**: 要把"前端选 LoRA"接进 AnimaStandardV7 工作流 (节点5 = Lora Loader (LoraManager))。lora_feature_plan.md 猜注入格式为 `workflow["5"]["inputs"]["loras"] = {"__value__": [[file, sm, sc]]}` (数组套数组) + `<lora:file:1>` 文本语法。但这是猜的, 猜错会静默不加载。
**决定**: 查本机 LoraManager 源码 (`E:\ComfyUI_windows_portable\ComfyUI\custom_nodes\comfyui-lora-manager\py\nodes\lora_loader.py`) 后定:
- ① **不走 text 字段**: `load_loras` 第151行 `del text` -- text 是 REQUIRED 输入但执行时直接删, 只服务前端 autocomplete; `<lora:...>` 文本语法只在 `LoraTextLoaderLM` (另一个节点) 的 `parse_lora_syntax` 里生效, 节点5 不解析。
- ② **走 loras widget `__value__`**: `get_loras_list` (utils.py:72) 兼容 `{"__value__":[...]}` 和 `[...]` 两种。元素是**对象** `{name, strength, clipStrength, active}`, 不是数组。`active` 必须为 true (`_collect_widget_entries` 第54行 `if not lora.get("active", False): continue`)。计划猜的 `[[file,sm,sc]]` 其实是 `lora_stack` 输入的格式 (节点5 没此输入)。
- ③ **触发词手动 prepend**: 节点5 output2 (trigger_words) -> 节点37 TriggerWordToggle -> 46 StringConcatenate -> 48 -> 54 本是自动注入触发词的链路, 但现有 `build_prompt` 的 `set_input("prompt_node","text",full_prompt)` 已把节点54 的 text 覆盖成字面值 (见 config 节点54 注释 "注入会覆盖角色模板"), 这条链早断了。故 LoRA 触发词必须自己拼进 full_prompt (放 quality_prefix 之后, 用户词之前), 不能指望 LoraManager 自动注入。
**收益**: 注入格式有源码兜底, 不会静默失效; 触发词确定性由 config 控制, 不依赖 LoraManager 元数据 DB。
**代价**: 触发词在 config.yaml 手维护 (与 LoraManager DB 可能重复, 但冗余无害); config.yaml 仍是 gitignore, loras 配置不进版本库 (与 tokens 同 tradeoff)。

## D17. 提示词两步走: 翻译独立端点 + 可选编辑, 默认仍一键

**背景**: 朋友抱怨"出的什么鬼图"时, 多半是 LLM 翻译/扩写跑偏, 但用户看不到中间结果只能猜。一键出图把翻译藏在 `/api/jobs` 里, 既没暴露自动扩写的差异化能力, 也没给翻坏兜底。
**决定**: 翻译与生成解耦。新增 `POST /api/translate` (只翻译不排队, 用 `verify_token` 不查 image 日限 -- translate 只花 LLM token 不占 GPU); `POST /api/jobs` 破坏性改收 `prompt_en` (不再翻译)。前端两按钮: 「直接生成」(翻译+提交一气呵成, 默认一键 UX 不变) + 「预览提示词」(翻译后可编辑 textarea 再确认)。translate 复用 LRU 缓存, 预览过再直接生成不二次花 token。
**收益**: 暴露扩写能力 (用户看见"春天"->啥) + 翻坏可手改 + 默认 UX 不变 (不强制两步, 朋友懒得写英文 tag 的门槛不抬高)。
**代价**: `/api/jobs` 破坏性改动 (前端 no-cache + e2e 同步改, 无包袱); translate 不限流, 理论可被刷 LLM token (friends-only + 邀请码, 可接受, 滥用再加独立限流)。

## D18. Anima 提示词规范 + LLM 结构化意图分解; 负面=常量, 否定语义解析弃用

**背景**: 实测 Qwen3-8B 平铺翻译丢三样东西: ①场景误读(书桌房间->教室) ②隐喻字面化(未来的方向->`future`) ③构图丢失(看向窗外->出图没看窗)。根因有二: (a) 提示词把模型拷成「150字符扁平 tag 袋 + 关思考」, 扁平 tag 天生表达不了空间关系/隐喻; (b) 8B 理解力弱。同时联网查证 Anima 模型(CircleStone Labs + Comfy Org, 2B, Cosmos 架构)提示词规范: 正向前缀 `masterpiece, best quality, score_7, safe`, 小写+空格(score tag 例外); 负面极简且固定(WAI-Anima 式: `worst quality, low quality, lowres, score_1/2/3, blurry, jpeg artifacts, bad anatomy, watermark, artist name`), 不像 Illustrious 要一长串 bad-hands。来源: civarchive.com/models/2458426/anima-official, lilting.ch/en/articles/anima-negative-prompt-shorten-illustrious。
**决定**:
1. LLM 层从「直接吐扁平 tag」改为「先结构化分解(scene/composition/mood/lighting/style) 再吐 TAGS 行」。隐喻落 mood(禁字面名词), 空间关系落 composition(带 facing/from behind/looking out 锚点), scene 强制表态具体地点。解析无 TAGS 行则整体当 tag 降级(不崩)。`translate()`/`siliconflow_translate()` 返回 `(prompt_en, breakdown)`, `/api/translate` 回传 breakdown 供前端预览展示「🤖 AI 理解」。
2. 参数: max_tokens 180->400, temp 0.2->0.4; `enable_thinking` 保留关闭但加 config 开关 `translate_enable_thinking`(默认 False), 隐喻仍弱就翻 True 重测(不动代码)。结构化字段本身是强制表态机制, 不依赖 CoT, 故默认关思考保住 D2 的延迟/复读安全。
3. Anima 规范化: `quality_prefix` 从 Illustrious 风格(`very aesthetic, ultra detailed`)改 Anima 官方(`masterpiece, best quality, score_7, safe, very aesthetic, absurdres`); LLM 被禁输出 quality/score tag(前缀已处理)与 realistic/3d(Anima 不擅写实); 工作流固化负面补构图否定词(`multiple views, split view, grid view, cropped, out of frame`, 对症 Anima 短提示词构图散开)。
4. **否定语义解析弃用**: Anima 负面是常量(不随用户输入变), config 特意不配 negative_node(工作流自带, 覆盖更差)。故原 Intent Engine 规划里的「否定语义解析」无必要, 砍掉。
**收益**: 三病同治(实测 3 句未见过的输入: 天台/公交站/神社, 场景/构图/隐喻全对, `realistic` 被禁); breakdown 让用户看见 AI 理解, 翻坏可手改(承 D17 两步走); Anima 提示词写法有据可依。
**代价**: 翻译慢一点(结构化+400 token, thinking 关仍 ~3-5s, 两步走已有延迟预算); 8B 偶发格式不稳靠 TAGS 行降级兜底; few-shot 含用户原测试句(泛化已另测 3 句未见过的验证, 非只抄例子)。

## D19. 抽卡 re-roll: 高温 + 发散指令 + 跳过缓存

**背景**: 同一句中文, LLM 结构化分解(D18)本身有随机性, 但 `translate()` 的 LRU 缓存(按 context 键)让相同输入永远返回首版, 朋友想"换个风格看看"只能改字重打。inspiration 调研 ① 把这列为轻量高价值(ComfyUI-StructPrompt 的 seed 控制扩写方向)。
**决定**: `siliconflow_translate(context, reroll=True)` 时 temperature 0.4 -> `reroll_temperature`(config, 默认 0.9), user content 前置发散指令("给一版与常规不同的创意解读, 变换 scene/mood/lighting")。`translate(text, reroll=True)` 透传, 且 reroll 时**跳过缓存读和写**。`/api/translate` 请求体加可选 `reroll: bool`。前端预览区加「🎲 再来一版」, 仅 breakdown 非空(LLM 路径)时显示。
**为什么跳过缓存写**: reroll 是探索性动作, 若写回同一 context 键, 之后正常翻译会返回 reroll 版(顶掉原版)污染缓存。跳过读写 = 每次重抽都新鲜, 正常预览仍命中首版缓存, 互不干扰。
**收益**: 朋友点一下就探索不同解读/风格, 不用重打字; 复用现有端点(只加一个 bool), 零新端点; 默认 UX 不变(reroll 是预览模式下的可选按钮)。
**代价**: reroll 每次实打实花一次 LLM token(不缓存); 快速路径(全命中词典, breakdown=null)无 LLM 可变, 前端直接隐藏按钮(不报错); 高温 0.9 偶发质量波动, 由现有重复-tag 兜底 + 可调 config 兜住。

## D20. Anima tag 顺序规范化: count -> character -> general

**背景**: inspiration 调研 ⑥(anima-prompt-helper)指出 Anima 期望固定序 `quality -> year -> rating -> count -> character -> series -> artist -> general -> 自然语言`。先前 `translate()` 拼接是 `char_tags + hits + new_tags`, 而 LLM 的 new_tags 按 D18 系统提示规则把 count(1girl/solo)放在 TAGS 首位 -- 即 count 被埋在 prompt_en 末尾、character 之后, 与规范相反。
**决定**: 新增 `normalize_tag_order(char_tags, other_tags)`: 用 `_COUNT_TAG_RE`(匹配 1girl/1boy/2girls/solo/6+girls/multiple girls 等)把 other_tags 拆 count vs general, 输出 `count + char_tags + general`。`translate()` 四条返回路径(快速全命中 / siliconflow / google / none)统一走它(原字符串拼接改 list 再 normalize)。quality 仍由 `build_prompt` 的 `quality_prefix` 在更外层 prepend, 不在此处理。
**为什么只排不做 NL**: Anima 的 Qwen3-0.6B 编码器原生支持末尾追加自然语言, 但 NL 会显著改变出图、需单独验证, 本次 ⑥(inspiration 定性为"白捡收尾")只做零风险的重排。NL 追加留待 ③/⑤ 大跨步时一并试。
**收益**: 对齐 Anima 官方期望序(count 在前利于编码器 recency bias), 零增删 tag 零风险; 预览即展示规范序, 所见即所出。
**代价**: year/rating/series/artist 桶未单独处理(我们不产 year/artist tag; rating 在 quality_prefix 的 score_7; character 与 series 在 char_dict 里常融合如 `march_7th_(honkai:_star_rail)`, 不强拆); 重排对出图的实际影响待端到端观察。

## D21. 词典热更新: char_dict.yaml / dict.yaml 按 mtime 重载, 不重启

**背景**: 角色词典(char_dict)和属性词典(dict)原先在模块级 `yaml.safe_load` 一次性载入全局变量, 加角色/加词条后必须重启后端才生效。朋友远程用时重启后端=中断服务(队列/在跑的图)。希望"存盘即生效"。
**决定**: 封装 `HotDict` 类(path + key_fn + mtime)。`.get()` / `.items()` 调用时先 `stat()` 取 mtime, 与上次不同才 `yaml.safe_load` 重载。`DICT`(key 小写)、`CHAR_DICT`(key 不小写, 中文无大小写)各实例化一个, 调用处(.get / .items)零改动。config.yaml 不做热更新(token/限额之类重启更稳妥)。
**为什么 mtime 而非 watchdog**: mtime 轮询每次访问就一个 stat(微秒级), 无第三方依赖, 与"翻译时才用词典"的访问模式天然契合; watchdog 要起后台线程+依赖, 对单文件热更新过重。
**为什么整体重建再赋值**: `self._d = {...}` 先建好新 dict 再绑名, 读端要么见旧要么见新, 不会读到半成品(async 单线程下更无并发问题)。
**兜底**: 解析失败(存盘写到一半 / YAML 笔误)`except` 保留旧 _d 不更新、打印 `[HotDict] 重载 ... 失败` 警告, 不阻断翻译; 下次访问再试。
**收益**: 存盘 char_dict/dict 后下一句翻译即生效, 不重启不中断服务。
**代价**: 每次 .get/.items 多一次 stat(可忽略); 坏 YAML 不报错只警告, 需看后端控制台才知(可接受, 比崩翻译强)。

## D22. detailer 精修工作流 + Image Comparer 剥离 + 调参 (186s->90s)

**背景**: 工作流里 FaceDetailer/HandDetailer 已建好但 MUTE, 启用可白捡脸/手细节质量。但实测启用后 186s(朋友不会等 3 分钟)。需既拿质量又不拖垮默认体验。
**决定**:
1. **拆两工作流**: `anima`(快速默认, 原版 ~40-50s) + `anima-detailer`(精修, ~90s), 前端下拉选。复用 `/api/workflows` 既有机制, 不加新端点; 注入节点两版相同(54/6/56/5), config 只多一条 file+label。
2. **Image Comparer (rgthree) 加入 sanitize_for_api 剔除集**: 它继承 PreviewImage(OUTPUT_NODE), 中间预览图进 `/history`; `submit_and_wait` 取"第一个有图的节点"会误取 comparer 中间图(手修版/原图)而非最终 SaveImage。本身不崩(extra_pnginfo=None 时只 save_images 存盘), 但干扰取图, 必剥。
3. **detailer 调参**: `max_size` 1536->1024、`steps` 16->12。渲染像素是时长主因(裁剪区被放大到 1536px, 比主图还大, 每步 2.87s); 12步是下限(再降不起作用, denoise 已低 0.26-0.4)。186s->90s。
**为什么拆工作流而非动态拨 MUTE 组**: 后端走 /prompt 用 API 导出 JSON(只含活跃节点), 拨 rgthree 组开关是 ComfyUI UI 动作, 后端无法运行时切; 切功能=换 JSON 文件。两工作流=两份 JSON + 下拉选最直接, 且落地 ROADMAP Phase 2.1 多工作流架子, 以后 ControlNet/inpaint/图生图 同套路加一条即可。
**收益**: 朋友默认快速出图, 要质量选精修; 脸/手细节白捡; 多工作流架子搭好。
**代价**: 两份 JSON 要分别维护(改注入节点/quality_prefix 要同步); 精修 90s 仍偏长(主图 KSampler 30步=37s 是大头, 不动怕掉整体质量); config 改动要重启(不热更新, 故意保 token/限额稳定); Eyes/hires detailer 因模型缺失未开(见 workflow-anatomy)。

## D23. ③ 参考图理解: 视觉 LLM 提氛围 (Qwen3-VL-8B-Instruct), 走 txt2img 非图生图

**背景**: inspiration ③--朋友上传参考图, "画一张跟这张同氛围的"。朋友常说不出想要啥但能给参考图, 直击"理解用户意图"。
**VL 模型选型**: 硅基流动 Qwen3.5-4B(免费)做不了视觉(两次超时 40s/70s, 疑似纯文本); Qwen3-VL-8B-Instruct 能用(1.1s, 64x64 红图正确识别"Red")但收费 $0.18/M 输入 + $0.68/M 输出 ≈ $0.0007/次, 朋友用量下月几毛封顶, 可忽略。曾考虑别的平台免费 VL(50万 token), 但仅够 ~230 次(3周用完)+要切换平台/格式, 不值。`siliconflow_vision_model` config 可调。
**决定**:
1. **图是氛围参考, 非图生图**: `siliconflow_vision_translate` 用 Qwen3-VL 从图提 mood/color/lighting/composition/scene -> 结构化 breakdown+TAGS(复用 _parse_structured_output)。图**不进 ComfyUI**, 走现有 txt2img。与 ⑤ img2img(LoadImage->VAEEncode->二采, Workflow Engine 层)正交, 不冲突; ③ 在 Prompt Engine 层。
2. **merge**: `translate(text, reroll, image_b64)` 有图时, char_dict+dict 仍从文本预匹配, 视觉 LLM 拿图+上下文(Known tags+Remaining)一次处理图+文, `normalize_tag_order` 拼接。视觉 LLM 替代文本 LLM(有图就不调文本 LLM)。图不缓存(探索性, key 含图复杂)。
3. **Qwen3-VL 不接受 enable_thinking**(400), 视觉调用不带; 文本 LLM(Qwen3-8B)仍带。
4. **normalize 加保序去重 + dict 多 tag 拆分**: 重复根因二: ①VL 偶发复读 -> normalize_tag_order 加 `seen` 去重(保留首次); ②**dict 多 tag 值("少女"->"girl,young,cute,innocent")原 `hits.append` 当 blob 不拆, 跟 VL/LLM 同名 tag 撞伪重复**(normalize 按整元素去重逮不到) -> dict 命中按逗号拆开(`hits.extend`)。②是老 bug(文本路径也有), ③ 撞上才暴露。
5. 前端参考图上传 canvas 缩到 768px/JPEG 0.85 base64(省 token); prompt+image 至少一项; /api/translate 加 image 字段(≤5MB); reroll 对视觉也生效。
**收益**: 朋友给张图就能"画同氛围的", 不用文字描述; 不改工作流、不冲突 img2img; 复用现有结构化输出/normalize/前端。
**代价**: 视觉 LLM 收费(可忽略); 视觉调用不缓存(每次花 token, 探索性可接受); VL 偶发复读靠 normalize 去重兜底。

## D24. 前端改版: 登录门禁 + 公告/教程下拉面板 + 移动端

**背景**: 原前端简陋--邀请码是主卡片里一个明文输入框 + 保存按钮, 无门禁; 无公告/教程, 朋友不知道新功能和用法; 移动端没专门考虑。
**决定**:
1. **登录门禁**: 全屏 #login 遮罩, 邀请码输入 -> 「进入」调 `/api/auth/check` 验证 -> 进主面板。token 存 localStorage, 页面加载自动验证。主面板移除 token 输入框(改隐藏 input), header 加「退出」。
2. **新增 /api/auth/check**(verify_token, 不查日限不耗配额): 不复用 /api/workflows(auth 查日限)验登录--否则达日限的朋友登不进(429)。
3. **下拉面板(非侧拉抽屉)**: 标题下方居中按钮行, 点开向下展开全宽面板(居中, max-680)。选下拉而非侧拉: 侧拉桌面只占右侧一小条要凑过去看, 下拉全宽居中、桌面/移动同款不用适配。两 tab: 更新公告(带日期, latest first)/ 教程(简述翻译/LoRA/参考图/工作流/re-roll, 不手把手)。
4. **标题居中**: 按钮挪到标题下方居中(toolbar), 不抢标题居中位置; 下拉面板全宽居中, 桌面/移动都不挤。
5. **loadWorkflows 改抛异常**(原来 try/catch 写 #status): 登录流程需它抛才能 catch 401; 调用方(submitJob 等)自己 catch。
**收益**: 门禁隔离(邀请码才能进); 朋友自助看公告/教程降门槛; 移动端不挤压。
**代价**: 邀请码登录是"暂时"方案(无独立账号系统); 下拉面板按需展开不常驻(可接受); loadWorkflows 失败现在走调用方 catch(登录流程会把网络错当 auth 失败显登录, 罕见)。

## D25. ⑤ 对话迭代 MVP: 显式路由不猜意图 + D 用 iterate 视觉变体

**背景**: ⑤ 多轮对话精修是北极星("再亮一点"式迭代)。用户调研发现同一对话里不同意图对应不同能力: 换一版=txt2img 重抽, 微调=img2img, 保姿势=ControlNet, 保氛围=③ 参考图。若靠 LLM 猜意图(Qwen3-8B 弱)既慢又不准。
**决定** (MVP 骨架 + A/D):
1. **显式路由**: 每张生成图挂操作按钮 [换一版 / 保氛围](B img2img 下阶段), 用户点按钮决定路由, 不猜意图。delta 文本只作提示词增量, 不作意图判断。
2. **A 换一版**: delta 有则 `session.raw += delta` 重翻译; 无则复用 `current_en`(免 LLM)换 seed。
3. **D 保氛围**: 上一张图读文件 base64 -> `siliconflow_vision_translate(mode="iterate")` 全量提取(锁主体+氛围)再变体。**与 ③ 不同**: ③ 用户参考图是 vibe-only 禁抄主体; D 要锁住实际出图的主体+氛围, 故独立 VISION_ITERATE_SYSTEM_PROMPT。
4. **会话存储**: SESSIONS 内存 dict(sid -> raw/current_en/turns), 与 JOBS/USAGE 同套, 重启清零(迭代线程本就临时)。每轮入队复用抽出的 `_enqueue`(create_job 与 dialog 共用, worker 不动)。
5. **每轮计日限**(USAGE+1): 对话迭代也是出图, MVP 用同一 30/天限额(刷多了到顶, 后续再议单独计数)。
**收益**: 骨架可玩(换一版/保氛围立即可用); 不猜意图模型短板无关; _enqueue 复用零 worker 改动; B(img2img)下阶段独立加, 不动骨架。
**代价**: 会话内存存储重启清零; delta 累积(raw 无限增长, MVP 可接受, 长对话可截断); vibe 每次读全尺寸图送 VL(token 稍多, 可后续前端缩图); ControlNet(保姿势)未做。

## D26. img2img (B): 拨 Load Image 组 + 后端注入 + 单张/对话双入口

**背景**: ⑤ 对话迭代的 B 路由(微调)。子 agent 查源码确认: ImpactSwitch 42 select(1=txt2img/2=img2img)可 set_input 覆盖; LoadImage 0 要文件名(非 base64); KSampler 6 denoise 连接(1.0)可覆盖为低值。无硬阻塞。
**决定**:
1. **第三份工作流** `AnimaStandardV7-Img2Img.json`(拨"Load Image"组导出, 38 节点)。config 加 `anima-img2img` 条目, 配 `image_node`(0)/`switch_node`(42)/`denoise_node`(6)。
2. **build_prompt 扩展**: 传 `image_filename`+`denoise` 时, set select=2(替换连接)+ image=filename + denoise=低值(替换连接)。不传则 select/image/denoise 保持原样(txt2img)。
3. **upload_image_to_comfy**: POST /upload/image 上传图到 ComfyUI input 目录 -> 返回文件名 -> set_input。LoadImage 要文件名字符串, 不是 base64。
4. **_enqueue 共用**: create_job(单张)和 dialog_turn(对话)都调 _enqueue, 传 image_filename+denoise。worker/submit_and_wait 全链路传递。JOBS 加 image_filename/denoise 字段。
5. **单张模式**: workflow 选 anima-img2img -> 上传标签变"图生图" + denoise 滑杆出现 + 图走 /api/jobs(不走 /api/translate)。
6. **对话微调(tweak)**: 上一张图 -> upload -> translate(delta, 纯文本) -> anima-img2img 入队 + denoise。
7. **UX 要点**: img2img prompt = **完整画面描述**(非指令"改XX"); denoise 越高越偏离原图(0.25-0.35=微调, 0.5+=大改)。默认 0.35。
8. **保氛围(vibe)删了**: 跟换一版高度重叠(都是文字驱动改描述), 且 reference(保氛围换主体) vs iterate(锁主体)内在矛盾。后端 vibe action 休眠保留, 前端按钮已删。
**收益**: img2img 落地, 单张+对话双入口; _enqueue 复用零 worker 改动; 工作流 config 驱动注入(与 lora/prompt 同套)。
**代价**: img2img prompt 要完整描述(用户需学习, 非"改XX"指令); denoise 调参有学习曲线; 单张 img2img 没有累积描述(对话微调更好用); LoadImage 占位文件(example.png)需存在于 ComfyUI input(txt2img 验证用)。

## D27. 前端大改: 工坊/暗房三屏 + 单文件 Tailwind CDN (非框架/非构建)

**背景**: 原前端 783 行单文件, 深色大留白里挤一个居中窄卡片, 后端三工作流/词典/参考图/对话迭代/结构化分解全藏在下拉和按钮后, 朋友感知不到工作量; 「单张生成」「对话迭代」是两个平级 tab, 但二者产品形态本质不同(单张=无状态设置先行; 对话=以上一张图为基准、有血缘)。曾纠结要不要上 React/Vue + Vite 构建。

**决定**:
1. **三屏结构(登录/工坊/暗房)**: 登录后全功能工坊(三栏: 画布 + AI理解面板 + 参数 inspector); 对话迭代不作平级 tab, 改为结果图上「继续迭代」进入暗房。理由: 用户调研——迭代念头是「看到这张图好看才有了别的点子」, 非用之前就有; 从图进入比重进一个 tab 顺。暗房大图在上、改动控件紧贴图下全宽、右侧竖向血缘演化条。
2. **AI 理解常驻结果旁**: breakdown(scene/composition/mood/lighting/style)不再藏在「先看翻译」后, 直接长在结果右侧, 露出「AI 真理解中文」核心卖点。翻译/编辑保持两段式: 输入栏「生成」静默翻译直接提交; 「先看翻译」填右侧面板, 面板内确认/再来一版/取消(在哪编辑在哪确认)。
3. **技术方式选 A(单 HTML + Tailwind CDN), 不上 React/Vue/Vite**: 前端是薄客户端, 后端三层 Prompt/Workflow/Intent Engine 才是主角; 引入构建链会多出用户看不懂的一层。零构建最贴「自己用+少量朋友」定位, 单文件内部分屏/功能分节保可维护。代价: Tailwind CDN 运行时编译、需联网, 若朋友网络打不开 cdn.tailwindcss.com 再换本地构建。
4. **历史画廊(localStorage 12 张)**: 画布下方横滑条, 点旧图只放大/下载。**继续迭代从原图直接进入**: 后端 `start-image` action 从已完成的 job 创建 session(原图当第一轮, 不重新生成), 前端 `toDarkroom` 调 `start-image` 直接进暗房显示原图 + 迭代控件。降级: 无 currentJobId 时回退 prompt 起步。
5. **保留隐藏原生 select**: 工作流/尺寸从下拉改卡片/宫格, 但提交逻辑靠 `$('workflow').value` 等取 id 值; 放三个 `hidden` select 由卡片同步 value, 提交逻辑零改动。

**收益**: 工作量外显(多工作流卡片/AI 理解/血缘); 单张与对话形态区分; 移动端竖排; 后端零改动。
**代价**: Tailwind CDN 依赖(可接受); 暗房首图非原图基准(后端限制); 单文件变长(分节可控)。

## D28. system prompt 信息分流重写 + quality_prefix 官方化

**背景**: 用户发现 LLM 输出 TAGS 后又用 NL 把输入重复翻译一遍(冗余)。根因排查: 旧 system prompt 的 3 个示例, 其 NL 全是 TAGS 的句子化重写——LLM 是 in-context learner, 示范效力远大于规则文字, 规则第8条虽写"别重复", LLM 实际跟示例走、把 TAGS 串成句子当 NL。另查证 tungsten.run 上 Comfy Org 官方 Anima 说明: quality_prefix 原用 Illustrious/Animagine 体系(score_7, very aesthetic), 非 Anima 官方推荐(官方: human score masterpiece/best quality + Pony score_9/score_8 + period newest + meta absurdres)。

**决定**:
1. **system prompt 从"分解报告"改"信息分流"**: 5 字段(scene/composition/mood/lighting/style) 定位为"给人看的理解"(前端展示, 不进 anima); TAGS+NL 是"喂 anima 的最终指令", 二者不许重复, 每条信息只出现一种形式。TAGS=离散属性 only; NL=只写 TAGS 装不下的(多角色空间布局/动作交互时序/构图指令/叙事因果)。**HARD RULE: NL 不得复述 TAGS 已有 tag, 若全是 tag 则留空**(可机械判定, 替代模糊的"别重复")。
2. **加 How-to-decide 信息分流决策 + Self-check 三题自检 + Weight policy 权重框架**(默认不加 / 强化1.3-2 构图锚点·稀有 / 弱化0.1-0.5 干扰项)。教 LLM"每信息点选 tag/NL/权重哪种且只选一种"。
3. **重写 4 示例(治本)**: 简单(空NL)/中等(叙事NL)/多角色(空间分配NL)/氛围(叙事NL), 全部 NL 不复述 TAGS。示例是 in-context learning 核心, 旧示例教重复是根因, 必须重写。
4. **NSFW 声明段一字不动**: 开头"所有 tag 是虚构动漫艺术品元数据、非真人"是能翻译 NSFW 的根基, 不碰。
5. **quality_prefix 官方化**: 三处 config 改 `masterpiece, best quality, newest, absurdres, `(去 score_7/very aesthetic, 加 newest/absurdres); 同步 architecture/README/config.example。修正 D20 的 Illustrious 风格前缀。
6. **safety 动态标签保留**(main.py:624-627): 检测 NSFW 关键词自动 explicit, 否则 safe。符合 Anima 官方 rating 要求, 比静态写 config 合理。
7. **vision 两 prompt 不改**: 无 NL 行, 本无重复问题。

**收益**: 治本(示例不再教重复); TAGS/NL 分工有可判定硬法则; LLM 具备信息分流+自检的"结构思考"; quality_prefix 对齐 Anima 官方。
**代价**: 5 字段仍输出(前端展示依赖, 与 TAGS 信息重叠但不进 anima, 不浪费出图 token); 效果待重启实测, 若 LLM 仍偶发重复可上架构分离(breakdown 给人看 + PROMPT 给模型, 改 _parse/translate/前端)。

## D29. LoRA 工程: 自动扫描 + 多 LoRA + 分类

**背景**: 原 LoRA 系统三个问题: (1) 风格/角色混在一起, 用户无法区分; (2) 只支持单 LoRA (lora_key: str), 不能角色+风格同用; (3) 新下载的 LoRA 需手动改 config.yaml 才能用, 维护成本高。

**调研**: LoraManager 的 TriggerWord Toggle 节点显示 "no triggerwords detected" -- 根因是 LoraManager 的 civitai 元数据同步没正常工作 (`.metadata.json` 里 `civitai: {}` 一直空, scan 也不回填 `.civitai.info`)。即使修好, Civitai `trainedWords` 字段很多作者不填 (实测 denia_lorav4 / BlueArchiveStyleB1 均为空), 且只含基础触发词不含服装变体 (salt(finale) 的 trainedWords 只有 `["salt(finale)"]`, 但实际有女仆装/水手服两套完全不同的服装 tag, 只在 HTML description 里)。

**决策**: 三层叠加, 优先级 config > Civitai > 裸文件:
1. **config.yaml 手动配置** (最高优先级): 人在 trigger words 上判断最准, 含服装变体 (同一 file 挂多条, 如 denia 一个文件三个角色)。加 `type: character|style` 字段分类。
2. **Civitai hash lookup 自动补全**: 后端启动时扫 `comfy_dir/models/loras/`, 对 config 未覆盖的文件按 SHA256 (优先读 LoraManager 的 `.metadata.json`, 没有才算) 查 `https://civitai.com/api/v1/model-versions/by-hash/{sha}`, 取 trainedWords/modelName/tags/baseModel。结果缓存到 `server/lora_cache.json` (gitignore), 不每次启动都请求。
3. **裸文件**: Civitai 查不到的, 只显示文件名, trigger 为空, 标记 `configured: false`。

**多 LoRA 注入**: `build_prompt` 的 `lora_key: str` 改为 `lora_keys: list[str]`, loras widget 数组注入多条, trigger 全部拼进 prompt。前端 `loras: ["key1", "key2"]` (向后兼容旧 `lora: "key"` 单选)。

**为什么不解析 HTML description**: 每个作者格式不同 (有的写「触发词：」有的写「Trigger:」有的写「Compulsory Taggings:」), 正则提取不可靠, 误判比不提取更糟。trainedWords 非空时自动可用, 为空时用户花 10 秒看一眼 Civitai 页面填 config。

**收益**: 新下载 LoRA → 有 trainedWords 的自动出现能直接用; 没有的标记未配置, 不再需要每次找 agent 加 config。角色+风格可同用。前端按类型分组展示。
**代价**: Civitai API 无 key 有限速 (加 0.3s 间隔); 大文件 SHA256 现算慢 (优先读 .metadata.json 规避); trainedWords 空的 LoRA 仍需手动配 trigger (无法自动化, 本质是 Civitai 数据质量问题)。

## D30. 修: 角色精确 tag 裸名变体去重 (防 LLM 输出 ganyu 触发原神 logo)

**背景**: 用户发现 char_dict 命中"甘雨"→`ganyu_(genshin_impact)` 后, 翻译结果仍含裸名 `ganyu`, 实测触发原神 logo(删 ganyu 即消失)。根因两层: (1) LLM 违规——D28 规则"Do NOT repeat or rephrase known tags"只防整串相等, `ganyu` ≠ `ganyu_(genshin_impact)`, LLM 不认违规, 且 `ganyu` 本身是合法 danbooru tag 当独立泛用名输出; (2) 后处理没兜——`normalize_tag_order` 只整串去重(seen set), 裸名不在 seen, 保留。原神 logo 触发: Anima 训练里 `ganyu`(裸名)强关联原神甘雨图(带 logo), 比 `ganyu_(genshin_impact)`(精确, 训练集被衍生图稀释)更"纯原神", 是触发 logo 的充分条件。

**决定**:
1. **代码兜底(A, 必须)**: 加 `_strip_char_bare_names(new_list, char_tags)`——对每个 char_tag 提取裸名(去 `_(series)` 后缀, 如 `ganyu_(genshin_impact)`→`ganyu`), 删 new_list 里等于裸名的项。在 translate() 的 siliconflow/vision 两分支 normalize 前调用。不依赖 LLM 听话。
2. **system prompt 加硬规则(B, 辅助)**: D28 那句"Do NOT repeat or rephrase known tags"补"If a known tag is the precise form name_(series), do NOT also output the bare name as a separate tag"。减少 LLM 输出概率。

**收益**: 治本(代码兜底, LLM 再不听话也不出裸名); 防 IP logo 误触发(不只原神, 任何 `角色_(系列)` 的裸名都可能弱化精度)。
**代价**: 裸名提取靠 `_(`/` (`分隔符, 极少数角色 tag 格式特殊可能漏(但 char_dict 都是标准 danbooru 精确 tag, 覆盖好)。

## D31. 修: 暗房 redo 替换意图检测 (换成X时删旧角色名防 char_dict 双命中)

**背景**: 用户暗房迭代"把图中人物换成天宫心", 结果 prompt_en 新旧角色并存(`ganyu_(genshin_impact)` + `amamiya_kokoro_(hololive)`) + 1boy 乱入, 出图仍是原神甘雨。根因: redo 实现(1162行)是"delta 累加到原 raw 末尾 + 整体重翻译", 把"换"语义当追加。累加后 raw 含两个角色名, `match_characters()` 双命中, 两个角色 tag 作为 known tags 喂 LLM, LLM 无法判断该删哪个, 全保留; 且 LLM 见两主体名误判双人加 1boy+1girl。tweak(img2img) 不受影响——denoise 默认 0.35 只微调(换姿势/去衣服), 用户不会用 tweak 换主体, 不触发双命中。

**决定**: redo 分支(1162)累加前检测 delta 是否含替换意图词(`换成/替换/改成/换为/改为`), 命中则遍历 CHAR_DICT.items() 从 session["raw"] 删所有旧角色名, 清多余逗号/空格, 再 += delta 重翻译。这样 raw 只剩新角色名, char_dict 单命中, LLM 输出干净替换。

**收益**: "换主体"语义正确执行(删旧加新); 防 char_dict 双命中导致的属性串色/IP logo; 不影响追加描述类 redo。
**代价**: 意图词靠枚举(换成/替换/改成/换为/改为), 用户换种说法("变成长椅上坐着天宫心")可能漏检; tweak 未改(实际用法不触发)。

## D32. 工作流合并: txt2img/img2img/detailer/inpaint 一份 JSON + 后端删节点拼接

**背景**: 原 3 个工作流 (anima 快速 / anima-detailer 精修 / anima-img2img 图生图) 每加功能要重新导出 API JSON, 排列组合爆炸。想用"一份全开工作流 + 运行时选功能"替代。

**调研**: (1) API 格式 JSON 无 MUTE 组信息, 不能运行时拨开关; (2) ImpactSwitch 的 select 虽可运行时切, 但**它所有输入都会执行**——精修链挂它后面, select=1 时精修链照样跑, 不省 GPU 时间, 违背"快速模式要快"; (3) 社区 AnimaDetailerV7 是图片编辑导向 (LoadImage 起, 无 txt2img), 不能直接顶快速工作流; 社区 AnimaStandardV7 才是正确基础 (有 txt2img+img2img+4路detailer)。

**决定**:
1. **基础 = 社区 AnimaStandardV7** (txt2img+img2img+4路 detailer), 嫁接 inpaint 链 (ImagePadForOutpaint→VAEEncode→SetLatentNoiseMask→KSampler→VAEDecode, 复用 LoadImage 0 的 alpha 作 mask)。
2. **后端删节点拼接** (真正省时): build_prompt 按 `detailer:{face,hand,nsfw,eyes}` 删未选 detailer 节点、重连; 删的节点不执行 (ComfyUI 懒执行, 不可达节点跳过)。快速=删全部 detailer, VAEDecode 直连 Save。
3. **inpaint 源切**: 有 image+inpaint 时, detailer 链源从主 VAEDecode(43) 切到 inpaint VAEDecode(206); 主 KSampler 变不可达不执行。
4. **调参**: 社区默认 max_size=1536 + steps=16 太慢 (3.2s/步, 全精修超时); 按 DEVLOG 第19条调成 **max_size=1024 + steps=12** (全精修 95s)。
5. **暗房**: tweak 的 `wf_name="anima-img2img"` 改 `"anima"` (img2img 由 image_filename 触发); 暗房加独立精修开关 (复用 detailerState)。

**收益**: 一份 JSON 覆盖 txt2img/img2img/精修(可逐路开), 旧 3 工作流退役; 删节点真正省时 (快速不跑 detailer)。
**注 (D33)**: inpaint 部分已撤销 (实测效果不达标, 见 DEVLOG 30 与 D33); 保留的是精修合并。
**代价**: build_prompt 拼接逻辑依赖工作流拓扑 (detailer 链 27→28→29→30 顺序写死在 config detailer_nodes); inpaint 链用纯 KSampler (弃 AnimaLLLiteApply, 那是 kohya 包的 `_sdscripts` 节点, 工作流用的 `AnimaLLLiteApply` 带 mask 版本包未找到, 降级纯 KSampler inpaint)。

**修复 inpaint mask 反转 (实测省时记结论)**: 该工作流 inpaint 采样器用**反转约定 (黑=重绘, 白=保留)**, 与标准 (白=重绘) 相反。前端 `getInpaintRGBA` 涂的区域 alpha=0 (重绘)、未涂 alpha=255 (保留)。另 inpaint denoise 0.9 (0.7 改不动色、1.0 叠角色; 0.9 单角色+改色+保留场景)。mask 尺寸必须与图对齐 (原先 ImageScaleToTotalPixels 把图缩到 1MP 但 mask 留原尺寸错位, 已去掉 resize 让二者同尺寸)。

## D33. 撤销 inpaint 局部重绘 (实测效果不达标)

**背景**: D32 在工作流合并时嫁接了 inpaint 链, 实测后效果不达标。

**问题**: ①该工作流 inpaint 采样器用**反转 mask 约定 (黑=重绘)**, 与标准 (白=重绘) 相反, 前端需反转; ②改发色时 mask 稍大就整头重绘成新角色, 紧贴头发丝才保脸, 但用户上手难; ③用户自己不用 inpaint, 网站也没几个人用, 投入产出不值。

**决定**: 撤销 inpaint。移除 AnimaFull.json 的 inpaint 节点 (200-206); build_prompt/page/链 inpaint 逻辑移除 (chain_source 固定主 VAEDecode 43); config 移除 inpaint_source/ksampler/denoise; 前端移除 inpaint 画布 (inpaint-section, getInpaintRGBA, 涂抹交互)。**保留**精修合并 (AnimaFull 一份 JSON 覆盖 txt2img/img2img/4路 detailer + 后端删节点拼接), 那是 D32 主成果。

**教训 (inpaint mask, 记下避免再踩)**: ComfyUI 通用约定白=重绘, 但**有些工作流/采样器反转 (黑=重绘), 必须实测**。inpaint 是"区域重绘"不是"改色": 改属性 (发色) 要精准 mask 紧贴目标, 否则整区域重生。改发色保同脸: denoise 0.9 + 紧贴头发 mask (不碰脸) 可行, 但 UX 难 (无缩放/撤销)。

**代价**: 局部重绘能力缺失; 需要局部修改时只能靠 img2img 低 denoise 或重新生成。见 DEVLOG 第 30 条。

## 待决策 / 方向
- **Intent Engine**: 迈向「理解用户意图」核心目标。**构图/场景/情绪的结构化分解已实现** (D18: LLM 输出 scene/composition/mood/lighting/style + TAGS); **否定语义解析弃用** (D18: Anima 负面是常量, 不随输入变); 仍待做: 歧义消解、LoRA/工作流自动推荐。符合 CLAUDE.md 规则 6。

## D34. Phase 1: Prompt IR 协议与语义 Compiler

**背景**: D28 已把 LLM 输出拆成 5 个给人看的 breakdown 字段、TAGS 和 NL, 但 `translate()` 最终仍主要返回字符串。复杂动作、多角色关系和后续增量修改没有稳定的中间表示, 且文本/视觉路径各自重复 tag 后处理。

**问题**: 直接切换成完整 JSON envelope 会扩大模型格式失败面; 让代码从 12 个 IR 字段独立推导全部 TAG/NL 又会提前引入 Phase 2 的字段级策略, 破坏 Phase 0 的行为基线。

**候选**:
1. 完整 JSON envelope (`prompt_plan + tags + nl`)。
2. 保留 5 字段并额外输出 IR, 兼容性最好但同一语义重复表达。
3. 用单行 IR JSON 取代 5 字段输出, 保留 TAGS/NL 作为当前编译候选, 旧行协议只作降级解析。

**决定**:
1. 采用方案 3。LLM 文本协议输出 `IR + TAGS + NL`; IR 固定 12 个字符串数组字段。
2. 后端从 IR 的 scene/composition/mood/lighting/style 派生原有 `breakdown` 形状, `/api/translate` 以 additive `prompt_ir` 字段返回; 前端无需改动。
3. Phase 1 中 TAGS/NL 仍是最终语义 prompt 的编译源, IR 用于结构化记录、breakdown 派生和回归度量。TAG/NL 字段策略留给 Phase 2。
4. 新增 `compile_prompt()` 统一角色裸名清理、去重、count→character→general 排序和 NL 拼接; `build_prompt()` 继续负责 quality/safety/LoRA/workflow。
5. 视觉 LLM 暂保留旧 5 字段协议, 解析器兼容但不把视觉 IR 纳入 Phase 1 门槛。
6. 增加最小模型护栏: 保留显式数量/单数物体关系, 多角色关系明确单一连续画面, 物理来源/附着关系保留在 NL。

**原因**: 单行 IR 保留现有行协议的降级能力, 避免重复输出 5 个展示字段; TAGS/NL 继续承接已验证的 D28 行为, 不让 Phase 1 同时承担 Phase 2 的表达策略; 编译边界把语义处理与工作流注入分开。

**代价**: IR 当前不是最终 Prompt 的唯一来源; 视觉路径暂不产 IR; 文本 max_tokens 从 400 调至 550; `prompt_ir` 增加 API 返回体但保持旧字段兼容。固定生图验收同时固定 Prompt、seed、尺寸, 避免 LLM reroll 污染视觉回归。

**验证**: 真实 `translate()` 评测两轮均 30/30 成功、30/30 IR 完整; `compare.py --require-ir` 通过; 7 个零依赖 Prompt 单测通过; 固定夹具 002/012/018 生图 3/3 通过内容验收。012 的锅柄与手连接仍需人眼复核, 不构成 Phase 1 阻塞。注：该 30 条 baseline 是未验证的 agent 产物，已于 2026-08-19 清理（见 DEVLOG 40），此处"30/30 IR"仅作历史记录，不再作为质量证据。

**相关文件**: `server/main.py`, `.tools/eval_set/image_cases.yaml`, `.tools/eval_set/run_gen_test.py`, `docs/api.md`, `docs/architecture.md`。

## D35. Phase 1.5 首轮 Rendering Strategy 实验结果

**背景**: Phase 1 的结构回归证明了 IR 可解析, 但用户人眼发现 018 的“对峙”被中央闪电和分页构图破坏。需要在不改变 checkpoint/workflow/seed 的情况下比较 Prompt 表达方式, 而不是继续凭格式推导规则。

**实验**: base Anima, `anima` workflow, 固定尺寸/seed/质量前缀/默认负面, 无 LoRA/无 detailer。7 个 case 比较 V1 TAG-only、V2 TAG+short NL、V3 weighted spatial NL、V4 NL-dominant；R2/R4 追加 V5 semantic negative。共 30 张图, 用户人眼盲评, vision agent 仅粗筛。

**用户结果**:

| Case | 用户胜者 | 结论 |
|---|---|---|
| R1 简单单人 | V1（差异不明显） | 简单内容没有证明 NL 必须存在 |
| R2 咖啡道具 | V3/V5 | 空间位置和单一道具关系有帮助；semantic negative 未证明额外收益 |
| R3 复杂单人姿态 | V3 | 明确“哪条腿/如何靠墙/如何看手机”的关系优于泛化 NL |
| R4 双人对峙 | V1/V4，但全部分页 | 没有可接受的全局胜者；base Anima 多角色构图问题未解决 |
| R5 逆光剪影 | V1/V2 | 过度强调 silhouette 会损失脸部可读性 |
| R6 成人 NSFW 单人 | V1 | TAG-only 的手部动作最可信；NSFW 暂偏 tag-first |
| R7 成人 NSFW 双人 | V1/V2/V4 | TAG、短 NL、NL-dominant 均可；weighted V3 产生黑线分页 |

**决定**:

1. 不把 V1/V2/V3/V4 任一方案升级为全局固定模板。
2. IR 继续保留为内部语义表示；最终渲染策略按语义类型选择，是后续 Compiler 方向。
3. weighted spatial NL 暂不进入生产默认策略；本轮在 base Anima 上不稳定。
4. semantic negative 暂不进入生产默认负面；R2 平局、R4 仍失败，证据不足。
5. `girl` vs `female` 本轮没有隔离变量。用户观察“`girl` 比 `female` 更适合当前二次元模型”作为后续词汇实验假设；NSFW 继续使用 canonical count `1girl` + 明确 `adult woman` 语义，不把 `female` 擅自替换成全局规则。
6. R4 换 seed 专项复测后，V1 在新 seed 得到一张连续画面，但其余变体仍分页/黑线/错位，且五张都没有稳定表达“后撤”。不继续为该 case 堆自动规则，记录为 base Anima 多角色构图/动作绑定限制；用户通过现有可编辑 `prompt_en` 手动辅助。

**未决定**: Compiler 2.0 的显著性管理、动态渲染 profile、语义负面和词汇 canonicalization 仍不固化；本轮只把实验证实的差异记录为策略候选。PLAN-v6 等下一轮证据，不为 R4 单点问题继续扩张主线。

**相关文件**: `.tools/eval_set/render_exp/cases.yaml`, `.tools/eval_set/render_exp/results.yaml`, `.tools/eval_set/render_exp/output/review.html`。

## D36. Phase 2 首轮渲染 Profile 与来源对照结果

**实验**: W3 使用同一 TAG/NL 组件比较 legacy（总是保留 NL）与 profile；W4 比较 Dictionary-style 与 LLM/NL-style Prompt；W6 比较 `girl` 与 `female` 词汇。全部固定 base Anima、workflow、尺寸和 seed，并由用户人眼评审。

**结果**:

- W3：P01 legacy 胜出；P04/P06 profile 胜出；P02/P03/P05/P07/P08/P09 基本平局；P10 两者都分页失败。P03/P08 的动作/姿态本身画错，不能归因于 NL 长短。
- W4：D01/D04/D06/D07/D08 Dictionary 胜出；D03 LLM 胜出；D02/D05/D09/D10 平局。Dictionary 在 canonical 外观、光影和 NSFW tag 上更可靠，但没有形成全局优先级。
- W6：两条 `girl`/`female` 对照均无明显差异。用户经验仍保留为后续词汇假设，但本轮不改 canonical。

**决定**:

1. `infer_render_profile()` 收窄为：明确成人 NSFW、单主体、无复杂关系/复杂动作时使用 `tag_first`；普通 SFW 不自动删除 NL，复杂关系继续 `relation_hybrid`。
2. 不把 profile 泛化到所有简单 SFW；P01 的 legacy 胜出否定了全局删 NL。
3. Dictionary-first 仅作为 canonical appearance/lighting/NSFW tags 的候选策略；新颖物体或关系仍允许 LLM candidate。暂不写死全局 Dictionary > LLM。
4. weighted spatial NL、semantic negative、`girl/female` 替换不进入默认生产策略。
5. 保留用户 Prompt 编辑作为复杂动作/多角色失败的正式 fallback；不为 P10 单点问题扩张架构。
6. 继续使用 failure taxonomy 分析 counting、binding、action/pose、interaction、spatial 和 anatomy，而不是把所有失败归因于 Prompt 长短。

**相关文件**: `.tools/eval_set/render_exp/phase2_results.yaml`, `.tools/eval_set/nsfw/`, `server/main.py`, `.tools/eval_set/taxonomy.yaml`。

## D37. Phase 2.5/2.6 Prompt Expansion 三路实验与生产落地（最终）

**背景**：Phase 2 首轮验证了渲染 profile、Prompt 来源和失败类型，但没有回答 AirPaint 的核心问题：短中文输入是否能通过画师级补全变成更可靠的完整画面。上一轮 E1-E7 的 V1/V2 只形成了两路固定 Prompt 对照，尚未隔离“用户写得更详细”与“系统自动补全”的差异。

**问题**：如果直接把更长 Prompt 视为改进，可能把无依据的服装、道具、场景或表情误加到用户意图中；如果只看结构完整，也会漏掉构图、光影、材质和 NSFW 表情张力的实际画面收益。

**候选**：
1. A1：短中文输入走当前生产翻译链路。
2. A2：同一意图改写为由 agent 代写、用户执行前抽查的口语化中文长描述，再走当前生产翻译链路。
3. A3：短中文输入走实验脚本内的画师补全协议，直接调用模型 API；不修改生产 system prompt。

**决定（实验协议）**：
1. 固定 base Anima、workflow、尺寸、seed 和默认负面；7 个 case 各生成 A1/A2/A3，共 21 张图。
2. A3 只允许在实验脚本内执行五层补全：主体锁定、动作/姿态、场景与保守道具、构图/镜头、光影/氛围/材质/风格；使用 Danbooru 语系，约 20 个元素上限，不输出负面 Prompt。
3. A3 的道具规则为保守添加，不新增无依据角色、武器或改变主体行为；明确 NSFW case 只分流服装状态、身体语言、揭示节奏和表情张力，不用年龄词堆叠质量。
4. 人眼按 A1/A2/A3 盲评；平局换 seed 补测。单次手脚等随机伪影不判策略失败，换 seed 复现后才进入 taxonomy。
5. 预先固定判定矩阵：A3 ≥ A2 才进入生产改造；A2 > A3 则转向辅助用户写详细中文；全平则终止并归档，不因 Prompt 更长而强行落地。

**代价/边界（实验前状态）**：本草案不修改旧生产 system prompt、Prompt IR 生产协议或默认负面；在结果出来前不引入 `prompt_ir_meta`、reroll 补全语义或生产级画师协议。

**验证**：21 张图已生成并完成第一轮盲评，关键平局换 seed 补测 9 张并完成第二轮评审；生产后 5 case 新旧 A/B 生成 10 张，用户确认 3 胜 2 平 0 负。15 个 Prompt 单测、30 条 SFW IR 回归、8 条 NSFW explicit safety 回归通过。

**相关文件**：`.tools/eval_set/render_exp/expansion/phase26_cases.yaml`、`.tools/eval_set/render_exp/expansion/run_phase26.py`、`.tools/eval_set/taxonomy.yaml`、`.tools/eval_set/render_exp/labels.yaml`。

### 第一轮人眼结果（2026-08-16）

盲评页面每组只有 A/B/C 三个位置，`tie` 仅表示两张图并列，不是额外图片分组。review key 解码后的结果：

| Case | 结果 | 胜出 arm | 主要观察 |
|---|---|---|---|
| E1 | tie | A2/A3 | 两者都好且各有特色；A1 明显 AI 画风 |
| E2 | B | A2 | A3 也不错，但 A2 细节完胜 |
| E3 | A | A2 | A1/A3 平平无奇 |
| E4 | A | A3 | A2 整体不行；A1 可看但女孩只有剪影、无人物细节 |
| E5 | B | A3 | A1 变成只有眼睛的特写；A2 有细节但 AI 画风明显 |
| E6 | tie | A3/A1 | 两者色气程度都好；A2 一般且没有诱惑力 |
| E7 | tie | A3/A1 | 两者都好；A2 把画面缩在很小空间 |

**阶段判断**：第一轮 A2 在 E2/E3 胜出，A3 在 E4/E5 胜出，A1/A3 在 E6/E7 并列，E1 为 A2/A3 并列。第二轮换 seed 后，E1 A2/A3 继续并列，E6 A1/A3 并列且 A2 因缺少人体失败，E7 A3 胜出且 A2/A1 均失败。汇总 A3 对 A2 为 4 胜、1 平、2 负，满足预定 `A3 ≥ A2` 判定，允许进入生产改造。`style_artifact`、`lighting_style`、`spatial_composition`、`semantic_misread` 已用于对应失败记录。

**生产改造边界**：只落地经过实验支持的画师补全协议；不把“更长 Prompt”本身作为目标。保留显式用户约束，补全聚焦主体可读性、动作/姿态、场景锚定、构图、光影、材质和 NSFW 表情/身体语言；生产回归失败则撤回本轮改造。

**相关结果**：`.tools/eval_set/render_exp/expansion/phase26_results.yaml`、`.tools/eval_set/render_exp/labels.yaml`。

### 生产改造后的新旧 A/B 验证

生产画师协议落地后，复用 E1/E2/E4/E6/E7 的旧 A1 Prompt 与新 `translate()` 输出，固定原 seed/尺寸/workflow/默认负面生成 10 张图，结果全部成功。用户按 A/B 盲位评审后确认 E1/E2/E4 新协议胜出，E6/E7 平局，合计 3 胜 2 平 0 负。

初版生产 A/B 使用旧 `IR + TAGS + NL` 协议叠加画师规则，实际新 Prompt 仍接近旧短翻译，且暴露主体计数、未请求剪影和 NSFW 景别护栏问题，不能作为生产收益证据。随后改为独立 `IR + PROMPT` 协议并增加代码护栏；v5 证实提示词增强保留，但 A2 的详细中文在部分 case 更强，自动扩写不替代用户具体视觉意图。

### 收尾决定

保留 v5 提示词增强、`prompt_ir_meta` 和 reroll 新补全语义；冻结继续增加自动扩写规则。自动增强负责通用可读性、构图、光影、材质和 NSFW 身体语言，不能凭空决定用户未表达的具体创意。Phase 2.6 完成后先观察真实使用反馈，不立即投入详细输入辅助 UX 或新的扩写实验。

## D38. Phase 3 Character Knowledge：平铺自动缓存 + Danbooru 可绘制性门槛

**背景**：正式 `char_dict.yaml` 目前有 156 条平铺角色映射，用户自己新增一行即可通过 HotDict 热更新生效。为此重构结构化 `char_dict` 或全量审计的成本高于收益；真正未解决的问题是未知角色名会落入普通 LLM misses，可能产生字面翻译、假 tag 或裸名。

**决定**：
1. 生产画师协议的 `IR.subject` 记录未知角色的候选 Danbooru tag；解析器兼容可选 `CHAR: 用户名 => 候选 tag` 行，但不强制增加第三输出行。
2. 后端查询 Danbooru `tags.json` exact tag；要求 `category=4`、非 deprecated，并以 `post_count >= character_auto_min_posts`（默认 100）判定 `likely_supported`。这是训练覆盖度代理，不等于 Anima 已完成人眼验证。
3. `likely_supported` 结果写入独立平铺 `server/knowledge_cache/characters_auto.yaml`，正式 `char_dict.yaml` 优先；`weak`/`absent` 只写 lookup JSON，`unavailable` 不缓存以便网络恢复后重试，不污染正式词典。
4. 自动缓存复用现有 `match_characters()`、裸名保护和 dialog redo 删除逻辑；不改 HotDict 值结构，不迁移 156 条正式词典，不做全量人工审计。
5. 自动缓存是运行时知识，不进 git；用户可以删除自动条目或复制到正式 `char_dict.yaml` 手动提升。
6. Danbooru 不可达（`unavailable`）时降级：将 LLM 归一化后的候选 tag 补进本次 Prompt，但不写 auto cache、不缓存不可达状态，等待网络恢复后重试；`absent`（Danbooru 查不到/非角色 tag）不补 tag。

**原因**：Danbooru 的 canonical tag 和 post_count 同时提供 tag 验证与当前 base Anima 可绘制性的低成本代理；对项目 owner，手动加一行仍是最快路径；自动缓存主要服务其他邀请用户，并避免未知角色每次重复查询。

**边界**：Danbooru 查询失败不阻断翻译；不自动覆盖正式词典；不存在可验证 tag 时不伪造 canonical tag。Phase 4 PromptState 继续延后到暗房真实使用证明字符串状态不足。

**验证**：Danbooru 主 API exact lookup 与 `name_matches` 已从本机连通；代理波动时查询正确返回 `unavailable`，且不会永久缓存。角色 hint 解析、category/post_count 分类、auto cache、最长优先和 safety marker 的 22 个 Prompt 单测通过。长门有希→`nagato_yuki`（9254）、御坂美琴→`misaka_mikoto`（10778）均已写入 auto cache；长门固定 Prompt 已成功生图并经用户确认。

**相关文件**：`server/main.py`、`.tools/test_prompt_unit.py`、`.gitignore`、`server/knowledge_cache/`、`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`。

## D39. LoRA Context / Binding：LLM 选语义，代码编译 exact trigger

**背景**：现有链路先由 `translate()` 生成完全不知道 LoRA 的 Prompt，再由 `build_prompt()` 全量 prepend config/Civitai trigger。实测会让人物细节挤压场景、构图和光影，也可能让普通 Prompt 与实际加载的 LoRA 表达两套冲突语义。2026-08-19 版 PLAN-LORA 试图让 LLM 逐字复制 quick-use，再由代码替换转义错误；但裸角色/词典全命中/vision 等路径会绕过文本 LLM，translate 与 jobs 之间也没有信息能区分“已 LoRA-aware”与旧 Prompt。

**问题**：如何让模型在规划 Prompt 时真正知道用户选中的 LoRA，同时保持 trigger、文件名和强度确定、可验证、可兼容旧调用？

**候选**：
1. 继续在 `build_prompt()` 盲拼所有 trigger；简单但继续割裂。
2. 让 LLM 输出完整 exact trigger，代码做字符串校验/替换；能看上下文，但格式脆弱且把 canonical 数据交给模型。
3. LoRA Asset + Semantic Profile；LLM 只选择 registry 候选 ID，代码用 Binding Compiler 编译 exact trigger，并让 translate/jobs/dialog 共用 binding snapshot。

**决定**：采用候选 3。

1. 人工知识进入 versioned `server/lora_registry.yaml`，按 Asset/Profile 保存 `provides/required_tags/default_tags/optional_tags/source/verified`；自动 `lora_cache.json` 继续 gitignore。
2. 新增保留嵌套结构的 `HotLoraRegistry`，不复用会执行 `str(v).strip()` 的 `HotDict`。
3. active LoRA/Profile 在翻译前进入 Reasoning/Vision Model 上下文；有 LoRA 时文本快速路径也必须进入 LoRA-aware painter 规划。
4. explicit Profile 由用户锁定；auto 只能从提供的 profile ID 中选择。没有匹配时只允许 registry 明确的 default，不取数组第一项。
5. LLM 不输出文件名、强度或 exact trigger；Binding Compiler 从 registry 确定性、幂等地合入 required/default tags。
6. `/api/translate` 返回 final `prompt_en + lora_bindings + registry_revision + warnings`；jobs/dialog 使用同一 binding/revision，避免预览与实际生成错位。
7. 翻译缓存 key 纳入 selection/profile/registry revision；LoRA alias 与 Character Knowledge 去重，避免同一角色重复 Danbooru lookup。
8. 旧 `loras`/`lora` key 通过 legacy adapter 兼容；真实 A/B 通过后才将 aware 链设为默认。

**原因**：语义匹配属于 LLM，canonical trigger、文件、权重、版本与 workflow 注入属于代码；该边界同时解决 Prompt/LoRA 割裂、转义脆弱、快速路径绕过、缓存串线和暗房状态漂移。

**代价**：API 需要新增 selection/binding/revision 契约；前端要支持多 Profile 与 stale Prompt；dialog/JOBS 需保存 binding snapshot；registry/loader/scanner/onboarding 都要成套实现，工程量高于字符串拼接。

**验证（2026-08-23）**：首版实现完成。41 个确定性单测覆盖 nested loader/last-good、legacy adapter、Profile/optional 白名单、revision 409、scanner inventory、本地 `.civitai.info`、Binding 幂等、cache 隔离和 text/vision/jobs/dialog 贯通；前端内联 JS 与 81 个元素引用检查通过。5 组 fixed-condition A/B 经用户人眼得到 aware `1 胜 / 4 平 / 0 负`；DeepSeek 换 Anima 专用 LoRA 后，aware/legacy 图书馆结果均被接受且优于旧 IL 版本。多人 composition 仍因无真实资产不计为完成。

**相关文件**：`server/main.py`、`server/lora_registry.yaml`、`.tools/register_lora.py`、`.tools/test_prompt_unit.py`、`.tools/eval_set/render_exp/lora_context_cases.yaml`、`.tools/eval_set/render_exp/run_lora_context_ab.py`、`web/index.html`、`docs/PLAN-LORA.md`、`docs/api.md`、`docs/architecture.md`。

## D40. LoRA 入库 Agent：LLM 结构化候选，代码守住作者硬事实

**背景**：手工 Registry 能保证 LoRA 语义质量，但每次新增复杂 LoRA 都依赖主开发对话重新读取作者 description、拆分 Profile 并编辑 YAML。LoRA Manager SQLite 只解决 ComfyUI 找文件，不理解人物/形态/装饰；把全部入库工作长期交给主 Agent 会浪费上下文，也不便于项目 owner 独立维护。

**问题**：如何复用现有 Reasoning Model 降低复杂 LoRA 入库成本，同时不让模型静默改写 exact trigger、文件名、强度或验证状态？

**决定**：扩展 `.tools/register_lora.py --agent`，并提供双击入口 `.tools/start_lora_onboard_agent.bat`。

1. 工具启动时尝试调用 LoRA Manager 增量 scan，随后列出未注册图片 LoRA；ComfyUI 未运行时安全跳过，不启动或关闭服务。
2. 维护者粘贴多行作者说明；Reasoning Model 只生成 Asset/Profile/provides/default/optional 的候选结构，并可根据 `revise` 自然语言意见重做。
3. 作者说明按不可信文本处理；API key/model 只从 gitignored `server/config.yaml` 读取，key 不复制、不打印、不写文档或 Registry。
4. 本地文件名、`source` 与 `verified:candidate` 由代码强制覆盖。逗号 prompt fragment 确定性拆项，泛化 `white/black/白/黑` 不进入 substring alias。
5. 作者原文中能验证的 exact trigger 转义由代码恢复；明确的单一通用推荐强度由代码应用到 model/clip。范围或分别声明的 model/clip 值不静默折叠，留给人确认。
6. 候选只在维护者输入 `write` 并再次输入最终 `y` 后原子写入；真实生图前不自动提升 verified。Civitai URL/sidecar 仍只是候选来源。

**原因**：语义拆分适合 LLM，文件系统事实、exact token、数值证据、schema 与写入权限适合代码。小型一次性上下文足以处理单个 LoRA，不依赖主开发对话的长期记忆，同时保留人工知识门槛。

**代价**：当前是本地终端向导而非网页管理页；候选仍需维护者检查，复杂或含糊作者说明可能需要一次 `revise`。LoRA Manager 与 AirPaint Registry 仍是两套职责明确的索引，不伪装成完全自动发现即生产可用。

**验证**：9 个确定性测试覆盖 fenced JSON、Remi base/white/black/swim Profile、单值强度硬事实、范围不折叠、exact trigger 转义恢复、泛化 alias 过滤、无 trigger/required 风格和 Civitai URL 分支。使用真实 Remi 作者说明进行了多轮 dry-run，Registry 均在取消后保持不变；最终规则得到 base + 三形态、0.7/0.7 与作者 exact trigger。三个新资产以 candidate 注册，等待真实生图验收。

**相关文件**：`.tools/register_lora.py`、`.tools/start_lora_onboard_agent.bat`、`.tools/test_lora_onboard_agent.py`、`server/lora_registry.yaml`、`docs/architecture.md`、`docs/BUILDHANDOFF.md`。

## D41. PLAN-LORA 最终关闭：fail closed、无独立冲突检测、显示名归 Registry

**背景**：用户确认新增 Remielle Dan、Dolphro-kun 与 Light LoRA 均实际生效，并要求核对 PLAN-LORA 是否全部完成、后续如何修改用户可见名称。

**复核**：Step 0-10 与核心目标均已实现并经过人眼验收，但计划正文有三处比代码更宽：SiliconFlow 服务失败降级继续生成、结构化 semantic conflict warning、前端 minimal tags 摘要。实际代码分别为 502 fail closed、LoRA context 协议抑制冲突、前端只展示 provides/Profile/verified。

**决定**：LoRA 工程按真实首版边界关闭，不为逐字满足旧计划临时增加未经验证的故障生成或冲突启发式。`none/google` 配置降级仍注入 binding 并 warning；SiliconFlow/Vision 真故障 fail closed。跨文件多人 composition 继续等待真实资产，不计入本工程未完成项。

LoRA 用户可见名称以 versioned `server/lora_registry.yaml` 为单一真相：Asset `name` 控制选择器与总览名称，Profile `name` 控制二级形态按钮。前端只负责渲染 `/api/loras`，不得按 key 硬编码中文名；Registry 修改经热加载后刷新网页即可生效。

**验证**：用户确认三项新增资产生效；Registry 提升为 verified。代码审计确认 `/api/loras → l.name/profile.name → web` 数据流、显式 Profile UI、stale binding、warnings 与 502 失败路径。

**相关文件**：`server/lora_registry.yaml`、`server/main.py`、`web/index.html`、`docs/PLAN-LORA.md`、`docs/architecture.md`。

## D42. 网站第一批细化：画布优先布局与分级画幅

**背景**：1920×950 实际工作台中，竖图显示高度只有约 418px；横跨主区域的输入框与 112px 历史缩略图占用大量垂直空间。尺寸只有三个平铺按钮，且 `1216x832` 不在用户当前 Anima workflow note 的推荐画幅中。

**问题**：如何在不推翻既有暗房视觉和 Prompt/LoRA 工作流的前提下，让成图成为真正的视觉中心，并向 8GB RTX 4060 开放有边界的高分辨率？

**候选**：
1. 只扩大画布宽度；竖图受高度限制，收益很小。
2. 桌面锁定一屏高度，压缩输入/历史、收窄 AI 理解栏；尺寸改为分组画幅选择器。
3. 默认全屏看图、其余控制隐藏；成图最大，但会破坏当前可同时检查 Prompt 和参数的工作方式。

**决定**：采用候选 2。桌面工坊使用确定的一屏高度，紧凑“拍摄指令条”与接触印样历史带把空间还给画布；移动端继续纵向排列。尺寸选择器点击展开、选择即收起，用画幅轮廓、方向和 MP 辅助识别。

标准档为 `832x1216 / 896x1152 / 1024x1024 / 1344x768`；高分辨率实验档为 `1024x1536 / 1536x864`，显式提示更慢和显存压力。删除 `1216x832`，暂不开放 `1152x1536 / 1536x1536`。`timeout_seconds` 继续作为约 1MP 基准，`generation_timeout_seconds()` 按像素面积放宽、上限 900 秒；前端高分辨率状态显示 2～5 分钟。

**原因**：这套改动解决的是竖图的高度瓶颈，而不是表面加宽；尺寸分级让用户仍可试高分辨率，但不会把接近显存上限的选项伪装成默认安全档。保留画布、AI 理解和参数同时可见，符合 AirPaint 面向懂 Prompt/ComfyUI 用户的定位。

**代价**：高分辨率显著更慢；8GB 显存下不承诺 detailer 组合。桌面使用一屏工作台，极低高度窗口需要依赖面板内部滚动；移动端尺寸菜单必须使用文档流展开，避免被参数卡片裁切。

**验证**：浏览器验证 1920×950、1280×720、390×844；宽屏画布高度约从 418px 增至 538px，输入区约从 222px 降至 155px。`1024x1536` 无 detailer 在 RTX 4060 Laptop 8GB 上真实成功，峰值显存约 7.75GB，输出 `anima_20260823_00014_.png`；旧 300 秒 deadline 先于 ComfyUI 完成误报超时，因此修正为 450 秒。42 个确定性单测、Python/内联 JS/DOM 引用检查通过。

**相关文件**：`web/index.html`、`server/main.py`、`server/config.example.yaml`、`.tools/test_prompt_unit.py`、`docs/api.md`、`docs/architecture.md`。

## D43. 显式选择生成分支并纠正 D42 的高分辨率结论

**背景**：用户检查成品文件发现，网站选择其他尺寸后多数输出仍为 832×1216；D42 记录的“1024×1536”任务日志显示 `SELECTED: input2`，30 步采样只用 47 秒，但总执行 309.72 秒。原生 ComfyUI 的真正 1024×1536 走 `input1`，总执行约 72 秒。

**问题**：D42 只检查了请求值、节点 56 和任务成功状态，没有检查 ImpactSwitch 最终分支与成品像素，因此把错误分支的耗时当成高分辨率性能，并据此增加了像素面积 timeout。

**决定**：`build_prompt()` 每次显式设置 ImpactSwitch：txt2img=`select=1`（节点 56 EmptyLatent），img2img=`select=2`（节点 33 VAEEncode）；工作流节点 32 的安全默认值改为 1。删除 `generation_timeout_seconds()`，恢复所有尺寸使用统一 `timeout_seconds`。前端成品徽标读取图片的实际像素，不再只复述请求值。

**原因**：生成模式是离散、可验证的程序状态，不能依赖工作流文件的历史 widget 默认值。txt2img 误走 `input2` 时，链路实际使用 `salt.jpg -> Resize(832×1216) -> VAEEncode`，既绕过请求尺寸，又引入采样前的图片处理与动态显存换入。放宽 timeout 只会掩盖路由错误。

**代价**：工作流文件和后端各自都保留安全选择，存在少量重复；这是有意的纵深防护。高分辨率仍比约 1MP 档更慢，且不承诺与 detailer 组合的 8GB 显存表现，但目前没有证据需要按像素自动延长 deadline。

**验证**：固定无 LoRA/detailer 请求 1024×1536，实际输出 `1529ed18e206.png` 经 PIL 确认为 1024×1536；Comfy history 为 `select=1`、节点 56=`1024x1536`，Comfy 执行约 82.3 秒、端到端 84.16 秒。旧任务 history 为节点 32=`2`、输出 832×1216、执行约 309.7 秒。42 个确定性单测覆盖 txt2img/img2img 双分支。

**修订关系**：本决定修订 D42 中“旧任务是真实 1024×1536”“300 秒来自高分辨率”“应按像素面积放宽 timeout”三项结论；D42 的布局与尺寸选择器决定继续有效。

**相关文件**：`server/main.py`、`server/workflows/AnimaFull.json`、`.tools/test_prompt_unit.py`、`web/index.html`、`docs/workflow-anatomy.md`、`docs/architecture.md`、`docs/api.md`。

## D44. 取消自动 rating 推断，并把 DeepSeek 长触发词改为条件配方

**背景**：DeepSeek 女仆请求的最终语义只有 `exposing crotch`，没有命中 `build_prompt()` 的英文 NSFW 关键词集合，于是后端自动追加 `safe`。同时 `deepseek_maid` Registry 把作者说明里的“正面/三分之四身份段 + 正面全身服装段”平铺成 30 余个通用 `default_tags`，无论视角和动作都强制注入完整服装、袜子与鞋子。

**问题**：rating 不是可靠的关键词分类问题。翻译的同义改写、漏译和中文原意都会让固定英文词表误判；`safe`/`explicit` 一旦被错误加入，还会与真实画面意图冲突。DeepSeek 作者说明本身按正面身份、正面全身、正面腰上、纯背面和侧面分段，旧 Registry 将条件配方误写成了全局默认值。

**决定**：删除 `build_prompt()` 的自动 `safe/explicit` 推断。LLM 不自行输出 rating tag；用户在可编辑英文 Prompt 中手动加入的 `safe/sensitive/questionable/explicit` 原样保留。`_prepare_painter_tags()` 对用户明确裸体意图的内容词保真和构图护栏继续存在，它不再被称为 rating 判定。

`deepseek_maid/maid` 默认只注入作者明确的 `deepseek_whale_girl + deepseek_maid_outfit`。作者给出的五类长清单进入白名单 optional 配方，仅在原文 alias 或 LoRA-aware LLM 判断用户明确要求对应视角时加入。Profile 改为 `candidate`：旧三张图片只验证过正面全量绑定，不能证明新的最小默认绑定质量。

**原因**：rating 控制权已经在生成前的英文 Prompt 编辑器中，后端猜测没有增加用户能力，反而引入静默冲突。LoRA Registry 应保存 exact trigger、已提供概念和条件知识，不应把某一构图示例伪装成所有请求都需要的 minimal tags。

**代价与风险**：未手动填写 rating tag 的请求将不再带 rating；如果 Anima 对 rating tag 有显著偏好，需要用户明确添加。最小 DeepSeek binding 可能降低身份或服装细节稳定性，必须用固定工作流/seed 的真实图片与旧全量绑定比较后，才能提升为 verified 或调整最小集合。

**验证**：确定性测试覆盖普通/裸体 Prompt 均不自动追加 rating、手动 `safe` 只保留一次、DeepSeek 默认仅两个 exact trigger，以及“正面全身”同时选中身份与全身服装配方。结构测试不构成图像质量结论。

**修订关系**：本决定废止 D28 第 6 项的动态 safety 标签，修订 D33 中 `build_prompt()` 的 quality/safety 职责；其余决定继续有效。

**相关文件**：`server/main.py`、`server/lora_registry.yaml`、`.tools/test_prompt_unit.py`、`docs/architecture.md`、`docs/PLAN-LORA.md`。

## D45. 状态连续的三栏工作台与纸本/石墨双主题

**背景**：D42 的一屏压缩方案扩大了画布，但实际使用仍暴露三类问题：初次进入时输入框不够醒目；Prompt、图片和参数没有形成稳定对齐关系；竖图周围大面积纯黑留白，参数徽标与操作按钮又叠在图片上。随后两轮独立原型还证明，靠动画压缩/铺开表单会产生明显廉价感，英文 Prompt 放到结果下方也会打断下一张图的修改流程。

**问题**：如何在不删减登录、翻译预览、参考图、动态尺寸、LoRA Profile、历史和暗房等生产功能的前提下，让首次描述、生成结果和继续迭代保持同一套空间语义，并同时提供可用的日间/夜间材质？

**候选**：
1. 继续微调 D42 的中央大画布 + 侧栏；改动小，但 Prompt/图片/参数对齐和首次入口问题不消失。
2. 用复杂 GSAP 动画把输入区压成结果页；视觉变化明显，但会动画布局尺寸、挤压参数并在低性能设备上抖动。
3. 固定三栏骨架，只切换内容显隐；首次为“画面描述 + 成像设置”，预览为跨两栏 Prompt 检查，出图后为“Prompt 左 / 图片中 / 参数右”，历史位于图片区下方；暗房复用“控制左 / 图片中 / 脉络右”。

**决定**：采用候选 3，并修订 D42 的生产布局部分；D42/D43 的画幅选择器、实际像素徽标和生成路由继续有效。

1. 桌面主骨架固定为 `324px / flexible / 360px`，画面描述跨 Prompt 与图片两栏，参数栏保持固定宽度；图片工具栏独立于媒体区域，不再覆盖成图。
2. 首次进入不显示空画布与历史；首次“先看翻译”显示跨两栏 Prompt 检查。已有结果后再次翻译只更新左侧 Prompt，图片和参数保持原位，不退回首次界面。
3. 媒体区域按真实图片等比 contain，使用当前图的低透明模糊背景填充余量；不把 1536 像素当 CSS 显示上限，也不强制画布跟原图像素一一对应。
4. 移动端保持纵向滚动：描述 → 图片 → Prompt/参数页签 → 历史；暗房为图片 → 控制 → 脉络，不缩放或并排挤压复杂表单。
5. `纸本画室` 与 `石墨暗房` 是同一功能界面的日间/夜间材质。纸本使用暖纸底、墨绿 `#334b51` 统一品牌/选中/主操作；石墨使用暖黑底与安灯橙。主题写入 `localStorage`，工坊和暗房同步。
6. GSAP 只用于视图切换时的 opacity/translate，禁止动画面板宽高、grid 列或表单几何；`prefers-reduced-motion` 下直接切换。
7. 生产 DOM ID、API 路径和现有状态语义保持不变；LoRA 名称/Profile 继续完全由 `/api/loras` 动态渲染，不把原型演示数据带入生产。

**原因**：固定骨架让用户形成稳定空间记忆；内容状态只决定哪些区域可见，不迫使参数栏和文本框在动画中重新排版。双主题是材质差异而不是两套功能分支，因此不会增加接口或状态维护成本。

**代价与风险**：页面整体高度不再强制锁死一屏，竖图和完整历史可能需要纵向滚动；GSAP 与 Tailwind 仍通过 CDN 加载，网络失败时布局与功能可用但过渡动画会降级；`web/` 为独立仓库，前端与根仓库文档必须分别提交。

**验证**：生产内联 JS 语法、122 个 DOM ID、重复/缺失引用检查通过；模拟真实 API 的浏览器验收覆盖 1920×1080 与 390×844、日夜切换、LoRA 多 Profile、首次翻译、确认生成、有图后重翻译、Prompt/参数移动页签和暗房。真实 `127.0.0.1:8000` 鉴权/工作流/LoRA 接口均返回 200，并完成一次 `/api/translate → /api/jobs → /api/jobs/{id}` 真实任务；输出 PNG 为 832×1216。

**相关文件**：`web/index.html`、`docs/architecture.md`、`docs/DEVLOG.md`、`ROADMAP.md`。

## D46. Visual Composer：三档补全、可编辑构思与自由 Anima Prompt

**背景**：真实使用再次暴露出旧画师协议的边界：固定约 20 个元素与历史 TAGS/NL 形态会把详细构思压回短翻译；`dict.yaml` 全命中时甚至完全绕过 Reasoning Model。与此同时，用户提供的高质量 Anima 样本证明，模型可同时理解 canonical tag、短英文关系描述和自然语言段落，Prompt 的有效形态不应由后端预先限定。此前画质实验也已经证明，没有一种 TAG-only、NL-dominant 或固定权重格式能全局胜出。

**问题**：完全放任模型会让稀疏输入的创意方向不可见、详细输入被擅自改写，也可能产生整段复读、互斥景别或与 active LoRA 重复争夺概念；继续依赖普通词典和固定格式，则只能得到“中文换成英文”的 translator，无法成为面向 Anima 的构图器。

**候选**：
1. 保留旧协议，只提高字符与元素上限；不能解决词典绕过、固定形态和不可见补全。
2. 删除所有约束，让 Reasoning Model 直接返回任意英文；自由度最高，但用户无法在生成前看见或修订模型新增的关键决定，协议错误也可能直接进入工作流。
3. 使用三档 Visual Composer：模型自由选择 TAG/NL 表达，同时把“用户锁定”和“模型补全”显式写入可编辑中文 `CONCEPT`，代码只保留确定性协议、角色、LoRA、重复与构图兜底。

**决定**：采用候选 3。

1. SiliconFlow 普通文本路径使用 `auto | faithful | free` 三档补全。`auto` 按语义覆盖度判断缺什么，`faithful` 只补成图必需项，`free` 在保留全部明确锁定项后自由完成画面；输入长短不替用户决定模式。
2. 文本模型必须严格返回 `CONCEPT + 精确 12 字段 IR + [LORA] + PROMPT`。有 active LoRA 时 `LORA` 行必需，否则不应出现。第一次协议错误只允许一次格式修复；仍失败则 502 fail closed，不把原始模型输出当 Prompt。
3. `CONCEPT` 固定为 `用户锁定：…｜模型补全：…`，作为生成前的中文控制面。用户编辑后通过 `concept_override` 重新编译，覆盖值保持原结构并作为权威蓝图；这仍是单轮重编译，不等于 Phase 4 PromptState 或字段级历史锁定。
4. `PROMPT` 可为 tag-only、短英文 clause、自然语言或自由混合，不设 tag 数、句数、词数或字符数目标。继续禁止机械复述整段语义，但允许关系句为了绑定主体、动作和构图而有意义地强化少量 tag。
5. SiliconFlow 普通文本不再让 ordinary `dict.yaml` 抢先删除词语或以全命中结果绕过 Composer。`char_dict` 仍提供角色 canonical tag；纯角色名且无 LoRA/构思覆盖时保留确定性快路；普通词典仍服务参考图、`google`/`none` 等 legacy 降级路径。
6. 代码不再对新文本 Composer 使用旧 `_prepare_painter_tags()` 的自动裸体、强制三分之四景别或风格删除。新路径只做可验证兜底：补主体计数、角色裸名去重、折叠完整 Prompt 的机械重复，并在用户明确要求全身/完整可见时删除 `mid-shot`、`medium shot`、`upper body`、`close-up`、`cropped`、`out of frame` 等互斥构图。
7. active LoRA 的 Profile/provides 继续在翻译前进入上下文，属于用户锁定；模型只能选择允许的 Profile/optional ID，代码仍确定性注入 exact trigger、文件名和强度。Composer 不重复改写 LoRA 已提供的身份、服装或画风。
8. 输入与输出上限按用途放宽：用户原文与 `concept_override` 各 4000 字符，客户端 `prompt_en` 6000，LoRA 编译后 Prompt 8000，对话 `delta` 2000；Reasoning Model `max_tokens=1800`。这些是防误用边界，不是鼓励 Prompt 越长越好。
9. 生产固定负面模板增加 `bad hands, missing fingers, extra fingers, fused fingers, extra arms, extra legs, bad feet, malformed feet`。它只提供低成本防御，不能被表述为已经解决人体或手脚质量。
10. 前端在原三栏骨架内增加补全模式和可编辑构思。原文、补全模式、LoRA 或构思变化都会让旧翻译进入 stale 状态；未把编辑后的构思重新应用前，不允许拿旧英文 Prompt 确认生成。直接生成仍会先翻译并把本次构思/Prompt 同步回检查区。

**修订关系**：本决定修订 D28 的生产 TAGS/NL 输出形态、D34 的文本协议、D36 的普通词典路由、D37 的约 20 元素上限与“冻结输入辅助”结论，以及 D44 中旧 `_prepare_painter_tags()` 对新文本路径的适用范围。D44 的 rating 控制权、D39 的 LoRA exact binding 边界和 D45 的固定三栏几何继续有效。

**代价与风险**：`auto/free` 的具体创意仍带随机性，稀疏输入不可能自动猜中用户未表达的唯一审美答案；更长 Prompt 也可能稀释重点。ordinary dict 不再缩短文本调用，SiliconFlow 普通文本的调用次数和 token 成本会上升。固定负面词与确定性护栏只能减少已知失败，不构成画质保证；复杂人体、多角色关系和 checkpoint 局限仍需真实图片与用户判断。

**验证**：`49 prompt unit tests passed`，`server/main.py` Python 编译通过；前端 2 段内联 JS、132 个 DOM ID、109 个静态引用检查通过，桌面与 390px 浏览器流程无 console error；3 条真实 SiliconFlow 烟测覆盖普通 auto、构思覆盖与角色+画风 LoRA 上下文。结构验证只证明协议和状态边界。画质证据仅为用户已确认的正常插画 `d709b7a58fc9.png` 与角色+画风 LoRA 插画 `695cf21fe007.png`，不从单测推导画质结论。

**相关文件**：`server/main.py`、`server/workflows/AnimaFull.json`、`web/index.html`、`.tools/test_prompt_unit.py`、`docs/api.md`、`docs/architecture.md`。

## D47. Visual Composer 定向护栏：画面容量与角色 LoRA 身份闭集

**背景**：Visual Composer 接入真实使用后出现两个可复现问题。其一，模型同时补出“上半身聚焦”“一手提裙摆”“另一手抚发”，生成图虽然像半身海报，却因手肘、裙摆和大腿被挤到边缘而呈现意外裁切感；改成 1024×1024 仍复现，证明根因不是像素不足。其二，用户只锁定 Remielle Dan 的 `black` Profile，Composer 却从形态名称推断 `long black hair / red eyes`，把服装形态错误扩张成角色身份属性。

**问题**：只加自然语言提醒不能保证模型遵守；直接写死角色真实发色又缺少 Registry 证据，并会阻止用户主动改色。全面语义冲突检测则会重新引入脆弱的大词表和过度规则化。

**决定**：

1. 在 Composer 的 renderability pass 中把画面视为有限预算。模型补全只允许一个主要手部交互；裙摆、髋部或大腿是核心交互时不得同时选择 close-up/upper-body，改用牛仔镜头或四分之三身，并让交互手、手肘与服装区域完整入镜。
2. 代码只检查少量跨 checkpoint 明确冲突：近景与完整下肢、近景与下半身交互、模型新增的多个手部/服装操作。命中后把具体原因交给第二次 Composer 调用重规划，不由代码替它选择审美主题。
3. 角色 LoRA 启用时，Profile ID/名称中的 `black/white/swim` 仅表示 Registry 形态，不是发色、瞳色或体型事实。用户没有明确要求时，Composer 必须从 IR.appearance 和 PROMPT 省略发色/瞳色，让 LoRA 权重提供身份外观。
4. 后端从用户原文及权威 `concept_override` 提取明确绑定的发色/瞳色白名单；`dict.yaml` 只作为 canonical 识别辅助，不重新取得普通文本路由权。输出同时扫描 IR.appearance 与最终 PROMPT，发现未授权颜色后进入一次语义修复，仍失败则 502 fail closed。用户明确写“黑色长发、蓝眼睛”等改色仍可通过。
5. LoRA Binding Compiler 删除已选 Profile 的兄弟 required trigger、精确身份复述及句首身份复述，但保留句中动作/场景语义；不根据开放式视觉描述猜测 Profile 或真实角色设定。

**原因**：构图容量和“Profile 名不是身份事实”都可以确定性判断；具体画面怎么重排仍属于 Reasoning Model。发色白名单只裁决用户是否授权改色，不需要代码知道角色原本是什么颜色，因此不会用未经验证的知识污染 Registry。

**代价与风险**：发色/瞳色识别只覆盖明确中英文绑定，不是通用角色一致性引擎；皮肤、体型、复杂妆容等仍主要依赖 LoRA context。用户通过 `concept_override` 保留的外观被视为权威修改。参考图 Vision 可能需要从图中提取外观，因此不套用文本 Composer 的闭集校验。

**验证**：Python 编译、Registry 12 assets 校验与 53 项 Prompt 单测通过。测试覆盖 `black form` 不等于 `black hair`、用户明确中英文改色、黑色发饰不误判，以及首轮越权后第二轮修复。真实 SiliconFlow 使用 Remielle Dan `black` + Fymrie 请求，最终 IR/PROMPT 均不含未请求的 `black hair/red eyes`，LoRA binding 无 warning；同为 1024×1024 的构图复测经用户确认没有问题。结构和单张图片仍不证明所有角色/姿态稳定。

**修订关系**：本决定补充 D39/D41 的 LoRA Context 边界，以窄的确定性身份属性检查取代“完全没有 semantic conflict detector”的旧首版状态；不引入通用冲突系统。它同时细化 D46 第 6、7 项，不恢复旧 Painter 的固定景别或属性词典主路。

**相关文件**：`server/main.py`、`.tools/test_prompt_unit.py`、`docs/architecture.md`、`docs/PLAN-LORA.md`。
