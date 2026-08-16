# AirPaint Build Handoff

> 交接日期：2026-08-16
> 当前分支：`main`
> 远程已推送到：`9bbd696 feat: complete Phase 2 prompt quality baseline`

## 1. 当前阶段与进度

### 当前阶段

项目处于 **Phase 2.6：Prompt Expansion 已完成，v5 增强已定稿并待推送**。

Phase 2 首轮已经完成并 push，但 Phase 2.5 尚未完成生产落地。

### 已完成模块

- Phase 1 Prompt IR：文本 LLM 输出 12 字段 `IR`、`TAGS`、`NL`，旧协议保留降级路径。
- `compile_prompt()`：角色裸名清理、tag 去重、`count → character → general` 排序、NL 拼接。
- `infer_render_profile()`：当前只对明确 NSFW、单主体、简单动作使用 `tag_first`；普通 SFW 保留 NL，复杂关系使用 `relation_hybrid`。
- `/api/translate`：增加 `prompt_ir`，前端旧 `breakdown` 契约保持兼容。
- 真实 Prompt Engine 回归链路：baseline runner 不再复刻局部逻辑，直接调用 `server.main.translate()`。
- Failure taxonomy：已覆盖 counting、entity binding、action/pose、interaction、spatial、lighting、NSFW anatomy、model artifact、semantic misread 等类型。
- NSFW 结构评测集：8 条明确成人内容，结构与 explicit safety 验证通过。
- Rendering Strategy 实验工具：固定 Prompt、seed、尺寸、workflow，支持盲评页、manifest、variant 对照。
- Dictionary vs LLM 对照工具与 girl/female 词汇对照工具。
- 用户可手动编辑英文 `prompt_en`，作为复杂动作/多角色失败的产品 fallback。

### 已验证项

- Phase 2 profile 收窄后，30 条真实链路回归：`30/30`，IR 完整 `30/30`。
- Prompt 单测：最近一次为 `10` 个通过。
- NSFW 结构验证：`8/8`，workflow safety 为 `explicit`。
- Failure taxonomy 聚合：`17 pass / 18 fail`，主要失败类型为 `interaction_relation`、`model_artifact`、`action_pose`、`anatomy_nsfw`。
- Phase 1.5 首轮：30 张图生成成功，用户完成盲评。
- Phase 1.5 R4 换 seed：5 张图生成成功，仍无法稳定表达“后撤”，已记录为 base Anima 多角色构图/动作绑定限制。
- Phase 2 W3 profile A/B：20 张图生成成功，用户完成盲评。
- Phase 2 W4 Dictionary vs LLM：20 张图生成成功，用户完成盲评。
- Phase 2 W6 girl/female：4 张图生成成功，用户认为无明显差异。
- Phase 2.5 E1-E7 扩写实验：14 张图生成成功，用户已完成详细评审。
- Phase 2.6 A1/A2/A3 三路实验：21 张图和 E1/E6/E7 换 seed 的 9 张补测图均生成成功；用户已完成两轮 A/B/C 盲评，A3 对 A2 为 4 胜、1 平、2 负；初版生产 A/B 被判定为协议退化，已改为独立 `IR + PROMPT` 画师协议并修复护栏；v5 新旧 A/B 为 3 胜 2 平 0 负，提示词增强保留。

## 2. 修改/新增文件清单

### 后端与测试

| 文件 | 当前作用 |
|---|---|
| `server/main.py` | Prompt IR 解析、`compile_prompt()`、`infer_render_profile()`、实验负面覆盖入口、原有 API/Workflow Engine |
| `.tools/test_prompt_unit.py` | 15 个零依赖 Prompt/IR/profile/画师协议/实验负面单测 |
| `.tools/eval_set/run_baseline.py` | 直接调用真实 `translate()`，输出 candidate，不覆盖 `baseline.yaml` |
| `.tools/eval_set/compare.py` | 比较结构不变量、IR 完整性、角色保护、count 排序、TAGS/NL 重叠告警 |
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
| `.tools/eval_set/render_exp/expansion/phase26_results.yaml` | 第一轮 A/B/C 盲评结果与 review key 解码 |
| `.tools/eval_set/nsfw/cases.yaml` | 8 条 NSFW 结构评测集 |
| `.tools/eval_set/nsfw/validate.py` | NSFW IR、成人声明和 explicit safety 校验 |
| `.tools/eval_set/nsfw/image_cases.yaml` | 6 条 NSFW 固定视觉基线夹具 |
| `.tools/eval_set/nsfw/visual_results.yaml` | N01-N06 用户视觉基线结果 |

