# ComfyUI Web 项目路线图

> 以「把产品做好用」为目标的功能规划。MVP 已完成，以下是当前规划。

## 当前主线: Prompt Intelligence (PLAN-v5)

> 2026-08-13 确立的新主线。完整路线见 [`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`](PLAN-v5%20—%20AirPaint%20Prompt%20Intelligence.md)（取代 PLAN-v4）。
>
> **核心**：NSFW-first + Prompt-first，LoRA 后置。首要目标是把成人虚构内容的 Prompt 与出图质量做到最好，普通绘图能力顺带做强；LLM 是大脑（意图/语义），代码是脊髓（canonical tag/知识库/数值/workflow）。IR 不规定固定最终 Prompt 格式，渲染策略必须用固定变量的人眼实验验证。
>
> 下面 Phase 2-5 是 MVP 之后的工程化补强（多工作流/前端/持久化/高级工作流），与 Prompt Intelligence 主线并行但优先级低。

### Phase 0: Prompt Baseline（已完成 2026-08-14）
- [x] 建 Evaluation Set 30 条（`.tools/eval_set/`，第一层结构回归 + 第二层生图验收）
- [x] 基础维护顺手修：scan_loras 三处问题 / 文档模型名同步 DeepSeek-V4-Flash / 已知 char_dict 错误（amamiya_kokoro 已修）

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

### Phase 2-8: 见 PLAN-v5
Prompt Quality → Character Knowledge → PromptState → LoRA Context → Trigger Engine → LoRA Composition → Workflow Intelligence

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

## Phase 5: 理解用户意图 (inspiration 调研方向)

> 2026-07-29 联网调研 Anima 生态同类项目后梳理的新方向 (见 `docs/inspiration.md`), 比"工程化晚期镀金"(多工作流/Docker/WebSocket) 更贴核心目标。按 价值/功夫 排序。

- [x] ① 抽卡 re-roll (同一句中文高温重出不同分解, 「🎲 再来一版」) - 2026-07-29, 见 DEVLOG 第16条 / D19
- [x] ⑥ 规范化 Anima tag 顺序 (count -> character -> general) - 2026-07-29, 见 DEVLOG 第16条 / D20
- [x] 质量提升: 启用 Face+Hand detailer 精修工作流 (anima-detailer, ~90s) - 2026-07-30, 见 DEVLOG 第19条 / D22
- [x] ③ 参考图理解 (Qwen3-VL 提氛围/配色/构图转 tag, 走 txt2img 非图生图) - 2026-07-31, 见 DEVLOG 第20条 / D23
- ~~② 标签选择器 UI~~ -- 已评估不做 (Prompt Intelligence 主线用 LLM 意图解析替代)
- ~~④ 多角色 + 防属性串色~~ -- 已评估不做 (NL 空间布局已部分覆盖, 精确分区需区域提示词=不做)
- [~] ⑤ 多轮对话精修 (骨架+A/D+B 已做: 会话线程+换一版+微调img2img, 2026-08-02, D25/D26; 剩保姿势 ControlNet 已评估不做)
