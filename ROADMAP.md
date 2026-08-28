# ComfyUI Web 项目路线图

> 以「把产品做好用」为目标的功能规划。MVP 已完成，以下是当前规划。

## 当前主线: Prompt Intelligence (PLAN-v5)

> 2026-08-13 确立的新主线。完整路线见 [`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`](PLAN-v5%20—%20AirPaint%20Prompt%20Intelligence.md)（取代已删除的 PLAN-v4）。
>
> **核心**：Anima-first + Prompt-first。当前优先把普通二次元人物插画的构思与 Prompt 编译做好，NSFW 复用同一表达基础而不以年龄词、rating 或裸体 tag 作为默认驱动；LLM 是大脑（意图/语义），代码是脊髓（canonical tag/知识库/数值/workflow）。IR 不规定固定最终 Prompt 格式，画质结论必须来自实际图片与人眼判断。
>
> 下面 Phase 2-5 是 MVP 之后的工程化补强（多工作流/前端/持久化/高级工作流），与 Prompt Intelligence 主线并行但优先级低。

### Phase 0: Prompt Baseline（已完成 2026-08-14，2026-08-19 清理）
- [x] 建 Evaluation Set 30 条（`.tools/eval_set/`，第一层结构回归 + 第二层生图验收）
- [x] 基础维护顺手修：scan_loras 三处问题 / 文档模型名同步 DeepSeek-V4-Flash / 已知 char_dict 错误（amamiya_kokoro 已修）
- [x] 2026-08-19：删除未验证的 `baseline.yaml`/`cases.yaml`/`run_baseline.py`/`compare.py`，结构回归改由单测覆盖，质量只由人眼确认

### Phase 1: Prompt IR + Compiler（代码实现完成 2026-08-15，视觉策略待实验）
- [x] 定义 Prompt IR（12 字段 JSON：subject/appearance/clothing/action/pose/interaction/scene/composition/lighting/mood/style/constraints）
- [x] 改 LLM 输出协议：IR 单行 JSON + TAGS/NL；保留旧协议降级；DeepSeek 两轮 30/30 IR 完整
- [x] Prompt Compiler 初版：`compile_prompt` 统一去重、角色裸名清理、排序和 NL 拼接；`build_prompt` 保留工作流适配边界
- [x] Evaluation Set 真实 `translate()` 链路回归通过，新增固定 Prompt+seed 图像夹具 002/012/018
- [ ] 人眼视觉验收：018 复检不认可“对峙感”，原 3/3 代理验收不作为质量结论，转入 Phase 1.5

### Phase 1.5: Rendering Strategy 实验（首轮与 R4 专项已完成 2026-08-15）
- [x] 固定 base Anima / workflow / sampler / CFG / steps / size / seed / negative，只改变 Prompt 渲染方式
- [x] 7 个 case × 4 variant：TAG-only / TAG+short NL / weighted spatial NL / NL-dominant
- [x] 加入成人 NSFW 单人与双人 case；R2/R4 额外测试 semantic negative
- [x] 用户人眼盲评，vision agent 只做粗筛；结果记录在 D35 与 `render_exp/results.yaml`
- [x] R4 双人对峙换 seed 专项复测；base Anima 仍不能稳定表达后撤/连续构图，不固化 Compiler 2.0 或 weighted rendering，保留 Prompt 手动编辑 fallback

### Phase 2: Prompt Quality（首轮实验完成 2026-08-15）
- [x] Failure taxonomy 第一批回标（35 张实验图）
- [x] 8 条成人 NSFW 结构集、6 条固定视觉基线
- [x] W3 profile A/B、W4 Dictionary vs LLM、W6 girl/female 对照
- [x] 收窄 `tag_first`：只覆盖明确成人 NSFW 单主体简单场景；普通 SFW 保留 NL
- [x] 补充 NSFW 人眼基线与 profile 结果；6 条固定视觉基线均通过人工检查
- [x] 不把 weighted NL / semantic negative / girl-female 替换纳入默认策略

