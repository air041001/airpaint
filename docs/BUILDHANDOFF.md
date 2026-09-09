# AirPaint Build Handoff

> 更新：2026-09-09
> 用途：新 Agent 只读本文件即可了解当前产品、验证状态、边界和接手路线。开发规约仍以根目录 `AGENTS.md` 为准；代码和本地配置优先于本文。

## 一句话定位

AirPaint 是 ComfyUI 上层的 Prompt / Intent / Knowledge Intelligence Layer。它把中文意图、参考范围、角色知识、LoRA 选择和成像参数编译成当前 Anima workflow 可执行的请求，同时保留 Concept、Prompt、LoRA Binding、尺寸与 seed 的可见控制权。

它不替代 ComfyUI，也不是局部修图器。当前进入最终封版：停止主动功能开发，只处理阻止使用的数据丢失、兼容或安全问题。

## 当前能力

### Prompt / Knowledge / LoRA

- Reasoning Model 当前为 DeepSeek-V4-Flash，Vision Model 为 Qwen3-VL；代码抽象不把具体型号设为永久架构。
- Visual Composer 提供 `auto / faithful / free`，协议为 `CONCEPT + 12字段 IR + CHAR + [LORA] + PROMPT`；旧三行协议仍兼容。
- TAG/NL 按 Anima 可理解性分工，Compiler 处理主体计数、角色裸名、已复现的多人坏措辞、排序、去重和 exact LoRA binding。
- 角色自动发现必须由模型显式返回用户原文名字与 canonical candidate，再通过 Danbooru exact 验证；不能从整句或 `IR.subject` 污染缓存。
- `server/lora_registry.yaml` 是人工 LoRA 真相源；selection/Profile/optional/强度由用户选择，文件名与 trigger 由代码编译。同物理文件只加载一次。
- 双角色是正式质量边界，三角色只 best-effort；不恢复区域提示词。

### 参考、整图重绘与迭代

- 参考图按 `composition / composition_vibe / full` 先生成结构化观察契约，再由 Composer 合并用户要求与 LoRA；优先级是用户明确要求 > LoRA Profile > 参考范围 > 模型补全。
- 上传拆成 768/0.85 的 Vision 图和最长边 1536/0.92 的 ComfyUI 图。
- Img2Img 有 0.35/0.55/0.75 重绘预设、0.1～0.9 高级范围、`preserve/crop` 和比例提醒。它是整图重新采样，不保证局部修改或人物/发型/构图保持。
- 暗房可从任意已有节点分支。换一版默认复用 Prompt/IR 并换 seed；“基于此图重绘”使用源图像素并默认继承尺寸/seed。`CHANGE_FIELDS` 约束语义 IR 的增量，不等于像素锁定。
- 旧 `image/vibe/tweak` 协议保留一个兼容周期；界面不再把 tweak 称为微调。

### 持久化、恢复与访问

- `server/state/airpaint.db` 是任务、会话、turn、历史和用量的权威来源；`JOBS/SESSIONS/USAGE` 仅作热缓存。
- SQLite 使用 schema version/migration、WAL、foreign keys、`synchronous=FULL`。任务、可选会话 turn 和一次用量在同一事务创建。
- 每个邀请码默认每天 90 张。任务一经落盘就计一次；参数校验失败不计，失败不退款，幂等重试/重启恢复/结果重取不重复计数。
- 数据库保存真实 seed、尺寸、Prompt/Concept/IR、手工编辑标记、LoRA Binding/revision、生成方式、父任务/会话、输入输出引用、ComfyUI prompt ID、生命周期和去敏后的实际请求快照。
- owner ID 由本机 `identity.key` 对邀请码做 HMAC；原始邀请码/API key 不写入业务记录。浏览器登录后使用签名 HttpOnly cookie，不再持久保存邀请码。
- 提交前预分配 ComfyUI prompt ID 并落盘。连接失败可安全等待；响应丢失进入 `reconcile_pending`，只核对不重发；等待超时进入 `result_pending`；已出图但下载失败进入 `result_ready`，只重取结果。
- `/api/history` 提供分页服务端历史；刷新/重新登录可找回进行中任务和有效 session 分支。图片通过 `/api/images/{filename}` 按 owner 读取，不再公开挂载 `/images`。
- `done` 任务会以本地输出文件为事实来源：确认文件被人工删除后转为内部 `deleted` 墓碑，并从历史、任务详情和会话中隐藏，不展示残留参数、不返还额度；网络/权限错误不作删除判断。
- `server.maintenance` 把 SQLite online backup、一致的 `identity.key` 和数据库引用图片打成带 hash manifest 的 zip；离线恢复前自动创建 rollback 包。

