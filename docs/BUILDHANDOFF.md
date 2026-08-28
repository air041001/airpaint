# AirPaint Build Handoff

> 交接日期：2026-08-28
> 当前分支：`main`
> 远程状态：Visual Composer、LoRA Context / Binding 与双主题三栏工作台均已实现和验收；实际提交以根仓库及 `web/` 子仓库各自的 `git log` / `git status` 为准

## 1. 当前阶段与进度

### 当前阶段

项目当前主线 **Phase 2.7 Visual Composer 已完成并通过定向生产验收**。LoRA Context / Binding 首版和 Composition 工程扩展（`docs/PLAN-LORA.md` v2）均已完成；多人画质仍是未验收边界。

Phase 2.6（Prompt Expansion）、Phase 2.7（Visual Composer）、Phase 3（Character Knowledge）与 PLAN-LORA Step 0-10 均已完成。生产文本不再受固定元素数或 TAG/NL 形态限制，用户可选择补全程度并在生成前编辑中文构思。网站保持现有有状态三栏工作台和纸本画室/石墨暗房双主题；Phase 4 PromptState 继续由真实暗房使用触发，Phase 8 Workflow Intelligence 长期保留但不自动启动。

### 已完成模块

- Phase 2.7 Visual Composer：SiliconFlow 普通文本严格输出 `CONCEPT + 12 字段 IR + [LORA] + PROMPT`；支持 `auto | faithful | free`，失败修复一次后 fail closed。
- 最终 Anima Prompt 可自由使用 tag、短句、自然语言或混合，不设固定元素数。ordinary `dict.yaml` 不再短路普通文本 Reasoning Model；Vision 与其他 legacy 路径保持兼容。
- 中文构思固定为 `用户锁定：…｜模型补全：…`，前端可编辑后以 `concept_override` 重编译；它是单轮控制面，不是 PromptState。
- `compile_prompt()` 与新 Composer 护栏：角色 canonical/裸名去重、主体计数、完整重复折叠、明确全身请求的互斥近景清理；新路径不再自动补裸体、年龄、rating、三分之四景别或删除画风。
- 定向可画性/身份护栏：模型补全不得把上半身近景与裙摆/髋腿交互或多个手部操作塞进同一画面；角色 LoRA 的 Profile 名不得推断发色/瞳色，只有用户明确改色可通过，越权时修复一次后 fail closed。
- `/api/translate/jobs/dialog`：贯通 `concept`、`completion_level`、`prompt_ir_meta` 与 LoRA binding snapshot，旧 `breakdown` 契约保持兼容。
- 结构性回归：由 51 个零依赖单测覆盖 Composer 协议、Prompt/角色知识、LoRA Registry/Binding、API/session 与 workflow，不依赖未验证 baseline。
- Failure taxonomy：已覆盖 counting、entity binding、action/pose、interaction、spatial、lighting、NSFW anatomy、model artifact、semantic misread 等类型。
- NSFW 结构评测集：8 条明确成人内容，结构与 explicit safety 验证通过。
- Rendering Strategy 实验工具：固定 Prompt、seed、尺寸、workflow，支持盲评页、manifest、variant 对照。
- Dictionary vs LLM 对照工具与 girl/female 词汇对照工具。
- 用户可手动编辑英文 `prompt_en`，作为复杂动作/多角色失败的产品 fallback。
- `AnimaFull.json` 固定 negative 增加常见手指、手臂、腿脚畸形词；这是低成本防御，不是人体质量保证。
- LoRA Context / Binding：versioned Registry/Profile、last-good revision、legacy adapter、Selection Resolver、幂等 exact Binding Compiler。
- active LoRA 在翻译前进入 Reasoning/Vision Model 上下文；translate/jobs/dialog/start-image 共用 binding snapshot/revision。
- LoRA Composition：角色最多 3 个语义 Profile，风格/动作/表情 LoRA 不设硬上限；同一 Asset 多 Profile 需 Registry opt-in 并只加载一次物理文件，每个 Asset 使用独立 0~2 强度。
- 前端 Profile auto/显式锁定、连续多选与当前叠加栈、per-asset 强度、provides/verified、切换后 stale Prompt 防串线。
- onboarding：双击 `.tools/start_lora_onboard_agent.bat` 或运行 `.tools/register_lora.py --agent`，粘贴作者说明后由 Reasoning Model 生成可修订候选；代码恢复 exact trigger/明确单值强度，双重确认后原子更新 Registry。`--inspect/--civitai/--validate` 继续可用，不自动把 HTML/Civitai trainedWords 升格为正式知识。
- 旧 `scan_loras()` / 启动时 Civitai hash lookup / `server/lora_cache.json` / `POST /api/loras/refresh` 已由上述 onboarding 流程取代并退役；`config.yaml.loras` 仍有未迁移资产，只能保留兼容读取，不能直接删除。

