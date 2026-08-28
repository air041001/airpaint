# AGENTS.md — AirPaint 项目开发规约

> 项目级 Agent 行为准则。不属于任何特定 coding agent。无论 Codex / Claude Code / Gemini CLI / 人工开发，都应遵守。
>
> 与具体文档的关系：
> - 本文件：怎么开发（稳定原则 + 当前仓库规约）
> - `docs/PLAN-v5`：未来路线（具体 Phase 任务）
> - `docs/architecture.md`：现在系统是什么样
> - `docs/decisions.md`：为什么这么设计
> - `docs/DEVLOG.md`：项目如何演变
> - `ROADMAP.md`：未来准备做什么（当前主线 = PLAN-v5）

---

#### 1. 项目核心方向

AirPaint 不是 ComfyUI 替代品。ComfyUI 是底层生成执行器，AirPaint 是其上层的 **Prompt / Intent / Knowledge Intelligence Layer**。

长期目标：让已经理解 ComfyUI / Prompt / LoRA 的用户，在 Prompt 编写、理解、迭代和知识积累上比直接使用 ComfyUI 更高效。

**核心优先级**：

```text
1. Prompt Intelligence
2. Prompt Knowledge
3. LoRA Intelligence
4. Workflow Intelligence
```

不要为了 Agent 化而 Agent 化。不要为了显得高级而提前引入：大规模 LoRA 推荐系统、embedding/vector DB、LoRA marketplace、社交系统、用户画像、大规模云端服务、自训练基础模型。

---

#### 2. 当前主要产品目标

### Prompt Intelligence

第一目标：把用户自然语言中的画面意图，编译成更适合当前模型（主要为 Anima）的高质量 Prompt。

重点逐步处理：subject / appearance / clothing / action / pose / interaction / scene / composition / lighting / mood / style / constraints，以及 TAG/NL 分流、canonical tag、排序、去重、局部修改、模型特定规则。

### Prompt Knowledge

现有 `char_dict.yaml` / `dict.yaml` 是历史知识资产，但不要假设它们永远比 LLM 更好。

长期：`本地知识 + LLM candidate + 外部知识 + 实际验证 → 更可靠的 Prompt Knowledge`。

角色知识应逐渐支持 alias / series / canonical_tag / source / confidence / candidate/verified；但**外部检索结果不得未经验证直接污染正式知识库**。

### LoRA Intelligence

LoRA 是扩展层，不是当前产品主线。

正确抽象：LoRA 提供 Anima 本身没有的角色、画风或特殊概念；Prompt 描述用户希望这个概念如何出现。

未来解决：LoRA context / Prompt/LoRA 语义分工 / trigger strategy / concept conflict / 多 LoRA composition。当前 LoRA 数量有限，不要提前建设庞大推荐基础设施。

---

#### 3. LLM 架构原则

当前实际方向：
- **Reasoning / Intent**：DeepSeek-V4-Flash
- **Reference Image / Vision**：Qwen3-VL（Qwen3-VL-Instruct 系列）

架构上**不要把模型名写死成永久设计**，应抽象为 Reasoning Model 与 Vision Model，未来可替换。

### 最重要原则

> **LLM 是大脑，代码是脊髓。**

- LLM 负责：意图、语义、关系、偏好、规划
- 代码负责：canonical tag、knowledge lookup、LoRA 实体、文件名、实际数值、prompt ordering、validation、workflow injection
- **不要让 LLM 直接决定不可验证的文件名、节点 ID 或任意数值**

---

#### 4. Prompt IR 原则

Prompt IR 是下一阶段最重要的新抽象之一。

建议从最小可用字段开始，**不要为了结构漂亮一次性堆很多字段**。

当前候选（12 字段）：

```text
subject / appearance / clothing / action / pose / interaction
scene / composition / lighting / mood / style / constraints
```

字段职责明确：

| 字段 | 职责 |
|---|---|
| subject | 主体计数 + 角色 |
| appearance | 发色/瞳/体型等离散属性 |
| clothing | 服饰 |
| action | **单角色**动作 |
| pose | **单角色**姿态 |
| interaction | **多角色**互动与空间关系（明确保留：action=自己做什么 / interaction=与他人/物关系） |
| scene / composition / lighting / mood / style | 环境/构图/光影/氛围/风格 |
| constraints | 用户约束（锁定项/否定项，negative_intent 延后） |

