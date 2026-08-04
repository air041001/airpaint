# ComfyUI Web 项目路线图

> 以"能写进简历"为目标的功能规划。MVP 已完成，以下是第二阶段。

## 当前状态: MVP 已上线

- [x] FastAPI 后端网关 (鉴权/限流/队列/翻译/内容过滤)
- [x] 单文件 SPA 前端 (后端静态托管于 airpaint.xyz, 已弃 GitHub Pages)
- [x] cloudflared 命名隧道 (固定域名 airpaint.xyz, 已弃临时隧道)
- [x] 中文→danbooru tag 翻译 (词典 + SiliconFlow Qwen3-8B)
- [x] ComfyUI API 对接 (sanitize_for_api + 统一 seed)
- [x] 851 条中文↔danbooru 词典
- [x] 单工作流 (AnimaStandard V7) 端到端验证
- [x] 一键启动脚本 start_airpaint.bat (后端 + 隧道)
- [x] 意图扩写 (氛围->场景) + 角色词典 (char_dict.yaml)

---

## Phase 2: 多工作流 + 体验升级

### 2.1 多工作流热插拔
- [x] config.yaml 支持多工作流定义 (anima 快速 / anima-detailer 精修) - 2026-07-30
- [x] 前端工作流下拉选择器 (既有, 现两个选项)
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

### 3.2 高级工作流
- [ ] 局部重绘 (inpainting) 工作流
- [ ] ControlNet 姿态/线稿控制
- [x] LoRA 选择器 (前端可选风格 LoRA) — 2026-07-26, 见 DEVLOG 第13条 / D16
- [x] 图片上传 → 图生图 - 2026-08-02, 见 DEVLOG 第23条 / D26

### 3.3 基础设施
- [ ] WebSocket 替代轮询
- [ ] Docker + docker-compose
- [x] cloudflared named tunnel (固定域名 airpaint.xyz)
- [ ] pytest 单元测试
- [x] 根目录纳入 git (air041001/airpaint 私有仓库; server/docs/.tools 已版本管理, config.yaml 等密钥已 gitignore)

---

## Phase 4: 简历包装

- [ ] README.md (架构图 + 截图 + 快速开始)
- [ ] 30 秒 demo 视频
- [ ] 项目描述文案 (STAR 法则)

---

## Phase 5: 理解用户意图 (inspiration 调研方向)

> 2026-07-29 联网调研 Anima 生态同类项目后梳理的新方向 (见 `docs/inspiration.md`), 比"工程化晚期镀金"(多工作流/Docker/WebSocket) 更贴核心目标。按 价值/功夫 排序。

- [x] ① 抽卡 re-roll (同一句中文高温重出不同分解, 「🎲 再来一版」) - 2026-07-29, 见 DEVLOG 第16条 / D19
- [x] ⑥ 规范化 Anima tag 顺序 (count -> character -> general) - 2026-07-29, 见 DEVLOG 第16条 / D20
- [x] 质量提升: 启用 Face+Hand detailer 精修工作流 (anima-detailer, ~90s) - 2026-07-30, 见 DEVLOG 第19条 / D22
- [ ] ② 标签选择器 UI (发型/瞳色/表情/姿势 chip, 降门槛)
- [x] ③ 参考图理解 (Qwen3-VL 提氛围/配色/构图转 tag, 走 txt2img 非图生图) - 2026-07-31, 见 DEVLOG 第20条 / D23
- [ ] ④ 多角色 + 防属性串色 (LLM 改写有主语英文 NL)
- [~] ⑤ 多轮对话精修 (骨架+A/D+B 已做: 会话线程+换一版+微调img2img, 2026-08-02, D25/D26; 剩保姿势 ControlNet)
