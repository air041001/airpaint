# AirPaint Build Handoff

> 更新：2026-08-31
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
- 参考图按 `composition_vibe/composition/full` 生成观察契约，再进入同一 Composer；独立 Img2Img 先理解原图，将文字视为改动项。

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
- 图片上传分分析图（768/JPEG 0.85）与生成图（1536/JPEG 0.92）。Img2Img 提供 0.35/0.55/0.75 重绘预设、preserve/crop 与比例提醒，不静默更换尺寸。
- 任务保存实际 seed、Concept、十二字段 IR、最终 Prompt/手工编辑标记、Binding 与父节点快照；暗房可从任一完成图分支。换一版默认新 seed；「基于此图重绘」（旧称微调，action 仍为 `tweak`）默认继承源图尺寸/seed，使用 `CHANGE_FIELDS` 增量修订，不拼接历史中文。界面明确它是整图重绘，不保证指定改动或其他内容不变；文字留空也会重新生成，独立上传不自动继承原图 LoRA。
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
python .tools/test_image_iteration.py
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

### 本轮参考图 / Img2Img / 迭代验收（D59）

实现与确定性检查已完成，五张真实图已达到本轮上限；图片验收尚未通过，未提交/推送。现已通过 58 项原 Prompt 回归、19 项新增参考/迭代回归、6 项 LoRA Composition，以及 Python 编译/pyflakes/前端语法检查。Registry 同步、修改、暂存和推送明确排除于本阶段；上述 15 Asset 为旧整理基线，不是本轮重新检查结果。

真实验收限定最多五张，统一当前 Anima workflow、生产负面、无额外 LoRA/精修；原始参考为用户此前提供的夏日水鲸/少女/白猫图。实际 seed、job 与源节点逐图记录，图片质量由用户确认，不能以接口或结构通过代替。

| 用例 | 任务 / seed | 状态 |
|---|---|---|
| 构图＋氛围，短发黄裙覆盖 | `299bcdd436` / `1004945114` | 已生成；短发/黄裙生效，仍带入向日葵装饰，范围不是完美语义隔离；待用户评价 |
| 完整参考，淡蓝裙覆盖 | `5471e37f11` / `1450984252` | 已生成；淡蓝裙/长发/花冠/猫保留，但回眸动作丢失；待用户评价 |
| 低强度独立 Img2Img | `cec5259858` / `1264505722` | 以完整参考图为原图，0.35 请求暖金傍晚；保形明显，光照变化很弱，不能判为改动成功 |
| 换一版 | `2563f223c9` / `328762716` | 父 `cec5259858`；Prompt/IR 原样相等，新 seed、txt2img；傍晚生效，构图变化明显，待用户评价 |
| 从历史节点微调 | `f40d90fc99` / `1264505722` | 父 `cec5259858` 而非最新图；0.55 只改红裙，CHANGE_FIELDS 仅 clothing，另外 11 字段及尺寸/seed 不变；实际图裙子仍蓝且风格漂移，不能判改色成功 |

浏览器已实际验证上传、参考翻译/生成、独立 Img2Img、换一版、选择旧图继续及微调；额外验证 crop 位置选择、比例提醒和主动采用最近画幅（未点击时仍保持 832×1216，点击后才改 1344×768）。ComfyUI 实际历史确认：第 3 张生成输入为 1344×768（没有误用 768 分析图）；第 5 张 sampler denoise=0.55、CLIP 正向含 red dress，节点 31 仍连接 39/47/0，源图正确。因此本轮不能把两张 Img2Img 的改动失败归为漏传 delta/seed/图片，但也不足以宣布 Anima 所有 Img2Img 都无效。

本地对照页：`server/images/acceptance-20260831.html`。五张文件依次为 `f50b4e6bc3f9.png`、`e2d6960db23d.png`、`308e3a9f8874.png`、`b83b3fc8176a.png`、`9fadc474dc59.png`，均在 `server/images/`，gitignored。图片与对照页不随 clone 提供。

接手边界：不要继续抽卡，不要宣称全部验收通过。先由用户评价参考/换一版，并决定是否接受当前 Img2Img 的保形优先边界，或另开一个有明确假设与预算的编辑能力修订。若未获接受，不提交为完成阶段。测试实例本机 `127.0.0.1:8001` 保存会话 `ce15a3279a`（内存态）；正常生产端口 8000 本轮未启动或重启。

## 已知边界

- 已实现最小任务快照、IR 增量修改与分支历史，但没有永久字段锁、区域约束或持久化。手工编辑英文 Prompt 后，旧 IR 要等下一次修订才重建；不宣称图像局部锁定。
- 参考字段白名单不等于完美语义隔离，具体装饰可能藏在布局描述中；图生图低强度也未必实现指定改动，必须看图。重启后旧会话无法继续，图片文件仍保留。
- 双角色已有 count、identity cluster、tag-first 和短关系句护栏；三角色仍为 best-effort。多 LoRA 工程正确不等于主体关系、遮挡、手部接触或属性绑定必然正确。
- 人体负面词只降低部分常见失败概率，不表示手脚问题已经解决。
- `char_dict.yaml` 是历史资产，不代表每条均已逐一验证；外部候选不得直接污染正式知识。
- `config.yaml` 含 token/API key并被忽略。敏感值不得进入代码、文档或提交。
- 本机可能对 `server/lora_registry.yaml` 使用 `skip-worktree` 抑制状态噪声。有意同步前先检查 `git ls-files -v server/lora_registry.yaml`，需要同步时先清除标记并审查差异。
- ComfyUI 节点注入必须核对 workflow JSON、目标 custom node 的 `INPUT_TYPES/execute()` 和实际连接；不可根据节点名称猜输入格式。

## 当前未来方向

没有自动开始的新 Phase。先从真实使用收集“输入 → 构思 → Prompt → LoRA/参数 → 图片”的可复现失败，再做定向修订。

- 更完整 PromptState：只有本轮最小快照/IR 增量仍无法满足真实需求时，再考虑永久字段锁等扩展。
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

本轮已删除未跟踪的 `.tools/eval_set/visual_director/` 共 32 个一次性文件；不迁移候选/缓存。D55/D56、DEVLOG 和正式回归仍保留有效结论；这批未跟踪文件本身不能从 Git 恢复。
