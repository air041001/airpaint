# AirPaint Build Handoff

> 更新：2026-08-30
> 用途：新 Agent 只读这一份文件即可了解项目全貌、当前证据、边界和接手路径。
> 开发规约仍以根目录 `AGENTS.md` 为准；代码与当前配置优先于本文。

## 一句话定位

AirPaint 是 ComfyUI 上层的 Prompt / Intent / Knowledge Intelligence Layer。它把中文画面意图、角色知识、LoRA 选择与成像参数编译成当前 Anima 工作流可执行的请求，同时保留 Prompt、LoRA 和 Workflow 的可见控制权。

当前产品不是面向小白的一键绘图器，也不替代 ComfyUI。核心优先级仍是 Prompt Intelligence → Prompt Knowledge → LoRA Intelligence → Workflow Intelligence。

## 当前已经可用

### Prompt / Intent

- Visual Composer 支持 `auto / faithful / free` 三档补全。
- Reasoning Model 当前为 DeepSeek-V4-Flash；参考图 Vision 当前为 Qwen3-VL。代码语义上分别视为 Reasoning / Vision Model，不把具体型号当永久架构。
- 文本协议为 `CONCEPT + 12 字段 IR + CHAR + [LORA] + PROMPT`；解析器仍兼容旧三行响应。
- TAG 与自然语言按当前 Anima 可理解性分流；同一信息不在两种形式重复。
- Compiler 确定性处理主体计数、角色裸名去重、LoRA exact binding、排序与已复现的双角色坏形态。
- 未知角色只有模型明确返回“用户原文名字 → canonical tag”后才查询 Danbooru；不再把整句或 `IR.subject` 猜测写进角色缓存。
- 翻译与生成解耦。用户可在生成前检查英文 Prompt、编辑中文构思并重新编译。

### Character / LoRA Knowledge

- `server/char_dict.yaml` 是历史角色知识；`server/knowledge_cache/` 是 gitignored 运行时候选，不可未经验证提升为正式知识。
- `server/lora_registry.yaml` 是 LoRA 的版本化真相源；`config.yaml.loras` 仅兼容尚未迁移资产。
- 服务启动不会扫描全部 LoRA，也不会把 Civitai trainedWords 自动升格为正式 trigger。
- onboarding 使用 `.tools/register_lora.py --agent`：目标文件 LoRA Manager 索引验收 → 作者说明候选 → 人工修订/双重确认 → Registry 原子写入。
- 多 Profile、跨 Asset 叠加、逐 Asset 强度、同物理文件去重和 registry revision 已进入生产链路。角色按语义 Profile 计数且最多 3 个；风格/动作/表情不设产品硬上限。
- 当前 style Asset 可使用固定人物印样预览。图片只帮助选择画风，不代表场景、多 LoRA 或多人质量。

### Workflow / API / UI

- 唯一现行工作流是 `server/workflows/AnimaFull.json`，覆盖 txt2img、img2img 与可选 detailer。
- Workflow Engine 负责清洗前端专属节点、统一 seed/尺寸、注入 Prompt/LoRA、选择生成分支、删减未选 detailer，并提交 ComfyUI。
- FastAPI 提供翻译、LoRA 列表、任务队列、对话迭代、健康检查与静态资源；完整契约见 `docs/api.md`。
- 队列为单并发 GPU 串行；用量、任务和对话状态仍是内存态，后端重启清零。
- `web/index.html` 是无框架单文件 SPA：纸本画室/石墨暗房双主题，桌面三栏，移动端重排，支持 Prompt 检查、LoRA 叠加、参考图、成像设置、最近作品和暗房迭代。
- 前端、后端和文档现在统一由本仓库追踪。旧 `air041001/air` 只保留迁移前历史，不再是活跃真相源，也不依赖 GitHub Pages。

## 仓库地图

```text
AGENTS.md                     开发原则、验证和 push 规约
README.md                     开发者入口与启动方式
ROADMAP.md                    只保留仍有效、由真实问题触发的未来事项
web/index.html                当前前端
server/main.py                启动入口与旧维护脚本兼容导出
server/settings.py            配置、路径、限制与稳定枚举
server/runtime.py             HTTP client、队列、任务/会话/用量内存态
server/knowledge.py           词典、角色匹配、候选查询与缓存
server/lora.py                Registry、selection、context、binding
server/prompt_engine.py       Reasoning/Vision、Composer、IR、Prompt compiler
server/workflow_engine.py     Workflow 注入与 ComfyUI 客户端
server/api.py                 FastAPI、中间件、路由与 worker
server/workflows/AnimaFull.json
server/lora_registry.yaml     跟踪的 LoRA canonical data
docs/architecture.md          当前系统细节
docs/api.md                   HTTP 契约
docs/decisions.md             ADR；旧决定保留，后续决定写修订关系
docs/DEVLOG.md                有意义事件的时间线
docs/workflow-anatomy.md      当前 workflow 节点和注入依据
.tools/                       维护、验证、启动脚本
```

