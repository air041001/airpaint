# AirPaint

AirPaint 是面向 ComfyUI 用户的 Prompt / Intent / Knowledge Intelligence Layer。它把中文画面意图、参考范围、LoRA 选择和生成参数编译成当前 Anima 工作流可执行的请求，同时保留可检查、可编辑的 Concept、Prompt、LoRA Binding 与 seed。

AirPaint 不替代 ComfyUI，也不是局部修图工具。当前版本进入封版维护：继续保留已经可用的 Prompt、参考图、整图重绘和历史分支能力，主动功能开发停止。

![AirPaint 沉浸展台界面](docs/assets/airpaint-workshop.png)

> 2026-09-09 封版界面的历史实拍；第 100 次提交又移除了顶部位置文字，因此图片不代表最终逐字界面。中央图片是本项目已有生成结果；为在 GPU 与外部 API 不可用时仍可复核主体布局，任务和历史状态由本地演示夹具提供，不冒充本次实时生成。完整演示路径与证据边界见 [演示记录](docs/demo.md)。

## 使用流程

1. 用中文写清主体、画面重点和必须保留的条件，按需要选择补全程度、尺寸与 LoRA。
2. 先检查并编辑 Concept 和最终英文 Prompt，再把确认后的请求交给当前 Anima 工作流。
3. 在结果页保留真实 seed、尺寸、Prompt 与 LoRA Binding；刷新或重新登录后从服务端历史继续。
4. 想重新抽取构图时选“换一版”；允许整张图重新采样时选“基于此图重绘”，不要把它当作局部修图。

## 能做什么

- 用 `auto / faithful / free` 三档把中文构思编译成 Anima Prompt。
- 在生成前检查和编辑中文 Concept、十二字段 IR 与最终英文 Prompt。
- 选择角色/风格 LoRA Profile，由后端按 Registry 确定性绑定真实文件和 trigger。
- 对提供多个作者配方的特殊 LoRA，独立选择用法模板；未选择时不自动套用，选定后可检查实际正向、负面、姿势候选与尺寸建议。
- 用参考图借构图、构图＋氛围或完整可见信息。
- 用 Img2Img 对整张图片重新采样，选择 0.35/0.55/0.75 重绘强度和保留/裁切适配。
- 从历史图片换一版或建立重绘分支，并查看真实 seed、尺寸、Prompt、LoRA 与父任务。
- 在后端重启、浏览器刷新或短暂断线后恢复排队、核对已提交任务及重新取回已有结果。

## 项目贡献

AirPaint 的工作不在于替代底层生成器，而是把 ComfyUI 上方几个容易失真的环节连接成一条可检查、可恢复的链：

- 用 Concept、十二字段 IR、TAG/NL 分工和知识解析，把中文意图编译成 Anima 可执行 Prompt，同时保留人工编辑权。
- 将 LoRA Profile 的语义选择与真实文件、trigger、强度和单 Loader 注入分开，避免让语言模型猜工作流资源。
- 把参考观察、Prompt 编译、ComfyUI 提交、seed、历史分支和故障核对保存为同一个可追溯任务，而不是只留下最终图片。
- 用 SQLite、幂等请求、预分配 ComfyUI prompt ID、受控图片访问和一致性备份，让个人单机生成在刷新、断线和重启后仍能解释发生了什么。

两张由当前项目真实生成、并从 PNG 内嵌 workflow 核对参数的封版案例见 [真实案例与参数](docs/showcase.md)。其中也保留了没有完全实现参考范围的失败事实，案例不是画质宣传样板。

## 快速启动

当前保存环境为 Windows、Python 3.10.10、ComfyUI `127.0.0.1:8188`。

1. 安装 [requirements.txt](requirements.txt) 中固定的 Python 依赖。
2. 复制 `server/config.example.yaml` 为 `server/config.yaml`，填写本机路径、邀请码和 API key。该文件已被 Git 忽略。
3. 启动 ComfyUI。
4. 双击 `.tools/start_airpaint.bat`。只在本机使用时运行 `powershell -File .tools/start_airpaint.ps1 -NoTunnel`。
5. 打开 `http://127.0.0.1:8000` 或已配置的 `https://airpaint.xyz`。
6. 结束时双击 `.tools/stop_airpaint.bat`。它先请求后端正常关闭并写回数据库，超时才强制终止；不会关闭 ComfyUI。

缺少配置、workflow、LoRA Registry、ComfyUI 或 cloudflared 时，启动脚本会指出具体缺项。完整启动、停止、状态解释和备份恢复见 [运维说明](docs/operations.md)。

## 配置边界

```yaml
comfy_url: http://127.0.0.1:8188
comfy_dir: "E:/ComfyUI_windows_portable/ComfyUI"
host: 127.0.0.1
port: 8000
allow_origins: ["https://airpaint.xyz", "http://127.0.0.1:8000"]
tokens: ["friend-xxxx"]
daily_limit: 90
translate: siliconflow
siliconflow_model: "deepseek-ai/DeepSeek-V4-Flash"
siliconflow_vision_model: "Qwen/Qwen3-VL-8B-Instruct"
```

原始邀请码和 API key 只应出现在 `server/config.yaml`。业务数据库只保存由本机身份密钥生成的匿名 owner ID，请求快照会剔除鉴权字段。

人工维护的 LoRA 真相源是 `server/lora_registry.yaml`。新增资产继续使用 `.tools/start_lora_onboard_agent.bat`；服务启动不会扫描并自动收录全部 LoRA。

## 数据与恢复