Prompt IR 不是最终 Prompt：

```text
User Intent → Prompt IR → Knowledge Resolution → TAG + NL → Prompt Compiler → Final Model Prompt
```

---

#### 5. TAG vs Natural Language

不要把目标定义为"把所有内容都翻译成 tag"。

- **TAG 适合**：角色、发色/发型、常见服饰、常见物体、标准化属性、常见风格/构图
- **NL 适合**：复杂动作、姿态关系、多人物互动、空间关系、连续行为、难以稳定压缩成 canonical tag 的语义

**原则：什么表达方式最有利于当前模型正确理解，就使用什么表达方式。**

**Hard Rule**：TAGS 和 NL 不重复——每条信息只出现在一种形式。NL 不得复述 TAGS 已有 tag，全是 tag 则 NL 留空。

---

#### 6. Dictionary vs LLM

不要永久规定 dictionary > LLM，也不要永久规定 LLM > dictionary。

根据**语义类型和实际效果**形成策略：

- 固定 canonical 属性 → dictionary 可能更可靠
- 新颖/长句/复杂关系 → LLM 可能更强
- 角色 canonical tag → 专门知识库或外部验证更合适

后续建立小型 Prompt 回归集（不是学术 benchmark，是防止凭感觉越改越差）。

---

#### 7. Character Knowledge

角色自动发现：

```text
识别人物 → 本地 char_dict 查询 → 命中 → 稳定 canonical tag
未命中 → 外部检索 → candidate → 验证 → knowledge cache → 稳定后 promote 正式知识
```

**不要让联网结果直接覆盖正式 `char_dict.yaml`。**

角色信息演进方向：alias / series / canonical_tag / source / confidence / verified。但修改数据结构前**必须检查当前 `HotDict`、`match_characters()` 等兼容性，不得凭空重构**（当前 `HotDict` L48 `str(v).strip()` 扁平化是结构化的硬障碍）。

---

#### 8. PromptState / 增量修改（暂不实现）

未来 Prompt 应从字符串升级为结构化状态，支持"只换衣服""动作不变只改光""保留角色""保持构图""换画风"等局部修改。

最终可类似：

```text
PromptState
├── semantic_ir
├── compiled_prompt
├── knowledge_used
├── lora_context
├── locked_fields
├── user_overrides
└── history
```

**这是较大的架构重构，不要在 Prompt IR 尚未验证之前提前实现。**

---

#### 9. LoRA 规则

当前代码已有：versioned LoRA Registry / onboarding + LoRA Manager 目标索引验收 / 多 LoRA 注入 / Binding Compiler；`config.yaml.loras` 仅为未迁移兼容层。

当前 exact trigger 编译注入仍是兼容边界，不是最终架构。

未来方向：

```text
LoRA → 它已经提供了什么概念？ → Prompt 还需要描述什么？
```

而不是：

```text
完整 trigger + 完整 Prompt
```

**Trigger Strategy 不要预设固定前 N 个 tag 或固定 N token**。不同 LoRA 训练方式和概念绑定不同。未来支持 full / minimal / semantic / manual，并通过小规模真实测试确定策略。

---

#### 10. 开发顺序

不要把所有长期 Phase 一次性铺开。

当前 PLAN-v5（详见 `docs/PLAN-v5 — AirPaint Prompt Intelligence.md`）：

```text
Phase 0  Prompt Baseline / 小型回归集
↓
Phase 1  Prompt IR + Prompt Compiler
↓
Phase 2  Prompt Quality（TAG/NL、Dictionary vs LLM、canonicalization / ordering / dedup）
↓
Phase 3  Character Knowledge 自动成长
↓
Phase 4  PromptState + Incremental Editing
↓
Phase 5  LoRA Context
↓
Phase 6  Trigger Engine
↓
Phase 7  LoRA Composition
↓
Phase 8  Workflow Intelligence
```

**基础维护项不是独立 Phase**：旧 `scan_loras` 的历史修复与退役 / 清理明显错误的 `char_dict` / 同步旧文档中的模型名称等，应在需要时完成，**不要让基础维护抢占 Prompt Intelligence 主线**。

---

#### 11. 开发前先读上下文

**禁止凭印象修改。**

```text
先定位 → 读相关代码 → 读对应文档 → 理解现有行为 → 再修改 → 验证 → 更新文档
```