### UI / 运行

- `web/index.html` 是无框架单页应用：沉浸展台、日间/夜间双主题，竖图按剩余高度完整展示；桌面 520px 编辑器默认收起，构思/Prompt/设置分页，手机独立适配。保留现有功能 DOM/API 契约。
- 「所有作品」为全局历史；「本次迭代」仅在进入真实 session 后显示。选择历史不覆盖当前草稿；图片参数与下一次生成设置分开。
- 历史来自服务端并可分页；卡片显示实际状态、seed、参数与父节点。`result_ready/reconcile_pending` 可“重新核对”。
- 轮询异常不再静默吞掉；显示连接中断和恢复查询，不用虚假百分比表达生成进度。
- 浏览器只保留主题和短暂的 request ID 恢复标识。POST 响应丢失时先查 `/api/requests/{id}`，不会自动重复创建。
- `.tools/start_airpaint.bat` 检查配置、ComfyUI 和可选 tunnel，隐藏启动进程并保存日志/PID；`.tools/stop_airpaint.bat` 优先触发 uvicorn 正常关闭，超时才强制停止，且不关闭 ComfyUI。

## 仓库地图

```text
AGENTS.md                     开发原则与 push 规约
README.md                     使用者/开发者入口
ROADMAP.md                    封版维护边界
requirements.txt             当前 Python 依赖版本
web/index.html                当前生产前端
server/settings.py            配置、路径、限制与稳定枚举
server/runtime.py             HTTP client、队列与热缓存
server/persistence.py         SQLite、身份、历史、用量与迁移
server/knowledge.py           词典、角色候选与缓存
server/lora.py                Registry、selection/context、binding
server/prompt_engine.py       Reasoning/Vision、Composer、IR/compiler
server/workflow_engine.py     workflow 注入与可恢复 ComfyUI 客户端
server/api.py                 FastAPI、鉴权、路由与单 worker
server/main.py                启动/正常关闭与旧符号兼容
server/maintenance.py         备份、校验、恢复
server/workflows/AnimaFull.json
server/lora_registry.yaml     LoRA canonical data
docs/operations.md            启停、状态、配额、备份恢复
docs/architecture.md          当前系统细节
docs/api.md                   HTTP 契约
docs/decisions.md             ADR
docs/DEVLOG.md                开发演进
```

依赖方向保持 `settings/runtime/persistence → knowledge/lora → prompt/workflow → api → main`。`main.py` 的兼容导出不是继续堆业务逻辑的理由。

## 验证状态

`v1.0.0` 确定性封版基线（2026-09-08）：

- `15 persistence/recovery tests passed`：事务配额、幂等冲突、cookie 重启、排队/运行中重启、响应丢失不重发、结果重取、历史/会话/图片归属、在线备份、整包恢复、SQLite sidecar 清理和假 ComfyUI 协议。
- `19 image iteration tests passed`。
- `58 prompt unit tests passed`。
- `6` 项 LoRA Composition 与 `18` 项 onboarding 回归通过。
- Python `compileall`/pyflakes、PowerShell 启停脚本解析、前端两段内联脚本/可靠性文案契约和当前 workflow 检查通过。
- 真实 AirPaint 进程以临时本地 ComfyUI 协议替身完成启动、cookie 登录、90 次配额读取、空历史查询和正常 shutdown；没有调用 GPU 或外部模型。
- 实际空库完成 online backup、manifest/hash/schema/integrity 校验与隔离目录整包恢复；没有覆盖生产目录。
- 浏览器在桌面与 `390×844`、纸本与石墨主题下完成结果恢复、Prompt/seed/配额、异常卡片、设置、历史分支与迭代暗房验收；移动端无横向溢出。纸本 Img2Img 画幅适配的近黑下拉框已修复。
- README 已保存当前真实界面截图与演示记录；中央图片为项目已有生成结果，任务状态明确标记为本地演示夹具，不冒充实时生成。
- `docs/showcase.md` 另存两张 D59 真实 SFW 原图，并从 PNG 内嵌 workflow 核对 Prompt、seed、尺寸、模型与采样参数；未把演示夹具字段当作真实参数。

`v1.0.0` 已封存于 `9af5604`。用户随后明确授权最后一次前端改版，选择「沉浸展台」并要求竖图优先、加宽编辑器、降低信息密度；这次是已授权的界面收尾，不重开 Prompt/模型/workflow 开发，也不移动旧 tag。当前前端验证与证据见 `docs/demo.md`。