- `server/state/airpaint.db` 是任务、会话、历史和每日用量的权威来源。
- `server/images/` 保存输出图，`server/state/source_images/` 保存迭代所需输入图。
- 浏览器只保存主题和短暂请求恢复标识，不再保存业务历史或原始邀请码。
- 生成图通过带归属校验的 `/api/images/{filename}` 读取，不存在公开 `/images` 目录旁路。
- 人工删除输出文件后，服务端会在下次启动或历史查询时把对应作品及参数从界面隐藏；网络断开或登录失效不会被当成删除，已使用的生成次数不返还。
- 每个邀请码每天默认可创建 90 个生成任务。任务一旦与配额在同一事务中创建就计一次；参数校验失败不计；失败任务不退次数；刷新、同请求重试、状态核对和重新下载不重复计数。

在线一致性备份：

```text
python -m server.maintenance backup
python -m server.maintenance verify server/backups/<备份文件>.zip
```

恢复必须先停止 AirPaint：

```text
python -m server.maintenance restore server/backups/<备份文件>.zip --replace
```

备份同时包含 SQLite、身份密钥以及数据库实际引用的输入/输出图片；恢复覆盖前自动再做一份回滚备份。

## 验证

```text
python -m compileall -q server
node .tools/check_frontend.js
python .tools/test_prompt_unit.py
python .tools/test_image_iteration.py
python .tools/test_persistence_recovery.py
python .tools/test_lora_composition.py
python .tools/test_lora_onboard_agent.py
python .tools/register_lora.py --validate
python .tools/inspect_wf.py
```

这些检查证明协议、持久化、恢复、Binding 和 workflow 注入没有已知结构退化，不能证明每张图片的审美或 Img2Img 改动都会成功。

## 已知限制

- Img2Img 是整图重绘。低强度可能几乎不改，高强度可能连带改变人物、发型、服装和构图；它不保证“只改指定区域”。
- D59 的参考图/Img2Img 五张定向样本已完成工程核对，但图片人眼验收没有全部通过，不能写成画质完成。
- 双角色有确定性数量和 Prompt 语法护栏，复杂互动与属性归属仍受 Anima、LoRA 和 seed 影响；三角色只 best-effort。
- 单 worker、单机 SQLite、HTTP 轮询；不提供分布式调度、WebSocket、数据库集群或多 GPU 并发。
- 旧浏览器历史没有可验证的归属和参数，不能伪装成完整可恢复任务；原文件仍可作为普通旧图片保留。
- 当前单页界面的 Tailwind 与 GSAP 仍从外部 CDN 加载，字体使用本机系统字体；断网时业务数据不丢失，但样式或动效可能降级。离线查看项目可使用仓库内截图与演示记录。

## 文档入口

- [BUILDHANDOFF](docs/BUILDHANDOFF.md)：当前能力、证据、边界和接手路线。
- [架构](docs/architecture.md) / [API](docs/api.md) / [运维](docs/operations.md) / [演示记录](docs/demo.md) / [真实案例](docs/showcase.md)。
- [设计决定](docs/decisions.md) / [开发日志](docs/DEVLOG.md) / [Roadmap](ROADMAP.md)。
- [AGENTS.md](AGENTS.md)：仓库开发规约。

## 维护状态

`v1.0.0`（`9af5604`）保持为历史封版基线，旧 tag 不变。2026-09-09 按用户授权完成沉浸展台前端收尾；此后用户解除封版并授权有限实验线：最终正负文本控制、LoRA 用法资料入库、中文编译消费用法资料、用法资料接入 onboarding。FComic 真实试用暴露模板未执行后，又完成了独立模板选择与确定性正负骨架修复；2026-09-14 已完成一次真实生成有限验收，图片达到用户可接受下限，另一次 `Failed to fetch` 只证明仍有未定位的传输稳定性风险。统计稳定性与其他模板画质未验证。项目现再次封板，新模型、RAG、微调、多 Agent、自动改图、新 workflow、微服务和前端框架迁移仍不在范围内。


## LoRA 用法登记（给使用者）

AirPaint 可以在登记 LoRA 时同时保存“仅适用于当前模型版本”的用法说明，之后生成时会自动带上，不需要手工复制 ID 或执行 SQL。

1. 双击 `.tools\start_lora_onboard_agent.bat` 启动向导。
2. 向导先问 `要做什么 (new/attach/cancel)`：
   - `new` = 新注册；`attach` = 为已注册资产补用法（会列出资产按可读名称选择，不重跑候选/预览）；
   - `cancel` 直接退出。
3. 补用法时会问是否添加用法说明：
   - 选 `n` 可整段跳过（不写资料库）；
   - 选 `y` 后选择录入方式 `paste`（粘贴，单独一行 `::end` 结束）或 `file`（输入 UTF-8 文件路径）；
   - 无论哪种方式，都会再问来源（author / community / user）、可选来源地址与作用域
     （留空 = 该资产共享；或填该资产已存在的 Profile ID）。
4. 向导会在最终确认前完整列出待关联资料与目标资料库，确认后自动写入不可变记录并把引用合并进资产。
5. 确认写入后，刷新浏览器即可在生成时选择该 LoRA。若资料登记了多个结构化用法模板，它们会在 LoRA 叠加栈中单独显示；默认“不套模板”，由用户明确点选后才执行。

命令行等价入口：

```text
python .tools/register_lora.py --agent                 # 向导（新注册或补用法）
python .tools/register_lora.py --attach-usage <ASSET>  # 只为已注册资产补用法（不重跑候选/预览）
python .tools/register_lora.py --usage --asset-key <KEY> --body-file <PATH> --db <PATH>
```

`--usage` 是低层入口，**必须显式 `--db`**；向导与 `--attach-usage` 缺省使用项目 `state`（可用 `--db` 覆盖）。

注意：登记原文只表示“保存了这份资料”，不等于资料有效，也不等于已实图验证；验证状态默认 `unverified`。