- 优先 `Grep` 定位符号，再读局部范围
- 修改模块时检查调用方和下游影响
- 对话上下文（CLAUDE.md / 当前对话）里的描述**可能滞后或漏读**，以代码为准

---

#### 12. ComfyUI 节点注入铁律

修改 workflow 注入前**不得靠猜**：

1. 根据 workflow JSON 找 `class_type`
2. 找本机 custom node 源码
3. 阅读 `INPUT_TYPES` / `execute()`
4. 确认输入是字面值 / 连接 `[node_id, output_index]` / `{"__value__": ...}` widget
5. 检查写入 input 是否会覆盖连接（覆盖连接会切断上游输出）
6. 本地打印最终 workflow / inputs
7. 再端到端跑 ComfyUI

特别注意：set input 覆盖原连接会切断上游输出。AirPaint 过去的 LoRA / Widget / Trigger 问题已经证明这一点。

---

#### 13. 测试要求

每次有意义的代码修改，运行与修改范围匹配的最低验证。

```bash
python -m py_compile server/main.py    # Python
node --check web/index.html            # 前端（如果改了前端）
```

验证维度（按修改范围选）：
- **Prompt Engine**：快速全命中 / LLM 路径 / known tags / unknown-miss / reroll / 角色识别 / TAG-NL / Prompt normalization
- **Workflow**：txt2img / img2img / detailer / LoRA 路径
- **多角色**：固定 Prompt+seed 生图人眼验收（避免 split view 等模型局限未发现）

> **结构性测试 ≠ 质量结论。** 单测（IR 解析、rating tag 手动透传、不崩溃、char 命中）是廉价预检；图像质量只由人眼对生成图确认。不以"N/N IR 完整"或"结构通过"作为阶段验收门槛。历史未验证的 30 条 baseline 已清理（见 DEVLOG），不要重建"跑批量、比结构"作为质量门。

**不要只"代码改成功"就视为完成。** 阶段完成 = 验证通过 + 文档同步 + push。

---

#### 14. Push 触发条件（用户明确要求）

> **一个阶段（Phase / Decision / 重大 Bug 修复）完成后，验收通过即 push。**

具体规则：

1. **阶段完成触发 push**：每个开发阶段（Phase / Decision / 重大 Bug 修复）完成后，跑完验证（py_compile / 端到端 / 文档同步），即可 push 到 origin/main，**不用等用户额外指示**
2. **验证标准**：至少跑 `py_compile` + 文档同步检查 + （如涉及 Prompt Engine）跑 baseline 对比格式退化
3. **commit message**：一句话标题 + 详情列表（背景/决定/验证/相关文件），**敏感信息不进 commit**（token/key 不进任何 md 或代码注释）
4. **微小修复可酌情合并**：单文件 typo / 注释修正可积累到下个阶段一起 push
5. **不强行 push**：如果验证失败，**先修后 push**，不把失败状态推到远程

**执行命令**：

```bash
git add <修改的文件>      # 不 add config.yaml（密钥在 .gitignore）
git commit -m "..."
git push
```

**交接给下个 agent 时**：push 状态应是 clean（无未提交改动），下个 agent 拉取后可直接继续。

---

#### 15. 文档闭环

```text
想法
↓
如果有设计取舍 → decisions.md
↓
实现
↓
验证
↓
architecture / api（如果现状改变）
↓
DEVLOG（每个有意义事件一条，不为微修复单独写）
↓
ROADMAP（如果计划状态改变）
↓
push
```

不是所有代码改动都改所有文档，只同步真正受影响的文档。

---

#### 16. 文档职责

| 文档 | 职责 |
|---|---|
| `AGENTS.md`（本文件） | 怎么开发。稳定的行为准则和项目原则 |
| `CLAUDE.md` | 兼容入口（指向 AGENTS.md） |
| `docs/architecture.md` | 现在系统是什么样。架构变化时同步，不长期堆未来计划 |
| `docs/api.md` | HTTP API 契约 |
| `docs/decisions.md` | 为什么这么设计。格式建议见 §17 |
| `docs/DEVLOG.md` | 项目如何演变。一条记录一个有意义的开发事件/阶段 |
| `ROADMAP.md` | 未来准备做什么。只保留当前仍有效的路线 |
| `docs/PLAN-v5.md` | 长期实施路线（唯一现行计划，取代已删除的 PLAN-v4） |
| `docs/PLAN-LORA.md` | LoRA Context 工程最终任务计划 |
| `docs/workflow-anatomy.md` | ComfyUI / workflow 底层技术知识与踩坑记录 |
| `README.md` | 给开发者/使用者看的项目入口 |