后端依赖方向应保持：

```text
settings / runtime
        ↓
knowledge / lora
        ↓
prompt_engine / workflow_engine
        ↓
api
        ↓
main
```

`main.py` 的兼容导出是迁移边界，不是继续堆业务逻辑的理由。新逻辑进入相应职责模块。

## 当前验证基线

日常确定性检查：

```text
python -m compileall -q server
node .tools/check_frontend.js
python .tools/test_prompt_unit.py
python .tools/test_lora_composition.py
python .tools/test_lora_onboard_agent.py
python .tools/register_lora.py --validate
python .tools/inspect_wf.py
```

2026-08-30 整理与拆分后的基线：

- Prompt / Registry / Workflow / API 路由：58 项通过。
- LoRA Composition：6 项通过。
- LoRA onboarding：18 项通过。
- Registry：15 个 Asset 校验通过。
- Python 模块编译、前端内联脚本解析和当前 workflow 检查通过。

这些测试只证明协议、解析、binding 和 workflow 结构没有退化。图片质量必须使用固定 Prompt/seed/参数真实生图并由人眼判断；不要用 Prompt 更长、IR 更满或测试数量代替画质结论。

## 已知边界

- PromptState 尚未实现。当前有结构化 IR 和可编辑构思，但暗房历史仍主要围绕编译后的字符串，不能保证字段级长期锁定。
- 双角色已有 count、identity cluster、tag-first 和短关系句护栏；三角色仍为 best-effort。多 LoRA 工程正确不等于主体关系、遮挡、手部接触或属性绑定必然正确。
- 人体负面词只降低部分常见失败概率，不表示手脚问题已经解决。
- `char_dict.yaml` 是历史资产，不代表每条均已逐一验证；外部候选不得直接污染正式知识。
- `config.yaml` 含 token/API key并被忽略。敏感值不得进入代码、文档或提交。
- 本机可能对 `server/lora_registry.yaml` 使用 `skip-worktree` 抑制状态噪声。有意同步前先检查 `git ls-files -v server/lora_registry.yaml`，需要同步时先清除标记并审查差异。
- ComfyUI 节点注入必须核对 workflow JSON、目标 custom node 的 `INPUT_TYPES/execute()` 和实际连接；不可根据节点名称猜输入格式。

## 当前未来方向

没有自动开始的新 Phase。先从真实使用收集“输入 → 构思 → Prompt → LoRA/参数 → 图片”的可复现失败，再做定向修订。

- PromptState：只有字段级锁定需求高频或字符串累积稳定出错时启动。
- Workflow Intelligence：只有当前 Prompt/Knowledge/LoRA 层无法表达明确需求时启动。
- 持久化、WebSocket、邀请码管理、Docker/多实例：由真实并发、可靠性或部署需求触发。
- 不恢复无目标的大批量 Prompt 跑分，不建设大规模 LoRA 推荐/marketplace/社交/用户画像/向量库。

## 接手读取路由

新 Agent 默认顺序：

1. 先读 `AGENTS.md`，再读本文件。
2. 修改 Prompt/角色：读 `server/prompt_engine.py`、`server/knowledge.py` 和相关 ADR。
3. 修改 LoRA：读 `server/lora.py`、`server/lora_registry.yaml`、onboarding 工具和相关 ADR。
4. 修改 workflow：读 `docs/workflow-anatomy.md`、`AnimaFull.json`、`server/workflow_engine.py`，再查本机 custom node 源码。
5. 修改 API/UI：读 `docs/api.md`、`server/api.py`、`web/index.html`。
6. 修改前先定位调用方和下游；完成后运行匹配范围的验证、同步受影响文档并按 `AGENTS.md` push。

已经完成的旧 PLAN 和一次性实验资产不再常驻主分支。历史结论保留在 `docs/decisions.md`、`docs/DEVLOG.md` 与 Git 历史；需要复盘时按 ADR/日期恢复，不把完成态计划继续当当前任务清单。