### Phase 2.5/2.6: Prompt Expansion（已完成 2026-08-16）
- [x] 7 个短输入覆盖角色细节、服装细节、场景锚定、光影、构图镜头、NSFW 张力、纯氛围扩写
- [x] 完成 A1/A2/A3 三路、21 张固定 Prompt/seed/尺寸/negative 图像和第一轮盲评
- [x] 统一 SFW/NSFW 补全底层协议：构图、光影、氛围、材质；NSFW 在服装状态/身体语言/揭示节奏上分流
- [x] E1/E6/E7 关键平局换 seed 补测（9 张）并完成第二轮 A/B/C 人眼评审
- [x] 应用第二轮结果判定矩阵：A3 对 A2 为 4 胜、1 平、2 负，进入生产改造
- [x] 生产改造后 5 case 新旧 Prompt A/B：3 胜 2 平 0 负
- [x] 保留生产提示词增强、`prompt_ir_meta` 和 reroll 新补全语义
- [x] 记录产品边界：自动扩写能改善稀疏输入，但无法替代用户提供具体视觉意图

Phase 2.6 收尾后不开新扩写子阶段；下一步由真实使用反馈决定，不立即建设详细输入辅助功能。

### Phase 2.7: Visual Composer（已完成 2026-08-27）
- [x] 用 `auto | faithful | free` 显式控制补全程度，不再根据输入长度猜模式
- [x] 文本路径改为严格 `CONCEPT + 12 字段 IR + [LORA] + PROMPT`；失败修复一次后 fail closed
- [x] 增加可编辑中文 `用户锁定｜模型补全` 构思，修改后重新编译并防止旧 Prompt 串线
- [x] SiliconFlow 普通文本绕开 ordinary `dict.yaml` 的全命中短路，角色 canonical knowledge 与 LoRA exact binding 继续由代码掌管
- [x] 最终 Prompt 允许 tag、短句、自然语言或混合，不设固定元素数和 TAG/NL 形态；代码仅保留重复、主体、角色、明确画面容量冲突及角色 LoRA 发色/瞳色越权等确定性护栏
- [x] 放宽分用途输入上限，固定 negative 增加常见手指/四肢/脚部失败词；不把负面词描述为人体质量保证
- [x] 53 项确定性单测、真实 SiliconFlow 普通/构思覆盖/角色+画风 LoRA smoke、桌面与 390px 前端流程通过；正常插画、角色+画风及同尺寸构图修复图片已由用户确认，Remielle `black` 不再被 Profile 名误补为黑发

Phase 2.7 是根据真实样图与定向验收完成的生产修订，不重启大批量 A/B。后续先积累真实使用中的失败样本，再决定是否做局部规则或新阶段。

### Phase 3: Character Knowledge（精简版，已完成 2026-08-18）
- [x] Danbooru 主 API 连通性验证，确认 exact tag/category/post_count 可用
- [x] `CHAR` 候选协议、post_count 分类和独立 auto cache 基础实现
- [x] 未知角色运行时联调：likely_supported 自动缓存，weak/absent/unavailable 不污染正式词典
- [x] 长门有希/御坂美琴查询：分别得到 `nagato_yuki`/`misaka_mikoto`，post_count 9254/10778
- [x] 30 条 SFW / 8 条 NSFW 结构与当时的 safety smoke（自动 rating 已由 D44 取消）
- [x] 长门固定 Prompt 图片人眼确认，Phase 3 ready to push
- [x] D38/DEVLOG/PLAN-v5/BUILDHANDOFF 定稿并 push

Phase 3 不做结构化 char_dict 迁移、156 条全量审计或复杂审批后台；正式 `char_dict.yaml` 继续支持用户手动加一行即时生效。

### Phase 4: PromptState + Incremental Editing（延后，使用数据触发）
- [ ] 观察暗房 redo/tweak 使用频率和 D31 类字符串累加问题
- [ ] 只有真实迭代痛点成立后才启动 PromptState 设计

