# AirPaint Build Handoff

> 交接日期：2026-08-25
> 当前分支：`main`
> 远程状态：LoRA Context / Binding 首版与双主题三栏工作台均已实现、验收并推送；实际以根仓库及 `web/` 子仓库各自的 `git log` / `git status` 为准

## 1. 当前阶段与进度

### 当前阶段

项目的 **最终大工程：LoRA Context / Binding（`docs/PLAN-LORA.md` v2）已在首版定义边界内全部完成并通过用户人眼验收**。

Phase 2.6（Prompt Expansion）、Phase 3（Character Knowledge）与 PLAN-LORA Step 0-10 均已完成。网站 MVP 细节优化已完成两批：先完成画布优先布局与分组尺寸选择器，再迁移为有状态三栏工作台和纸本画室/石墨暗房双主题；迁移后的标题标记、LoRA Profile 选中态、下拉边界、直接生成 Prompt 同步和竖图首屏约束缺陷也已修复。Phase 4 PromptState 继续由真实暗房使用触发；Phase 8 Workflow Intelligence 长期保留但不自动启动。

### 已完成模块

- Phase 1/2.6 Prompt IR：生产文本 LLM 输出 12 字段 `IR` + `PROMPT`，旧 `TAGS/NL` 和 5 字段协议保留降级解析。
- `compile_prompt()`：角色裸名清理、tag 去重、`count → character → general` 排序、NL 拼接。
- `infer_render_profile()`：当前只对明确 NSFW、单主体、简单动作使用 `tag_first`；普通 SFW 保留 NL，复杂关系使用 `relation_hybrid`。
- `/api/translate`：增加 `prompt_ir` 和 additive `prompt_ir_meta`，前端旧 `breakdown` 契约保持兼容。
- 结构性回归：由 42 个零依赖单测覆盖 Prompt/角色知识、LoRA Registry/Binding 与 txt2img/img2img 显式路由，不依赖未验证 baseline。
- Failure taxonomy：已覆盖 counting、entity binding、action/pose、interaction、spatial、lighting、NSFW anatomy、model artifact、semantic misread 等类型。
- NSFW 结构评测集：8 条明确成人内容，结构与 explicit safety 验证通过。
- Rendering Strategy 实验工具：固定 Prompt、seed、尺寸、workflow，支持盲评页、manifest、variant 对照。
- Dictionary vs LLM 对照工具与 girl/female 词汇对照工具。
- 用户可手动编辑英文 `prompt_en`，作为复杂动作/多角色失败的产品 fallback。
- LoRA Context / Binding：versioned Registry/Profile、last-good revision、scanner inventory、legacy adapter、Selection Resolver、幂等 exact Binding Compiler。
- active LoRA 在翻译前进入 Reasoning/Vision Model 上下文；translate/jobs/dialog/start-image 共用 binding snapshot/revision。
- 前端 Profile auto/显式锁定、per-asset 默认强度、provides/verified/待注册、切换后 stale Prompt 防串线。
- onboarding：双击 `.tools/start_lora_onboard_agent.bat` 或运行 `.tools/register_lora.py --agent`，粘贴作者说明后由 Reasoning Model 生成可修订候选；代码恢复 exact trigger/明确单值强度，双重确认后原子更新 Registry。`--inspect/--civitai/--validate` 继续可用，不自动把 HTML/Civitai trainedWords 升格为正式知识。

### 已验证项