2026-09-09 封版维护新增“缺失输出隐藏”回归，当前 `18 persistence/recovery tests passed`：覆盖完成图存在时正常访问、人工删除后历史/任务/图片/会话不再暴露、额度不返还、旧幂等请求不自动重画，以及 `result_ready` 不因尚无本地文件被误删。该维护不改变 `v1.0.0` 基线标签，也不构成新的图像质量结论。

结构性检查不能替代图片质量。不得把上述数字写成参考图或 Img2Img 画质通过。

### D59 图片验收事实

参考图/Img2Img/迭代工程实现已经在基线 commit `54c7368` 推送。五张真实图达到约定上限，工程输入、seed、节点连接和 ComfyUI history 已核对，但人眼结果没有全部通过：

| 用例 | 任务 / seed | 事实结论 |
|---|---|---|
| 构图＋氛围参考 | `299bcdd436` / `1004945114` | 短发/黄裙生效，仍带入向日葵装饰，范围并非完美隔离 |
| 完整参考 | `5471e37f11` / `1450984252` | 淡蓝裙/长发/花冠/猫保留，回眸动作丢失 |
| 低强度 Img2Img | `cec5259858` / `1264505722` | 0.35 保形明显，但请求的暖金光照变化很弱 |
| 换一版 | `2563f223c9` / `328762716` | Prompt/IR 相同且 seed 改变，傍晚生效、构图明显变化 |
| 历史节点重绘 | `f40d90fc99` / `1264505722` | 0.55、CHANGE_FIELDS=clothing 和 red dress 已实际送入，但裙子仍蓝且风格漂移 |

后续用户又用 0.75 请求脱衣，图像改变但人物、发型、服装意图均漂移。结论是当前链路确实在做 latent Img2Img，但 denoise 控制“保留多少噪声/结构”，不是局部编辑强度；封版只保留并说明，不继续堆 Prompt 或恢复 inpaint。

旧验收页和图片在本机 gitignored `server/images/` 中，clone 不保证存在。它们只作维护者复盘，不是公开演示素材。

## 已知边界

- 旧内存任务、localStorage 历史和孤立图片没有可信 owner/参数，不能凭空写入 SQLite；文件可作为旧图另存。
- `identity.key` 必须和数据库一起备份；丢失它会破坏旧 owner 映射。
- 单 worker、单机 SQLite、HTTP 轮询；不支持多实例共享图库、分布式调度或多 GPU。
- 当前单页界面的 Tailwind 与 GSAP 由外部 CDN 提供，字体使用本机字体；断网时状态数据不丢失，但样式或动效可能降级。仓库内 README 截图与 `docs/demo.md` 可离线说明项目。
- Img2Img 不是局部重绘；参考字段白名单也不能保证语义完全隔离。
- 双角色复杂遮挡、接触、身份和属性绑定仍会受模型/LoRA/seed 影响；三角色不承诺。
- 人体负面词只能降低常见失败概率，不能解决模型人体能力。
- `char_dict.yaml` 是历史资产，不代表每条已逐一验证。
- `server/config.yaml` 含密钥且被忽略。敏感值不得进入代码、文档或提交。
- 本机 `server/lora_registry.yaml` 使用 skip-worktree；此次封版明确不检查同步、不修改、不暂存。已有 repo 外安全副本位于 `E:\comfy-web-final-seal-safety\2026-09-07\lora_registry.local.yaml`。

## 封版边界与接手路线

封版完成后不再主动启动 Prompt、模型或 workflow 新阶段。只处理阻止正常使用的数据丢失、兼容或安全问题，具体见 `ROADMAP.md`。

接手顺序：

1. 先读 `AGENTS.md` 和本文件。
2. 运行 `git status`；再用 `git ls-files -v server/lora_registry.yaml` 明确 skip-worktree，不能把 clean status 当 Registry 一致证明。
3. 可靠性/历史/恢复读 `persistence.py`、`api.py`、`workflow_engine.py` 和 `docs/operations.md`。
4. Prompt/角色读 `prompt_engine.py`、`knowledge.py` 和相关 ADR；LoRA 读 `lora.py`、onboarding 工具和 Registry。
5. workflow 改动必须读 `docs/workflow-anatomy.md`、实际 JSON 和本机节点 `INPUT_TYPES/execute()`。
6. 仅在真实阻断问题范围内修改，运行最小相关验证、同步文档、显式暂存并 push。