### Phase 5-7: LoRA Context / Binding / Composition（工程链已完成 2026-08-28）
- [x] `docs/PLAN-LORA.md`：LoRA-aware Painter + versioned Registry/Profile + 确定性 Binding Compiler
- [x] translate/jobs/dialog 共用 binding snapshot/revision；前端支持 Profile auto/锁定和 stale Prompt 防串线
- [x] 41 个确定性单测、前端 smoke 与 5 组真实 A/B：aware 1 胜 4 平 0 负
- [x] 本地入库 Agent；Remielle Dan / Dolphro-kun / Light 三项新增资产生效并完成人工验收
- [x] Composition 工程：角色最多 3 个语义 Profile、风格/细节不限；同 Asset 多 Profile 需 Registry opt-in，物理文件只加载一次，逐 Asset 强度 0~2
- [ ] 用固定 Prompt/seed 的真实多人图验收空间关系、动作/属性绑定；不从结构支持推导画质完成

LoRA 组合管线在上述边界内关闭；后续质量问题进入 Prompt Intelligence/真实出图评测，Phase 8 Workflow Intelligence 不纳入本工程。

### 网站 MVP 细节优化（第二批已完成 2026-08-24）
- [x] 尺寸改为点击展开、分组选择、选中即收起；四个标准档 + 两个高分辨率实验档，删除 `1216x832`
- [x] 修复 txt2img 误走 832×1216 占位图 VAE 分支；RTX 4060 Laptop 8GB 实测 `1024x1536` 真正输出，约 84 秒，前端显示实际成品像素
- [x] 第二批状态连续工作台：描述跨栏、Prompt 左、图片中、参数右、历史下方；首次不显示空画布，有图后重翻译不移走图片
- [x] 纸本画室/石墨暗房双主题；纸本用墨绿统一品牌/选中/主操作，夜间保留安灯橙
- [x] 移动端 Prompt/参数页签与暗房图片→控制→脉络顺序；过渡只使用 opacity/translate
- [ ] 后续按真实使用继续处理空/错状态、历史作品细节和可访问性，不提前增加新后端功能

---

## 当前状态: MVP 已上线

- [x] FastAPI 后端网关 (鉴权/限流/队列/翻译/内容过滤)
- [x] 单文件 SPA 前端 (后端静态托管于 airpaint.xyz, 已弃 GitHub Pages)
- [x] cloudflared 命名隧道 (固定域名 airpaint.xyz, 已弃临时隧道)
- [x] 中文→danbooru tag 翻译 (词典 + SiliconFlow DeepSeek-V4-Flash)
- [x] ComfyUI API 对接 (sanitize_for_api + 统一 seed)
- [x] 851 条中文↔danbooru 词典
- [x] 单工作流 (AnimaStandard V7) 端到端验证
- [x] 一键启动脚本 start_airpaint.bat (后端 + 隧道)
- [x] 意图扩写 (氛围->场景) + 角色词典 (char_dict.yaml)

---

## Phase 2: 多工作流 + 体验升级

### 2.1 多工作流热插拔
- [x] 工作流合并: txt2img/img2img/精修 一份 AnimaFull.json + 后端删节点拼接 (D32) - 2026-08-11
- [x] 前端工作流选择器 (既有; 工作流合并后仅一个选项: anima, D32)
- [ ] 每个工作流独立的 sizes/quality_prefix/negative_prefix (目前共享同值, 未做独立)
- [x] 后端按选择动态加载对应 workflow JSON (build_prompt 按 wf_name 读 file)

### 2.2 前端体验升级
- [x] Tailwind CSS 重写界面 (工坊/暗房三屏, 2026-08-03, 见 DEVLOG 第24条 / D27)
- [x] 实时队列状态 (排队第N位 / 生成中 / 完成)
- [x] 历史画廊 (localStorage 缓存最近12张缩略图)
- [x] 移动端适配
- [x] 下载原图 (一键复制提示词: 英文 prompt 可编辑, 手动框选复制; 未做专门按钮)