- Phase 2 profile 收窄后，结构回归由单测覆盖（历史 30/30 仅记录，baseline 已清理）。
- Prompt/LoRA 单测：最近一次为 `42` 个通过。
- NSFW 结构验证：`8/8`，workflow safety 为 `explicit`。
- Failure taxonomy 聚合：`17 pass / 18 fail`，主要失败类型为 `interaction_relation`、`model_artifact`、`action_pose`、`anatomy_nsfw`。
- Phase 1.5 首轮：30 张图生成成功，用户完成盲评。
- Phase 1.5 R4 换 seed：5 张图生成成功，仍无法稳定表达“后撤”，已记录为 base Anima 多角色构图/动作绑定限制。
- Phase 2 W3 profile A/B：20 张图生成成功，用户完成盲评。
- Phase 2 W4 Dictionary vs LLM：20 张图生成成功，用户完成盲评。
- Phase 2 W6 girl/female：4 张图生成成功，用户认为无明显差异。
- Phase 2.5 E1-E7 扩写实验：14 张图生成成功，用户已完成详细评审。
- Phase 2.6 A1/A2/A3 三路实验：21 张图和 E1/E6/E7 换 seed 的 9 张补测图均生成成功；用户已完成两轮 A/B/C 盲评，A3 对 A2 为 4 胜、1 平、2 负；初版生产 A/B 被判定为协议退化，已改为独立 `IR + PROMPT` 画师协议并修复护栏；v5 新旧 A/B 为 3 胜 2 平 0 负，提示词增强保留。
- Phase 3 Danbooru 角色查询：主 API exact lookup 与 `name_matches` 连通；长门有希/御坂美琴已分别写入 `nagato_yuki`/`misaka_mikoto` auto cache，长门固定 Prompt 已生图并经用户确认。
- 未验证的 baseline 回归资产已清理（DEVLOG 40）；结构不变量由确定性单测覆盖。
- PLAN-LORA v2 已落地并通过用户验收：5 组真实 A/B 为 aware 1 胜 4 平 0 负；DeepSeek 换 Anima 后图书馆 pair 平局且用户认为优于旧 IL；Blue Archive 午后光线补测正常。
- Remielle Dan（base/白/黑/泳装）、Dolphro-kun 风格与无 trigger Light 风格均由用户确认生效并通过验收，Registry 已提升为 verified。LoRA 显示名来自 `server/lora_registry.yaml` 的 Asset/Profile `name`，不要在前端按 key 硬编码别名。
- 尺寸路由纠错后，`1024x1536` 在 RTX 4060 Laptop 8GB、无 detailer 下实际输出 1024×1536，端到端 84.16 秒；此前 309.72 秒任务实际误走 `input2` 并输出 832×1216，不是高分辨率性能结论。前端现从成品 `naturalWidth/naturalHeight` 显示真实像素。
- 生产前端已迁移为状态驱动的三栏工作台：描述跨栏、Prompt 左、成图中、参数右、历史下方；首次翻译使用独立检查态，有图后重翻译保持图片不动。纸本/石墨只改变材质与配色，不改变标题标记语义；LoRA 选中 Profile 在两主题均有明确对比度，角色/风格菜单会按右栏上下空间自动翻转。
- “生成”会静默调用翻译并把返回的英文 Prompt/五项拆解同步到左栏，再提交任务；不再只有“先看翻译”路径更新 UI。成图舞台脱离 `flex-1` 的固有尺寸反撑，按视口高度在桌面连续缩放并始终使用 contain，896×1152 等竖图不会再把画布撑成 1152px 高。

## 2. 修改/新增文件清单

### 后端与测试

| 文件 | 当前作用 |
|---|---|
| `server/main.py` | Prompt/Intent/Workflow Engine + LoRA Registry/Scanner/Resolver/Binding/API/session |
| `server/lora_registry.yaml` | versioned LoRA Asset/Profile/trigger/provides/default strength 人工知识 |
| `.tools/test_prompt_unit.py` | 42 个零依赖 Prompt/角色知识/LoRA Registry/Binding/API/session/workflow 路由单测 |
| `.tools/register_lora.py` | LoRA sidecar inspection、Registry validate 与原子 onboarding |
| `.tools/start_lora_onboard_agent.bat` | 双击启动本地 LoRA 入库 Agent；API key 只从 gitignored config 读取 |
| `.tools/test_lora_onboard_agent.py` | onboarding JSON/schema/exact trigger/strength/Civitai 分支确定性测试 |
| `.tools/eval_set/render_exp/lora_context_cases.yaml` | 5 组真实 LoRA fixed-condition A/B 夹具 |
| `.tools/eval_set/render_exp/run_lora_context_ab.py` | legacy/aware/author-control 真实生图与盲评页 runner |
| `.tools/eval_set/image_cases.yaml` | Phase 1 固定 Prompt+seed 视觉夹具（002/012/018，已人眼验证） |
| `.tools/eval_set/run_gen_test.py` | 固定 Prompt+seed 生图测试 runner |
| `.gitignore` | 忽略 candidate、NSFW candidate 和实验输出目录 |

### Phase 2 评测资产