### 已验证项

- Phase 2 profile 收窄后，结构回归由单测覆盖（历史 30/30 仅记录，baseline 已清理）。
- Prompt/LoRA 单测：最近一次为 `51` 个通过（旧 scanner 退役后删除其 2 项专用测试）。
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
- 新增 `si_arknights_v2` candidate：作者样图与 safetensors 内嵌训练元数据共同确认 `si_(arknights)` 主触发词及基础外观/白裙标签，已登记为单一 `base` Profile；坐姿、蝴蝶、竹林与作者组合使用的其他 LoRA 不纳入绑定。尚未完成人眼验收，不得提升为 verified。
- 尺寸路由纠错后，`1024x1536` 在 RTX 4060 Laptop 8GB、无 detailer 下实际输出 1024×1536，端到端 84.16 秒；此前 309.72 秒任务实际误走 `input2` 并输出 832×1216，不是高分辨率性能结论。前端现从成品 `naturalWidth/naturalHeight` 显示真实像素。
- 生产前端已迁移为状态驱动的三栏工作台：描述跨栏、Prompt 左、成图中、参数右、历史下方；首次翻译使用独立检查态，有图后重翻译保持图片不动。纸本/石墨只改变材质与配色，不改变标题标记语义；LoRA 选中 Profile 在两主题均有明确对比度，角色/风格菜单会按右栏上下空间自动翻转。
- “生成”会静默调用翻译并把返回的英文 Prompt/五项拆解同步到左栏，再提交任务；不再只有“先看翻译”路径更新 UI。成图舞台脱离 `flex-1` 的固有尺寸反撑，按视口高度在桌面连续缩放并始终使用 contain，896×1152 等竖图不会再把画布撑成 1152px 高。
- Visual Composer 真实 SiliconFlow 定向 smoke 覆盖普通 auto、编辑构思重编译与角色+画风 LoRA 上下文；三次均返回完整 12 字段 IR、构思与正确 binding。
- Remielle Dan `black` + Fymrie 的真实失败复测已通过：Profile 名不再产生未请求的 `black hair/red eyes`，IR/PROMPT 均无越权颜色且 binding 无 warning；1024×1024 构图修复图由用户确认没有问题。
- Composition 结构验证：新增 6 项测试覆盖同文件多 Profile 单 binding/单加载、角色按 Profile 计数上限 3、Registry opt-in、6 个风格叠加、冲突强度拒绝和 snapshot round-trip；旧 scanner 退役后的 51 项 Prompt/LoRA 回归继续通过。真实 Registry payload 为 3 个角色 Profile + 4 个风格生成 6 个 binding/6 个唯一 Loader 文件。
- Composition 浏览器验证：桌面与 390px 下完成同文件双 Profile、跨文件角色组合、第四角色拦截、4 个风格连续多选、逐 Asset 强度与纸本/石墨主题；移动端无横向溢出。该验证不证明多人生成质量。
- 前端 mock 已覆盖三档传参、构思 dirty 阻断/重新应用、原文/LoRA stale 防串线、job 的 `concept/completion_level`、1365×720 与 390×844；console 无错误。
- 正常二次元插画 `d709b7a58fc9.png` 和角色+画风 LoRA 插画 `695cf21fe007.png` 已由用户确认可接入生产。该人眼结果是当前画质证据；49 项单测与 smoke 只证明结构和链路。

## 2. 修改/新增文件清单

### 后端与测试

