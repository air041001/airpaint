# AirPaint Build Handoff

> 交接日期：2026-08-23
> 当前分支：`main`
> 远程状态：LoRA Context / Binding 首版实现、验证与文档闭环应与 `origin/main` 同步；实际以 `git log` / `git status` 为准

## 1. 当前阶段与进度

### 当前阶段

项目的 **最终大工程：LoRA Context / Binding（`docs/PLAN-LORA.md` v2）已在首版定义边界内全部完成并通过用户人眼验收**。

Phase 2.6（Prompt Expansion）、Phase 3（Character Knowledge）与 PLAN-LORA Step 0-10 均已完成。网站 MVP 细节优化第一批也已完成：画布优先布局、紧凑输入、接触印样历史带和分组尺寸选择器。Phase 4 PromptState 继续由真实暗房使用触发；Phase 8 Workflow Intelligence 长期保留但不自动启动。

### 已完成模块

- Phase 1/2.6 Prompt IR：生产文本 LLM 输出 12 字段 `IR` + `PROMPT`，旧 `TAGS/NL` 和 5 字段协议保留降级解析。
- `compile_prompt()`：角色裸名清理、tag 去重、`count → character → general` 排序、NL 拼接。
- `infer_render_profile()`：当前只对明确 NSFW、单主体、简单动作使用 `tag_first`；普通 SFW 保留 NL，复杂关系使用 `relation_hybrid`。
- `/api/translate`：增加 `prompt_ir` 和 additive `prompt_ir_meta`，前端旧 `breakdown` 契约保持兼容。
- 结构性回归：由 42 个零依赖单测覆盖 Prompt/角色知识、LoRA Registry/Binding 与高分辨率 timeout，不依赖未验证 baseline。
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
- Prompt/LoRA 单测：最近一次为 `41` 个通过。
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
- 前端布局已按用户 1920×950 截图重排；`1024x1536` 在 RTX 4060 Laptop 8GB、无 detailer 下真实成功，峰值约 7.75GB，耗时略超旧 300 秒。后端现按像素面积将该档 timeout 放宽为 450 秒，前端提示 2～5 分钟。

## 2. 修改/新增文件清单

### 后端与测试

| 文件 | 当前作用 |
|---|---|
| `server/main.py` | Prompt/Intent/Workflow Engine + LoRA Registry/Scanner/Resolver/Binding/API/session |
| `server/lora_registry.yaml` | versioned LoRA Asset/Profile/trigger/provides/default strength 人工知识 |
| `.tools/test_prompt_unit.py` | 42 个零依赖 Prompt/角色知识/LoRA Registry/Binding/API/session/timeout 单测 |
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
| `docs/DEVLOG.md` | 第 31-45 条记录 Phase 1、LoRA Context、入库 Agent、最终收口与首批 UI 优化 |
| `docs/decisions.md` | D39 为已实现并验证的 LoRA Binding 决策 |
| `docs/architecture.md` | 当前 Prompt/LoRA Registry/Binding/Workflow/Frontend 架构与边界 |
| `docs/api.md` | translate/jobs/dialog 的 selection/binding/revision 契约 |
| `docs/workflow-anatomy.md` | 用户维护的 AnimaFull 权威节点解剖；当前 HEAD 中已单独提交，后续必须优先参考 |

### 当前工作区

- PLAN-LORA planning commit 为 `b141a7a`；实现、前端和本次文档闭环应已分别提交并推送，具体 hash 以 `git log` 为准。
- `docs/PLAN-v4` 与 `docs/inspiration.md` 已删除（历史参考不再保留）；`web/index.html.bak2` 已删（web/ 子仓库）。
- `.tools/eval_set/render_exp/output/`、`.tools/eval_set/nsfw/output/`：实验生成物，已被 gitignore，不能当源码提交。
- `.opencode/`、`opencode.jsonc`：环境未跟踪文件，未修改、不可纳入提交。

## 3. 未完成项

### 立即下一步

1. 没有自动开启的新大阶段；网站细节后续优先观察 LoRA 中文摘要、空/错状态、暗房 redo/tweak、历史作品与手动 Prompt 编辑反馈。
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

前端：两个内联 script 语法通过；103 个 DOM id 无重复，全部 `$()` 引用存在；浏览器完成 1920×950、1280×720、390×844 响应式与尺寸展开/收起验证。

高分辨率：`1024x1536` 无 detailer 真实工作流成功输出 `anima_20260823_00014_.png`；旧 300 秒 deadline 先误报超时，像素面积 timeout 修正后该档为 450 秒。

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

- 修改前必须先读 `AGENTS.md`、本文件、`docs/PLAN-LORA.md`、最新 PLAN-v5、D34-D39 和 `docs/workflow-anatomy.md`。
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
- 当前工作区未跟踪 `.opencode/`、`opencode.jsonc` 不要 stage；用户维护的 workflow 文档变更不要回滚。

## 下一会话必读文件

按以下顺序读取：

1. `AGENTS.md`
2. `docs/BUILDHANDOFF.md`
3. `docs/PLAN-LORA.md`（最终任务执行蓝本）
4. `docs/PLAN-v5 — AirPaint Prompt Intelligence.md`
5. `ROADMAP.md`
6. `docs/decisions.md`（重点 D34-D39）
7. `docs/DEVLOG.md`（重点第 31-42 条）
8. `docs/architecture.md`
9. `docs/workflow-anatomy.md`
10. `.tools/eval_set/image_cases.yaml`
11. `.tools/eval_set/nsfw/visual_results.yaml`
12. `.tools/eval_set/render_exp/expansion/phase26_results.yaml`
13. `.tools/eval_set/render_exp/README.md`