### 文档

| 文件 | 当前作用 |
|---|---|
| `docs/PLAN-v5 — AirPaint Prompt Intelligence.md` | 已记录 Phase 1/1.5/2，当前新增 Phase 2.5 Prompt Expansion 方向 |
| `ROADMAP.md` | Phase 2 首轮完成，Phase 2.5 扩写实验进行中 |
| `docs/DEVLOG.md` | 第 31-38 条记录 Phase 1、Phase 1.5、Phase 2 和 Phase 2.6 收尾 |
| `docs/decisions.md` | D34 IR、D35 Rendering Strategy、D36 Phase 2、D37 Phase 2.6 最终结论 |
| `docs/architecture.md` | 当前 Prompt IR/Compiler、R4 多角色限制和手动 Prompt fallback |
| `docs/api.md` | `/api/translate` 的 `prompt_ir` 契约 |
| `docs/workflow-anatomy.md` | 用户维护的 AnimaFull 权威节点解剖；当前 HEAD 中已单独提交，后续必须优先参考 |

### 当前未提交文件

- `ROADMAP.md`、`docs/DEVLOG.md`、`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`：Phase 2.5 扩写实验启动状态的未提交文档修改。
- `.tools/eval_set/render_exp/expansion/`：E1-E7 扩写实验 README 和 cases，尚未 commit。
- `.tools/eval_set/render_exp/output/`、`.tools/eval_set/nsfw/output/`：实验生成物，已被 gitignore，不能当源码提交。
- `.opencode/`、`opencode.jsonc`：环境未跟踪文件，未修改、不可纳入提交。

## 3. 未完成项

### 立即下一步

1. Phase 2.6 已完成：保留提示词增强，冻结继续扩写规则。
2. 已完成 15 单测、30 条 SFW/8 条 NSFW 回归和 v5 新旧 A/B 人眼确认。
3. 后续先观察真实使用反馈，不立即建设详细输入辅助 UX 或新的扩写实验。

### 条件性未完成项

- Phase 2.6 文档和回归已完成，完成提交后 push。

### 已明确跳过

- R4 不再继续做自动特判；复杂多角色失败使用现有 Prompt 编辑 fallback。
- weighted spatial NL 不进入默认策略。
- semantic negative 不进入默认策略。
- girl/female 不改 canonical，当前 W6 无明显差异。
- 不写 PLAN-v6，先完成扩写证据。

## 4. 当前上下文遗留问题

- Phase 2 首轮验证的是 rendering/source 对照，不是 Prompt Expansion；这导致代码曾经更像 translator，而不是画师补全器。
- D13 的“氛围→场景扩写”精神在 D28 的 “TAGS only / 简单输入 NL 留空 / ONLY remaining input” 约束下明显减弱；Phase 2.5 要验证是否恢复。
- 12 字段 IR 当前主要记录用户/LLM 结果，尚未记录 `user` 与 `expanded` 来源。只有画师补全胜出才引入 `prompt_ir_meta` 或等价 additive 来源信息。
- E1-E7 两轮 A1/A2/A3 人工结论已记录；v5 生产 A/B 最终为 3 胜 2 平 0 负。
- A2 详细中文在部分 case 更强，说明输入信息量是质量上限；自动增强保留但不替代具体用户意图。
- NSFW 目标是“高质量二次元插画的色气感”，不是裸体 tag 堆砌；统一补全底层应使用构图、光影、氛围、材质，NSFW 只在服装状态、身体语言、揭示节奏上分流。
- R4 的失败不是当前 Phase 2 主线 blocker，但应继续作为 failure taxonomy 的 `interaction_relation` + `spatial_composition` + `model_artifact` 样本。
- `docs/decisions.md` 历史 D1-D36 必须保持完整；上一轮曾发生误覆盖，后续编辑只能追加 ADR，不能用 Add File 替换已有文件。