| 文件 | 当前作用 |
|---|---|
| `.tools/eval_set/taxonomy.yaml` | 失败类型定义 |
| `.tools/eval_set/render_exp/labels.yaml` | Phase 1.5 现有图片的人工作业标签 |
| `.tools/eval_set/render_exp/label.py` | taxonomy 标签校验与聚合 |
| `.tools/eval_set/render_exp/phase2_results.yaml` | W3/W4/W6 人工结果与 Phase 2 结论 |
| `.tools/eval_set/render_exp/profile_cases.yaml` | profile A/B 10 case 夹具 |
| `.tools/eval_set/render_exp/run_profile_ab.py` | legacy/profile 固定条件 A/B runner |
| `.tools/eval_set/render_exp/dict_llm_cases.yaml` | Dictionary/LLM 10 对照夹具 |
| `.tools/eval_set/render_exp/run_pair_experiment.py` | 两组 Prompt 来源对照 runner，也支持 girl/female |
| `.tools/eval_set/render_exp/girl_female_cases.yaml` | girl/female 4 图词汇夹具 |
| `.tools/eval_set/render_exp/run_experiment.py` | 通用固定 Prompt/seed 实验 runner和盲评页生成器 |
| `.tools/eval_set/render_exp/expansion/README.md` | Phase 2.5 扩写实验说明 |
| `.tools/eval_set/render_exp/expansion/cases.yaml` | E1-E7 忠实翻译/画师补全夹具 |
| `.tools/eval_set/render_exp/expansion/phase26_cases.yaml` | Phase 2.6 A1/A2/A3 输入夹具 |
| `.tools/eval_set/render_exp/expansion/run_phase26.py` | prepare/render 两阶段 runner，A3 原型协议仅在实验脚本内 |
| `.tools/eval_set/render_exp/expansion/phase26_results.yaml` | 两轮 A/B/C 盲评与生产 v5 A/B 结果 |
| `.tools/eval_set/nsfw/cases.yaml` | 8 条 NSFW 结构评测集 |
| `.tools/eval_set/nsfw/validate.py` | NSFW IR、成人声明和 explicit safety 校验 |
| `.tools/eval_set/nsfw/image_cases.yaml` | 6 条 NSFW 固定视觉基线夹具 |
| `.tools/eval_set/nsfw/visual_results.yaml` | N01-N06 用户视觉基线结果 |

### 文档

| 文件 | 当前作用 |
|---|---|
| `docs/PLAN-v5 — AirPaint Prompt Intelligence.md` | 长期实施路线（唯一现行计划，取代已删除的 PLAN-v4） |
| `docs/PLAN-LORA.md` | 最终任务 v2 设计、Step 0-10 与最终验收结果 |
| `ROADMAP.md` | Phase 2.6/3/LoRA Context 首版完成；Phase 4/8 与多人 composition 条件触发 |
| `docs/DEVLOG.md` | 第 31-50 条记录 Phase 1、LoRA Context、入库 Agent、最终收口、双主题迁移与迁移后修复 |
| `docs/decisions.md` | D39 为已实现并验证的 LoRA Binding；D45 为当前有状态三栏工作台与双主题决策 |
| `docs/architecture.md` | 当前 Prompt/LoRA Registry/Binding/Workflow/Frontend 架构与边界 |
| `docs/api.md` | translate/jobs/dialog 的 selection/binding/revision 契约 |
| `docs/workflow-anatomy.md` | 用户维护的 AnimaFull 权威节点解剖；当前 HEAD 中已单独提交，后续必须优先参考 |

### 当前工作区

- `web/` 是独立 Git 仓库。双主题生产迁移为 `e429c91`，迁移后标题标记/LoRA 选中态/菜单边界修复为 `95fa803`，直接生成 Prompt 同步与首屏成图约束修复为 `99bb15c`，均已推送 `origin/main`。
- 根仓库最近的迁移后交接更新为 `2c97eff`；本交接更新位于其后的文档提交，具体 hash 以 `git log` 为准。
- `docs/PLAN-v4` 与 `docs/inspiration.md` 已删除（历史参考不再保留）；`web/index.html.bak2` 已删（web/ 子仓库）。
- `.tools/eval_set/render_exp/output/`、`.tools/eval_set/nsfw/output/`：实验生成物，已被 gitignore，不能当源码提交。
- `server/lora_registry.yaml` 当前有用户自有未提交修改；`.opencode/`、`opencode.jsonc` 与 `docs/PROMPT-TRANSLATION-HANDOFF.md` 是用户/环境未跟踪文件。它们均未由本次前端修复修改，后续不要误 stage、回滚或覆盖。

## 3. 未完成项

### 立即下一步

1. 没有自动开启的新大阶段；双主题三栏工作台已经迁移并完成首轮迁移后修复。下一会话应以真实使用反馈继续处理小缺陷，优先观察空/错状态、暗房 redo/tweak、历史作品与手动 Prompt 编辑，不应在没有新证据时再次整体推翻布局。
2. 新下载 LoRA 推荐双击 `.tools/start_lora_onboard_agent.bat`：它会尝试调用 LoRA Manager 增量 scan、列出未注册文件、接收多行作者说明并展示候选；输入 `revise` 可自然语言修订，只有 `write` + 最终 `y` 才写 Registry。仍需检查 exact tags/强度，并在真实生图后再提升 verified。
3. 若 ComfyUI 未运行，Agent 会安全跳过 Manager scan；启动 ComfyUI 后重新运行即可。增量 scan 仍找不到文件时才调用 `?full_rebuild=true`，不要日常全量哈希。