### 2.3 一键启动
- [x] start_airpaint.bat: 一键拉起 FastAPI + cloudflared (ComfyUI 仍由用户 bat 单独启)
- [x] 启动前健康检查 (ComfyUI 是否运行)
- [x] start_tunnel.bat: 隧道单独挂了时补起, 不碰后端
- [ ] 进一步: 连 ComfyUI 一起拉起 (可选)

---

## Phase 3: 工程化 + 高级工作流

### 3.1 持久化
- [ ] SQLite 用量统计 (替代内存计数)
- [ ] 生成历史记录 (可查询/回溯)
- [ ] 邀请码管理 (创建/禁用/用量查看)

### 3.2 高级工作流 (已评估: 不做)
> 2026-08-12 评估后决定**不做**这些高级工作流 (理由: 它们即使在 ComfyUI 里也不便捷, 无"自由度"可还原; 做成对普通用户便捷需要不成比例的 UX 工作量, 用户群撑不起。详见 D33 讨论)。后续 agent 不要再当规划做。
- [x] LoRA 选择器 - 2026-07-26, D16
- [x] 图片上传 图生图 - 2026-08-02, D26
- [x] 局部重绘 inpainting -- 试做后撤销 (效果不达标: mask 精确度 UX 难, 改属性保脸不可靠)
- ~~ControlNet LLLite~~ -- 已评估不做 (需前端画姿态+配模型, UX 重)
- ~~区域提示词 Regional Prompting~~ -- 已评估不做 (3 张 mask + 3 提示词, 对普通用户是灾难)
- ~~Ultimate SD Upscale~~ -- 已评估不做 (唯一可便宜加的后处理, 但优先级低)
- ~~SAM 3.1 万物 Detailer~~ -- 已评估不做 (大模型, 非核心)
- ~~ControlNet OpenPose~~ -- 已评估不做 (同 ControlNet, UX 重)

### 3.3 基础设施
- [ ] WebSocket 替代轮询
- [ ] Docker + docker-compose
- [x] cloudflared named tunnel (固定域名 airpaint.xyz)
- [ ] pytest 单元测试
- [x] 根目录纳入 git (air041001/airpaint 私有仓库; server/docs/.tools 已版本管理, config.yaml 等密钥已 gitignore)

---

## Phase 4: 对外展示

- [ ] README.md (架构图 + 截图 + 快速开始)
- [ ] 30 秒 demo 视频

---

## Phase 5: 理解用户意图（历史调研方向，已基本落地）

> 2026-07-29 联网调研 Anima 生态同类项目后梳理的新方向，比"工程化晚期镀金"(多工作流/Docker/WebSocket) 更贴核心目标。按 价值/功夫 排序。原调研文档 `docs/inspiration.md` 已删除，以下为落地记录。

- [x] ① 抽卡 re-roll (同一句中文高温重出不同分解, 「🎲 再来一版」) - 2026-07-29, 见 DEVLOG 第16条 / D19
- [x] ⑥ 规范化 Anima tag 顺序 (count -> character -> general) - 2026-07-29, 见 DEVLOG 第16条 / D20
- [x] 质量提升: 启用 Face+Hand detailer 精修工作流 (anima-detailer, ~90s) - 2026-07-30, 见 DEVLOG 第19条 / D22
- [x] ③ 参考图理解 (Qwen3-VL 提氛围/配色/构图转 tag, 走 txt2img 非图生图) - 2026-07-31, 见 DEVLOG 第20条 / D23
- ~~② 标签选择器 UI~~ -- 已评估不做 (Prompt Intelligence 主线用 LLM 意图解析替代)
- ~~④ 多角色 + 防属性串色~~ -- 已评估不做 (NL 空间布局已部分覆盖, 精确分区需区域提示词=不做)
- [~] ⑤ 多轮对话精修 (骨架+A/D+B 已做: 会话线程+换一版+微调img2img, 2026-08-02, D25/D26; 剩保姿势 ControlNet 已评估不做)