| 文件 | 当前作用 |
|---|---|
| `server/main.py` | Prompt/Intent/Workflow Engine + LoRA Registry/Scanner/Resolver/Binding/API/session |
| `server/lora_registry.yaml` | versioned LoRA Asset/Profile/trigger/provides/default strength 人工知识 |
| `.tools/test_prompt_unit.py` | 51 个零依赖 Composer/Prompt/角色知识/LoRA Registry/Binding/API/session/workflow 单测 |
| `.tools/test_lora_composition.py` | 6 个 LoRA 多 Profile/多 Asset/强度/物理文件去重确定性测试 |
| `server/workflows/AnimaFull.json` | 当前统一 Anima 工作流；固定 negative 含质量、构图及紧凑的人体防御词 |
| `.tools/register_lora.py` | LoRA sidecar inspection、Registry validate 与原子 onboarding |
| `.tools/start_lora_onboard_agent.bat` | 双击启动本地 LoRA 入库 Agent；API key 只从 gitignored config 读取 |
| `.tools/test_lora_onboard_agent.py` | 14 个 onboarding JSON/schema/exact trigger/strength/Civitai/Manager 目标验收/本地 metadata 确定性测试 |
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
| `ROADMAP.md` | Phase 2.7/3 与 LoRA Composition 工程链完成；Phase 4/8 及多人画质验证条件触发 |
| `docs/DEVLOG.md` | 第 31-55 条记录 Phase 1、LoRA Context、双主题迁移、Visual Composer、定向生产修复与 Composition 扩展 |
| `docs/decisions.md` | D39 为 LoRA Binding，D45 为三栏工作台，D46 为 Visual Composer，D47 为定向护栏，D48 为 LoRA Composition 计数/去重边界 |
| `docs/architecture.md` | 当前 Prompt/LoRA Registry/Binding/Workflow/Frontend 架构与边界 |
| `docs/api.md` | translate/jobs/dialog 的 selection/binding/revision 契约 |
| `docs/workflow-anatomy.md` | 用户维护的 AnimaFull 权威节点解剖；当前 HEAD 中已单独提交，后续必须优先参考 |

### 当前工作区

- `web/` 是独立 Git 仓库。Visual Composer 三档补全、可编辑构思与 stale 状态闭环为 `10615ae`；此前双主题迁移/修复为 `e429c91`、`95fa803`、`99bb15c`，均已推送 `origin/main`。
- 根仓库当前 Visual Composer 实现、测试、工作流与文档位于同一次阶段提交，具体 hash 以 `git log` 为准。
- `docs/PLAN-v4` 与 `docs/inspiration.md` 已删除（历史参考不再保留）；`web/index.html.bak2` 已删（web/ 子仓库）。
- `.tools/eval_set/render_exp/output/`、`.tools/eval_set/nsfw/output/`：实验生成物，已被 gitignore，不能当源码提交。
- `server/lora_registry.yaml` 当前有用户自有未提交修改；`.opencode/`、`opencode.jsonc` 与 `docs/PROMPT-TRANSLATION-HANDOFF.md` 是用户/环境未跟踪文件。它们均未由本次前端修复修改，后续不要误 stage、回滚或覆盖。

## 3. 未完成项

### 立即下一步

1. 没有自动开启的新大阶段；Visual Composer 已进入生产。下一会话应通过日常真实出图收集可复现失败，记录“输入 → 构思 → Prompt → LoRA/参数 → 图片表现”，只做定向修订；不要恢复无目标的大批量 A/B，也不要用更长 Prompt 或更满 IR 代替图片判断。
2. 新下载 LoRA 推荐双击 `.tools/start_lora_onboard_agent.bat`：选择目标文件后，工具会调用 LoRA Manager 增量 scan，并通过 `/api/lm/loras/list` 验证该文件确实已解析为完整路径；验证通过后才接收作者说明和调用 Reasoning Model。输入 `revise` 可自然语言修订，只有 `write` + 最终 `y` 才写 Registry，真实生图后再提升 verified。
3. 若 ComfyUI 未运行、scan 被取消、返回格式异常或目标文件仍未入列表，Agent 会在调用 LLM/写 Registry 前停止。增量未命中时可由用户明确选择一次全量重建；默认不自动承担大文件哈希成本。只有明确需要离线准备 Registry 时才使用 `--no-manager-scan` 绕过运行时索引验收。
4. `si_arknights_v2` 首次两次生成在 ComfyUI 节点 5 报 `ModelMMAP allocation failed for si_(arknights)-v2.safetensors`。根因是文件尚未进入 LoRA Manager SQLite 索引：`get_lora_info_absolute()` 查找失败后把原始相对文件名交给 Aimdo，因而 mmap 打不开；这与缺 trigger、文件损坏或显存不足无关。用户手动访问 `/api/lm/loras/scan` 后，Manager 已生成 sidecar 并在 `/api/lm/loras/list` 返回完整绝对路径；修订后的入库 Agent 已用该文件完成真实增量扫描与目标命中 smoke。

