# AirPaint Build Handoff

> 交接日期：2026-08-22
> 当前分支：`main`
> 远程状态：PLAN-LORA v2 规划更新应与 `origin/main` 同步；实际提交以 `git log` 为准

## 1. 当前阶段与进度

### 当前阶段

项目处于 **最终任务：LoRA Context / Binding 工程（`docs/PLAN-LORA.md` v2）待执行**。

Phase 2.6（Prompt Expansion 生产协议）、Phase 3（Character Knowledge 自动缓存）均已完成并 push。Phase 4 PromptState 继续延后。当前唯一主线是 PLAN-LORA v2：选中的 LoRA/Profile 在翻译前进入模型上下文，LLM 负责语义选择，代码通过 Binding Compiler 确定性编译 exact trigger；按 Step 0-10 顺序执行。

### 已完成模块

- Phase 1/2.6 Prompt IR：生产文本 LLM 输出 12 字段 `IR` + `PROMPT`，旧 `TAGS/NL` 和 5 字段协议保留降级解析。
- `compile_prompt()`：角色裸名清理、tag 去重、`count → character → general` 排序、NL 拼接。
- `infer_render_profile()`：当前只对明确 NSFW、单主体、简单动作使用 `tag_first`；普通 SFW 保留 NL，复杂关系使用 `relation_hybrid`。
- `/api/translate`：增加 `prompt_ir` 和 additive `prompt_ir_meta`，前端旧 `breakdown` 契约保持兼容。
- 结构性回归：由 23 个零依赖单测覆盖（char_dict 命中/裸名剥离/排序/safety marker/角色 lookup），不依赖 30 条未验证 baseline。
- Failure taxonomy：已覆盖 counting、entity binding、action/pose、interaction、spatial、lighting、NSFW anatomy、model artifact、semantic misread 等类型。
- NSFW 结构评测集：8 条明确成人内容，结构与 explicit safety 验证通过。
- Rendering Strategy 实验工具：固定 Prompt、seed、尺寸、workflow，支持盲评页、manifest、variant 对照。
- Dictionary vs LLM 对照工具与 girl/female 词汇对照工具。
- 用户可手动编辑英文 `prompt_en`，作为复杂动作/多角色失败的产品 fallback。

### 已验证项

- Phase 2 profile 收窄后，结构回归由单测覆盖（历史 30/30 仅记录，baseline 已清理）。
- Prompt 单测：最近一次为 `23` 个通过。
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
- 未验证的 baseline 回归资产已清理（删除 `baseline.yaml`/`cases.yaml`/`run_baseline.py`/`compare.py`，DEVLOG 40）；结构不变量由 23 个单测确定性覆盖。
- 最终任务规划已按用户确认重写为 v2：`docs/PLAN-LORA.md`（LoRA Asset/Profile、versioned registry、Binding Compiler、LoRA-aware text/vision、binding revision、前端/暗房状态与真实 A/B）；D39 记录“LLM 选语义、代码编译 exact trigger”。

## 2. 修改/新增文件清单

### 后端与测试

| 文件 | 当前作用 |
|---|---|
| `server/main.py` | Prompt IR 解析、`compile_prompt()`、`infer_render_profile()`、实验负面覆盖入口、原有 API/Workflow Engine |
| `.tools/test_prompt_unit.py` | 23 个零依赖 Prompt/IR/profile/画师协议/角色 lookup/实验负面单测 |
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
| `docs/PLAN-LORA.md` | **最终任务执行蓝本 v2**：LoRA Context / Binding 工程（Step 0-10） |
| `ROADMAP.md` | Phase 2.6/3 完成，Phase 4 延后，最终任务为 LoRA 工程 |
| `docs/DEVLOG.md` | 第 31-41 条记录 Phase 1 至 Phase 3、baseline 清理与 PLAN-LORA v2 |
| `docs/decisions.md` | D30-D33 已重排补全，D34-D38 历史结论，D39 为当前 LoRA Binding 决策 |
| `docs/architecture.md` | 当前 Prompt IR/Compiler、R4 多角色限制和手动 Prompt fallback |
| `docs/api.md` | `/api/translate` 的 `prompt_ir`/`prompt_ir_meta` 契约 |
| `docs/workflow-anatomy.md` | 用户维护的 AnimaFull 权威节点解剖；当前 HEAD 中已单独提交，后续必须优先参考 |

### 当前工作区

- Phase 2.6、Phase 3、baseline 清理、D33 重排补全均已提交并推送；PLAN-LORA v2、D39 与本次文档同步组成独立 planning commit，交接时应已 push。
- `docs/PLAN-v4` 与 `docs/inspiration.md` 已删除（历史参考不再保留）；`web/index.html.bak2` 已删（web/ 子仓库）。
- `.tools/eval_set/render_exp/output/`、`.tools/eval_set/nsfw/output/`：实验生成物，已被 gitignore，不能当源码提交。
- `.opencode/`、`opencode.jsonc`：环境未跟踪文件，未修改、不可纳入提交。

## 3. 未完成项

### 立即下一步

1. 按 `docs/PLAN-LORA.md` Step 0 开始资产/cache 审计和迁移映射；不要再把 deepseek_maid 误判为“未扫描”，实际是 cache 中 `type=unknown` 后被 API 隐藏。
2. 依次执行 Step 1-10：Registry/loader → scanner → legacy adapter → Binding Compiler → LoRA-aware text/vision → API/session → 前端 → onboarding → 真实 A/B → 文档 push。
3. 用户已确认核心硬约束：选中的 LoRA 必须在翻译前进入模型上下文，Prompt 与 LoRA 不割裂、不串人物/服装/风格；exact trigger、文件和权重由代码确定。

### 条件性未完成项

- 无当前阻塞开发项；PLAN-LORA v2 核心边界已确认。实现细节如函数命名可按代码调整，但不得退回“LLM 复制 trigger”或“生成时盲拼”的旧边界。

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
- LoRA 工程的核心痛点（PLAN-LORA §0）：全 trigger 盲拼导致 Prompt 与 LoRA 语义割裂并挤压场景空间。v2 解法是 LLM 看见 `provides/Profile` 后规划剩余画面，代码用 Binding Compiler 编译 registry 中的 minimal exact tags。

## 5. 验证命令与状态

### 当前最近验证

```text
python -m py_compile server/main.py .tools/test_prompt_unit.py .tools/eval_set/nsfw/validate.py .tools/eval_set/render_exp/label.py .tools/eval_set/render_exp/run_experiment.py .tools/eval_set/render_exp/expansion/run_phase26.py .tools/eval_set/render_exp/expansion/run_production_ab.py
```

状态：通过。

```text
python .tools/test_prompt_unit.py
```

状态：`23 prompt unit tests passed`。

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
- **LoRA Context / Binding 是当前最终任务目标，不是禁区**。按 PLAN-LORA v2 实现 versioned Registry/Profile、LoRA-aware text/vision、确定性 Binding Compiler 和 translate/jobs/dialog binding snapshot；不要退回让 LLM 逐字复制 trigger 的旧计划。
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
6. `docs/decisions.md`（重点 D34-D39；当前工程先看 D39）
7. `docs/DEVLOG.md`（重点第 31-41 条；当前工程先看第 41 条）
8. `docs/architecture.md`
9. `docs/workflow-anatomy.md`
10. `.tools/eval_set/image_cases.yaml`
11. `.tools/eval_set/nsfw/visual_results.yaml`
12. `.tools/eval_set/render_exp/expansion/phase26_results.yaml`
13. `.tools/eval_set/render_exp/README.md`