### Decisions 格式建议

```text
Dxx. 标题

背景：
问题：
候选：
决定：
原因：
代价：
验证：
相关文件：
```

新决定推翻旧决定时，不删除旧 ADR；新增 ADR 并明确 supersedes / revises 哪个旧决定。

---

#### 17. 当前模块地图（仓库现状）

`server/main.py` 是单文件后端，关键模块：

| 模块 | 关键符号 | 职责 |
|---|---|---|
| 鉴权/限流 | `auth()` `USAGE` | Bearer token + 日限，**内存计数（重启清零）** |
| 内容过滤 | `check_banned()` | banned_words 子串匹配 |
| **Prompt Engine** | `translate()` `match_characters()` `match_dict_words()` `siliconflow_translate()` `_parse_structured_output()` `normalize_tag_order()` `_strip_char_bare_names()` `HotDict` | 三层：角色→词典→LLM（信息分流：5 字段给人看 + TAGS 离散 + NL 关系叙事不重复，D28）/ LRU 缓存 500 / reroll 跳过缓存 / tag 规范序 count→char→general / 词典 mtime 热更新 / **裸名变体去重防 IP logo（D30）** |
| **Workflow Engine** | `sanitize_for_api()` `build_prompt()` `upload_image_to_comfy()` | 清洗 + 注入 prompt/seed/size/多 LoRA/img2img/detailer（D32 合并工作流 + 删节点拼接）/ 统一 seed / img2img + detailer + LoRA 一份 `AnimaFull.json` |
| **LoRA Registry** | `HotLoraRegistry` `get_lora_registry()` `resolve_lora_selections()` `compile_lora_bindings()` | versioned Registry 为正式真相，未迁移 `config.yaml.loras` 只作兼容；新文件由 `.tools/register_lora.py --agent` 检查 LoRA Manager 索引、蒸馏作者说明并原子入库。旧启动扫描/Civitai cache 已由 D49 退役。 |
| ComfyUI 客户端 | `submit_and_wait()` | `/prompt` 提交 + `/history` 轮询 + `/view` 取图 |
| 队列 | `worker()` `QUEUE` | 单并发 asyncio.Queue（GPU 串行） |
| 静态托管 | `/` `/images` | `/` 返回 `web/index.html`，`/images` 出图 |
| **Intent Engine** | `match_characters()` `siliconflow_vision_translate()` | 构图/场景/情绪分解（D18 LLM 结构化）/ **参考图理解 ③ Qwen3-VL**（D23）/ **⑤ 对话迭代显式路由**（D25/D26/D31 redo 替换意图检测）/ 否定解析弃用（Anima 负面=常量） |

**Sensitive**：`.workbuddy/memory/` 是 Agent 工具数据目录，**不应进 git**（建议加进 `.gitignore`），避免误提交 Agent 内部数据。

---

#### 18. 敏感信息

`server/config.yaml` 含 token 与 API key，已 `.gitignore`。密钥只活在 config.yaml，不写进任何 md 或代码注释。

- 新增含密钥文件前先确认已 ignore
- 仓库当前私有（air041001/airpaint），不每次 push 扫密钥
- **转公开前**统一审计：`grep -rnE "sk-|cfk_|friend-[0-9]" --exclude-dir=.git .`

---

#### 19. 当前阶段特别原则

**先把普通 Prompt 做强，再做 LoRA 增强。**

不要让 LoRA trigger / 自动发现等问题吞掉 Prompt Intelligence 主线。
不要为了"Agent / Prompt IR / Knowledge"听起来高级而提前实现所有未来组件。

每一个新抽象都应该回答：

> **它是否让 AirPaint 更准确地把用户想要的画面传达给当前模型？**

如果不能，暂缓。

---

#### 20. 最终原则

AirPaint 的长期目标不是：

> "一句话帮小白画图。"

而是：

> **"一个懂 ComfyUI 的人，依然可以完全理解 Prompt / LoRA / Workflow，但使用 AirPaint 时，可以更快、更准确地把脑中的画面编译成最终生成请求。"**

所有后续 Agent 都应围绕这个目标工作。
