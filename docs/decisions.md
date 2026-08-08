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

**背景**: 原前端 783 行单文件, 深色大留白里挤一个居中窄卡片, 后端三工作流/词典/参考图/对话迭代/结构化分解全藏在下拉和按钮后, 朋友感知不到工作量; 「单张生成」「对话迭代」是两个平级 tab, 但二者产品形态本质不同(单张=无状态设置先行; 对话=以上一张图为基准、有血缘)。用户要做简历/毕设, 纠结要不要上 React/Vue + Vite 构建。

**决定**:
1. **三屏结构(登录/工坊/暗房)**: 登录后全功能工坊(三栏: 画布 + AI理解面板 + 参数 inspector); 对话迭代不作平级 tab, 改为结果图上「继续迭代」进入暗房。理由: 用户调研——迭代念头是「看到这张图好看才有了别的点子」, 非用之前就有; 从图进入比重进一个 tab 顺。暗房大图在上、改动控件紧贴图下全宽、右侧竖向血缘演化条。
2. **AI 理解常驻结果旁**: breakdown(scene/composition/mood/lighting/style)不再藏在「先看翻译」后, 直接长在结果右侧, 露出「AI 真理解中文」核心卖点。翻译/编辑保持两段式: 输入栏「生成」静默翻译直接提交; 「先看翻译」填右侧面板, 面板内确认/再来一版/取消(在哪编辑在哪确认)。
3. **技术方式选 A(单 HTML + Tailwind CDN), 不上 React/Vue/Vite**: 前端是薄客户端, 后端三层 Prompt/Workflow/Intent Engine 才是简历/毕设主角; 引入构建链会多出用户看不懂、答辩答不上的一层。零构建最贴「自己用+少量朋友」定位, 单文件内部分屏/功能分节保可维护。代价: Tailwind CDN 运行时编译、需联网, 若朋友网络打不开 cdn.tailwindcss.com 再换本地构建。
4. **历史画廊(localStorage 12 张)**: 画布下方横滑条, 点旧图只放大/下载。**继续迭代从原图直接进入**: 后端 `start-image` action 从已完成的 job 创建 session(原图当第一轮, 不重新生成), 前端 `toDarkroom` 调 `start-image` 直接进暗房显示原图 + 迭代控件。降级: 无 currentJobId 时回退 prompt 起步。
5. **保留隐藏原生 select**: 工作流/尺寸从下拉改卡片/宫格, 但提交逻辑靠 `$('workflow').value` 等取 id 值; 放三个 `hidden` select 由卡片同步 value, 提交逻辑零改动。

**收益**: 工作量外显(多工作流卡片/AI 理解/血缘); 单张与对话形态区分; 移动端竖排; 后端零改动。
**代价**: Tailwind CDN 依赖(可接受); 暗房首图非原图基准(后端限制); 单文件变长(分节可控)。

## D28. system prompt 信息分流重写 + quality_prefix 官方化

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

## 待决策 / 方向
- **Intent Engine**: 迈向「理解用户意图」核心目标。**构图/场景/情绪的结构化分解已实现** (D18: LLM 输出 scene/composition/mood/lighting/style + TAGS); **否定语义解析弃用** (D18: Anima 负面是常量, 不随输入变); 仍待做: 歧义消解、LoRA/工作流自动推荐。符合 CLAUDE.md 规则 6。