### 条件性未完成项

- 无当前阻塞开发项。不得退回“LLM 复制 trigger”或“生成时盲拼 trainedWords”的旧边界。

### 已明确跳过

- R4 不再继续做自动特判；复杂多角色失败使用现有 Prompt 编辑 fallback。
- weighted spatial NL 不进入默认策略。
- semantic negative 不进入默认策略。
- girl/female 不改 canonical，当前 W6 无明显差异。
- 不写 PLAN-v6；Phase 4 PromptState 继续延后（暗房使用数据触发）。
- PLAN-LORA 明确不做：自动解析 HTML description / LoRA 冲突 ML 检测 / LLM 决定 trigger 权重 / Phase 8 Workflow Intelligence；组合链路完成也不等于多人画质完成。

## 4. 当前上下文遗留问题

- Phase 2 首轮只验证 rendering/source 对照；Phase 2.7 已进一步移除固定元素数、固定 TAG/NL 形态与 ordinary dict 全命中短路，当前生产目标是 Visual Composer，不再是 translator。
- `prompt_ir_meta` 以 additive 方式标注 Visual Composer、补全档、构思覆盖、重复折叠和 reroll 方案；不改变 12 字段 IR 结构。
- E1-E7 两轮 A1/A2/A3 人工结论已记录；v5 生产 A/B 最终为 3 胜 2 平 0 负。
- A2 详细中文在部分 case 更强，说明输入信息量是质量上限；自动增强保留但不替代具体用户意图。
- NSFW 目标是“高质量二次元插画的色气感”，不是裸体 tag 堆砌；统一补全底层应使用构图、光影、氛围、材质，NSFW 只在服装状态、身体语言、揭示节奏上分流。
- R4 的失败不是当前 Phase 2 主线 blocker，但应继续作为 failure taxonomy 的 `interaction_relation` + `spatial_composition` + `model_artifact` 样本。
- `docs/decisions.md` 历史 D1-D38 必须保持完整（D30-D33 已重排、D33 已补全）；编辑只能追加 ADR，不能用 Add File 替换已有文件。
- LoRA Context 已解决原“先翻译、后盲拼”的状态割裂；真实 A/B 没有场景退化。Composition 已支持同/跨文件的角色与风格叠加，但尚无固定条件多人图的人眼证据，不能从 schema、Loader 或浏览器通过推导为质量完成。
- 当前前端的产品语义为 `compose → review → result → darkroom`：首次无图不显示空画布；首次查看翻译进入 review；“生成”静默翻译后直接提交，但仍必须把本次英文 Prompt/拆解写入结果左栏；已有结果后再次翻译只更新左侧 Prompt，图片保持原位。日夜主题必须共享相同组件几何和交互，只允许材质、颜色、阴影等视觉 token 不同。
- 三档补全和中文构思已经是这个状态机的一部分。原文、补全档、构思、LoRA/Profile 或参考图版本变化后，旧翻译必须 stale；编辑构思未重新应用时不得提交旧 Prompt。

## 5. 验证命令与状态

### 当前最近验证

```text
python -m py_compile server/main.py
python .tools/test_prompt_unit.py
python .tools/test_lora_composition.py
python .tools/register_lora.py --validate
```

状态：Python 编译通过；`51 prompt unit tests passed`、`6 lora composition tests passed`、`14 lora onboarding agent tests passed`；当前本机用户维护、未纳入本次清理提交的 Registry 为 `registry valid: 15 assets`。`si_(arknights)-v2.safetensors` 的真实 Manager 增量扫描/列表命中 smoke 通过。`server/workflows/AnimaFull.json` 可正常解析，正负两个 wildcard 字段包含相同人体防御词。

