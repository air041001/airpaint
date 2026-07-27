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
- [ ] config.yaml 支持多工作流定义 (anima / noobai / 未来更多)
- [ ] 前端工作流下拉选择器
- [ ] 每个工作流独立的 sizes/quality_prefix/negative_prefix
- [ ] 后端按选择动态加载对应 workflow JSON

### 2.2 前端体验升级
- [ ] Tailwind CSS 重写界面
- [ ] 实时队列状态 (排队第N位 / 生成中 / 完成)
- [ ] 历史画廊 (localStorage 缓存最近12张缩略图)
- [ ] 移动端适配
- [ ] 一键复制提示词 / 下载原图

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
- [ ] 图片上传 → 图生图

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