## 5. 验证命令与状态

### 当前最近验证

```text
python -m py_compile server/main.py .tools/test_prompt_unit.py .tools/eval_set/compare.py .tools/eval_set/nsfw/validate.py .tools/eval_set/render_exp/label.py .tools/eval_set/render_exp/run_profile_ab.py .tools/eval_set/render_exp/run_pair_experiment.py
```

状态：通过。

```text
python .tools/test_prompt_unit.py
```

状态：`10 prompt unit tests passed`。

```text
python .tools/eval_set/compare.py --candidate .tools/eval_set/candidate_phase2_profile_final.yaml --require-ir
```

状态：`30/30` case，IR `30/30`；TAGS/NL overlap 为 warning，不作硬失败。

```text
python .tools/eval_set/nsfw/validate.py .tools/eval_set/nsfw/candidate.yaml
```

状态：8 case `PASS`，explicit safety 通过。

```text
python .tools/eval_set/render_exp/label.py
```

状态：`17 pass / 18 fail`，taxonomy 标签有效；主要失败为 interaction、model artifact、action/pose、anatomy NSFW。

### Phase 2.5/2.6 已执行命令

```text
python .tools/eval_set/render_exp/run_experiment.py --cases .tools/eval_set/render_exp/expansion/cases.yaml --output .tools/eval_set/render_exp/output/expansion --expected-count 14
```

状态：14/14 图片生成成功。

```text
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode prepare
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode render
```

状态：固定 Prompt 21/21 图片生成成功；第一轮 A/B/C 盲评已记录到 `phase26_results.yaml`。

下一次执行三路实验时，复用 `run_experiment.py` 的固定 seed/manifest/review.html 机制，但需要新增 A1/A2/A3 夹具或专用 runner。

## 6. 注意事项/禁区

- 修改前必须先读 `AGENTS.md`、本文件、最新 PLAN-v5、D34-D36 和 `docs/workflow-anatomy.md`。
- `docs/workflow-anatomy.md` 是当前 AnimaFull 节点权威参考；节点 4 是负向 `ImpactWildcardProcessor`，节点 55 是负向 CLIP 编码，不要再按旧节点猜。
- 不要把实验用 `negative_text_node` / 节点 4 覆盖能力当成生产默认负面；config 默认没有 `negative_text_node`，D6 固定负面策略保持不变。
- 不要把 weighted NL、semantic negative、girl/female 替换、R4 特判、PromptState、LoRA context、自动优化 Agent 提前合并。
- 不要把“Prompt 更长”“IR 字段更满”“结构解析通过”当成图像质量证明；人眼是最终语义验收。
- 单张图的手脚/武器偶发错误先视为随机因素；只有换 seed 后仍重复才归入策略失败。
- 但分页、黑线、第三主体、场景关系丢失等重复出现的问题要进入 failure taxonomy，不能用“随机”掩盖。
- E6 不要强行写 `adult woman`/`female` 作为视觉正向描述；按用户确认使用自然 `woman/1girl`，同时保持成人内容和 safety 约束。
- base Anima 是当前唯一 checkpoint；不要引用其他 merge 的 score/artist 经验直接写成官方规则。
- `baseline.yaml` 是历史参考，不能覆盖；candidate 和实验 output 已忽略。
- 生产 API 端口可能仍运行旧后端进程，修改 `server/main.py` 后要重启后端再做在线 smoke。
- 当前工作区未跟踪 `.opencode/`、`opencode.jsonc` 不要 stage；用户维护的 workflow 文档变更不要回滚。

## 下一会话必读文件

按以下顺序读取：

1. `AGENTS.md`
2. `docs/BUILDHANDOFF.md`
3. `docs/PLAN-v5 — AirPaint Prompt Intelligence.md`
4. `ROADMAP.md`
5. `docs/decisions.md`（重点 D34-D36）
6. `docs/DEVLOG.md`（重点第 31-36 条）
7. `docs/architecture.md`
8. `docs/workflow-anatomy.md`
9. `.tools/eval_set/baseline.yaml`
10. `.tools/eval_set/render_exp/expansion/cases.yaml`
11. `.tools/eval_set/render_exp/README.md`