```text
python .tools/test_lora_onboard_agent.py
python .tools/register_lora.py --validate
```

状态（既有 LoRA 工程验证）：`9 lora onboarding agent tests passed`。当前 Registry 数量以后续 `--validate` 实际输出为准。

前端当前验证：2 个内联 script 语法通过；Composition 浏览器 smoke 完成桌面与 390px：同 Asset 多 Profile、跨 Asset 角色、3 角色上限、4 风格连续多选、逐 Asset 强度、双主题和无横向溢出均符合契约；除 Tailwind CDN 既有提示外无 JS 错误。既有三档构思、stale 阻断、job snapshot、1920×950/1080 与竖图 contain 验收继续有效。

Reasoning Model 当前验证：既有 3 条 SiliconFlow smoke 覆盖普通 auto、`concept_override` 重编译、角色+画风 LoRA context；2026-08-28 追加 Remielle `black` Profile 身份越权复测，最终 IR/PROMPT 均无未请求发色/瞳色。没有重启批量图片 A/B。

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
- 不要把实验用 `negative_text_node` / 节点 4 覆盖能力当成动态 semantic negative；config 默认没有 `negative_text_node`。生产仍使用工作流固定负面，只由 D46 增加紧凑人体防御项。
- **LoRA Context / Binding / Composition 工程链已经完成**。维护时保持 versioned Registry/Profile、LoRA-aware text/vision、确定性 Binding Compiler、translate/jobs/dialog snapshot、角色上限按语义 Profile 计数和物理文件单次加载边界；不要让 LLM 决定文件、强度或 exact trigger。
- 不要把 weighted NL、semantic negative、girl/female 替换、R4 特判、PromptState、自动优化 Agent 提前合并。
- 不要把“Prompt 更长”“IR 字段更满”“结构解析通过”当成图像质量证明；人眼是最终语义验收。
- 单张图的手脚/武器偶发错误先视为随机因素；只有换 seed 后仍重复才归入策略失败。
- 但分页、黑线、第三主体、场景关系丢失等重复出现的问题要进入 failure taxonomy，不能用“随机”掩盖。
- 不要强行写 `adult woman`/`female` 作为视觉正向描述，也不要因本地 NSFW 意图自动堆裸体词或 rating。模型应忠实保留用户明确内容，构图与人体可读性使用同一 Anima 表达基础。
- base Anima 是当前唯一 checkpoint；不要引用其他 merge 的 score/artist 经验直接写成官方规则。
- `baseline.yaml` / `cases.yaml` / `run_baseline.py` / `compare.py` 已删除（未验证的 agent 产物，曾误导注意力）；结构性不变量由单测覆盖，图像质量只由人眼确认。
- 生产 API 端口可能仍运行旧后端进程，修改 `server/main.py` 后要重启后端再做在线 smoke。
- 当前根工作区的 `server/lora_registry.yaml` 用户修改，以及未跟踪 `.opencode/`、`opencode.jsonc`、`docs/PROMPT-TRANSLATION-HANDOFF.md` 都不要误 stage 或回滚；用户维护的 workflow 文档变更同样不要回滚。

## 下一会话必读文件

先固定读取：

1. `AGENTS.md`
2. `docs/BUILDHANDOFF.md`

再按任务路由补读，不要全量加载：

- **前端细节**：`web/index.html` 相关范围、`docs/decisions.md` D45/D46、`docs/DEVLOG.md` 第 48-51 条、`docs/architecture.md` 的 Frontend 段。
- **LoRA 维护/扩展**：`docs/PLAN-LORA.md`、`docs/decisions.md` D39、`docs/api.md` LoRA 契约；新增资产优先走 `.tools/register_lora.py --agent`。
- **Prompt Intelligence**：`docs/PLAN-v5 — AirPaint Prompt Intelligence.md`、`ROADMAP.md`、相关 ADR；只有要复核旧实验结论时才读对应 `phase26_results.yaml` / `visual_results.yaml`。
- **Workflow/ComfyUI 注入**：`docs/workflow-anatomy.md`、目标 workflow JSON、相关 custom node 源码与 `server/main.py` 注入路径。