### 条件性未完成项

- 无当前阻塞开发项。不得退回“LLM 复制 trigger”或“生成时盲拼 trainedWords”的旧边界。

### 已明确跳过

- R4 不再继续做自动特判；复杂多角色失败使用现有 Prompt 编辑 fallback。
- weighted spatial NL 不进入默认策略。
- semantic negative 不进入默认策略。
- girl/female 不改 canonical，当前 W6 无明显差异。
- 不写 PLAN-v6；Phase 4 PromptState 继续延后（暗房使用数据触发）。
- PLAN-LORA 明确不做：自动解析 HTML description / LoRA 冲突 ML 检测 / LLM 决定 trigger 权重 / Phase 8 Workflow Intelligence；无真实多人资产前不宣称完成 LoRA composition。

## 4. 当前上下文遗留问题

- Phase 2 首轮验证的是 rendering/source 对照，不是 Prompt Expansion；这导致代码曾经更像 translator，而不是画师补全器。
- D13 的“氛围→场景扩写”精神在 D28 的 “TAGS only / 简单输入 NL 留空 / ONLY remaining input” 约束下明显减弱；Phase 2.5 要验证是否恢复。
- `prompt_ir_meta` 已以 additive 方式标注 painter expansion 来源、字典来源和 reroll 方案；不改变 12 字段 IR 结构。
- E1-E7 两轮 A1/A2/A3 人工结论已记录；v5 生产 A/B 最终为 3 胜 2 平 0 负。
- A2 详细中文在部分 case 更强，说明输入信息量是质量上限；自动增强保留但不替代具体用户意图。
- NSFW 目标是“高质量二次元插画的色气感”，不是裸体 tag 堆砌；统一补全底层应使用构图、光影、氛围、材质，NSFW 只在服装状态、身体语言、揭示节奏上分流。
- R4 的失败不是当前 Phase 2 主线 blocker，但应继续作为 failure taxonomy 的 `interaction_relation` + `spatial_composition` + `model_artifact` 样本。
- `docs/decisions.md` 历史 D1-D38 必须保持完整（D30-D33 已重排、D33 已补全）；编辑只能追加 ADR，不能用 Add File 替换已有文件。
- LoRA Context 已解决原“先翻译、后盲拼”的状态割裂；真实 A/B 没有场景退化。仍需注意：跨文件多人 LoRA composition 尚无资产证据，不能从 schema 支持推导为质量完成。
- 当前前端的产品语义为 `compose → review → result → darkroom`：首次无图不显示空画布；首次查看翻译进入 review；“生成”静默翻译后直接提交，但仍必须把本次英文 Prompt/拆解写入结果左栏；已有结果后再次翻译只更新左侧 Prompt，图片保持原位。日夜主题必须共享相同组件几何和交互，只允许材质、颜色、阴影等视觉 token 不同。

## 5. 验证命令与状态

### 当前最近验证

```text
python -m py_compile server/main.py .tools/register_lora.py .tools/test_lora_onboard_agent.py .tools/eval_set/render_exp/run_lora_context_ab.py
```

状态：通过。

```text
python .tools/test_prompt_unit.py
```

状态：`42 prompt unit tests passed`。

```text
python .tools/test_lora_onboard_agent.py
python .tools/register_lora.py --validate
```

状态：`9 lora onboarding agent tests passed`；`registry valid: 9 assets`。

前端：4 个内联 script 语法通过；122 个 DOM id 无重复，104 个静态 `$()` 引用全部存在。浏览器完成 1920×950、1920×1080、1365×720、390×844 验证：直接生成后英文 Prompt 与五项拆解正确出现，“先看翻译”仍保持当前图片；同一张 896×1152 竖图在 950px 高视口中由错误的 1152px 固有高度降为约 552px 完整 contain，1080/720 高视口分别自适应约 682/322px，工具栏与舞台均在首屏。干净重载只有既有 Tailwind CDN 警告，无运行时错误。

高分辨率：修复后 `1024x1536` 无 LoRA/detailer 端到端输出 `1529ed18e206.png`，PIL 实测 1024×1536；`build_prompt`/Comfy history 均确认 txt2img `select=1`、节点 56=`1024x1536`。端到端 84.16 秒，Comfy 执行约 82.3 秒。原 `anima_20260823_00014_.png` 历史记录为 `select=2`，实际 832×1216、执行约 309.7 秒，不能作为高分辨率验证。

真实 LoRA：5 组 fixed-condition A/B 均完成用户人眼验收，aware 1 胜 4 平 0 负；DeepSeek Anima 最终 3/3 生成，图书馆 A/C 平局。

```text
python .tools/test_prompt_unit.py  # 含 test_painter_tag_guard_preserves_explicit_safety_marker
```

状态：NSFW explicit safety marker 由确定性单测覆盖（`nsfw/validate.py` 作为工具保留，运行前需临时生成 8 条 candidate，candidate 是 gitignored 运行时产物）。

```text
python .tools/eval_set/render_exp/label.py
```

状态：taxonomy labels valid；Phase 2.6 结果与生产 v5 A/B 已纳入聚合。

### Phase 2.5/2.6 已执行命令

```text
python .tools/eval_set/render_exp/run_experiment.py --cases .tools/eval_set/render_exp/expansion/cases.yaml --output .tools/eval_set/render_exp/output/expansion --expected-count 14
```

状态：14/14 图片生成成功。

```text
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode prepare
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode render
```

状态：固定 Prompt 21/21、平局补测 9/9、生产 v5 A/B 10/10 图片生成成功，结果均已记录。

## 6. 注意事项/禁区

- 修改前必须先读 `AGENTS.md` 与本文件，再按下方“下一会话必读文件”的任务路由读取对应资料；不要把所有历史评测文档无差别塞入上下文。
- `docs/workflow-anatomy.md` 是当前 AnimaFull 节点权威参考；节点 4 是负向 `ImpactWildcardProcessor`，节点 55 是负向 CLIP 编码，不要再按旧节点猜。
- 不要把实验用 `negative_text_node` / 节点 4 覆盖能力当成生产默认负面；config 默认没有 `negative_text_node`，D6 固定负面策略保持不变。
- **LoRA Context / Binding 首版已经完成**。维护时保持 versioned Registry/Profile、LoRA-aware text/vision、确定性 Binding Compiler 与 translate/jobs/dialog snapshot 边界；不要让 LLM 决定文件、强度或 exact trigger。
- 不要把 weighted NL、semantic negative、girl/female 替换、R4 特判、PromptState、自动优化 Agent 提前合并。
- 不要把“Prompt 更长”“IR 字段更满”“结构解析通过”当成图像质量证明；人眼是最终语义验收。
- 单张图的手脚/武器偶发错误先视为随机因素；只有换 seed 后仍重复才归入策略失败。
- 但分页、黑线、第三主体、场景关系丢失等重复出现的问题要进入 failure taxonomy，不能用“随机”掩盖。
- E6 不要强行写 `adult woman`/`female` 作为视觉正向描述；按用户确认使用自然 `woman/1girl`，同时保持成人内容和 safety 约束。
- base Anima 是当前唯一 checkpoint；不要引用其他 merge 的 score/artist 经验直接写成官方规则。
- `baseline.yaml` / `cases.yaml` / `run_baseline.py` / `compare.py` 已删除（未验证的 agent 产物，曾误导注意力）；结构性不变量由单测覆盖，图像质量只由人眼确认。
- 生产 API 端口可能仍运行旧后端进程，修改 `server/main.py` 后要重启后端再做在线 smoke。
- 当前根工作区的 `server/lora_registry.yaml` 用户修改，以及未跟踪 `.opencode/`、`opencode.jsonc`、`docs/PROMPT-TRANSLATION-HANDOFF.md` 都不要误 stage 或回滚；用户维护的 workflow 文档变更同样不要回滚。

## 下一会话必读文件

先固定读取：

1. `AGENTS.md`
2. `docs/BUILDHANDOFF.md`

再按任务路由补读，不要全量加载：

- **前端细节**：`web/index.html` 相关范围、`docs/decisions.md` D45、`docs/DEVLOG.md` 第 48-50 条、`docs/architecture.md` 的 Frontend 段。
- **LoRA 维护/扩展**：`docs/PLAN-LORA.md`、`docs/decisions.md` D39、`docs/api.md` LoRA 契约；新增资产优先走 `.tools/register_lora.py --agent`。
- **Prompt Intelligence**：`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`、`ROADMAP.md`、相关 ADR；只有要复核旧实验结论时才读对应 `phase26_results.yaml` / `visual_results.yaml`。
- **Workflow/ComfyUI 注入**：`docs/workflow-anatomy.md`、目标 workflow JSON、相关 custom node 源码与 `server/main.py` 注入路径。
