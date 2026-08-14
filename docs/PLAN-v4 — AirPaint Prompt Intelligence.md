# PLAN-v4 — AirPaint Prompt Intelligence

> 状态：草案 v0.1（2026-08-13）
>
> 本计划重新定义 AirPaint 的长期方向。
>
> **核心目标：不是替代 ComfyUI，也不是服务不会使用 AI 绘图的小白用户，而是让已经理解 Prompt / LoRA / ComfyUI 的用户，在“描述画面 → 形成高质量 Prompt → 使用知识 → 使用 LoRA → 修改 Prompt → 执行 Workflow”这一整条链路上，比直接使用 ComfyUI 更高效、更智能、更可控。**
>
> **核心优先级：Prompt Intelligence > Prompt Knowledge > LoRA Intelligence > Workflow Intelligence。**

---

# 0. 产品定位

## 一句话

**AirPaint = 面向 ComfyUI 用户的 Prompt Intelligence Layer。**

ComfyUI 负责执行生成工作流。

AirPaint 负责理解用户想表达的画面，并把它编译成更适合当前模型与工作流的 Prompt。

最终结构：

```text
用户意图
    ↓
理解 / 规划
    ↓
Prompt Intelligence
    ↓
Prompt Knowledge
    ↓
Prompt Compiler
    ↓
LoRA / 其他扩展辅助
    ↓
Workflow Engine
    ↓
ComfyUI
```

AirPaint 不隐藏专业概念。

用户可以知道：

- Prompt
- Danbooru tag
- LoRA
- trigger
- weight
- checkpoint
- detailer
- img2img
- workflow

AirPaint 的目标不是让用户“不需要懂这些东西”，而是让用户在**已经懂这些东西**的前提下，少做机械工作，并获得比直接使用 ComfyUI 更好的 Prompt 操作体验。

---

# 1. 产品价值排序

整个项目不以“自动化程度”排序，而以对实际绘图质量和效率的贡献排序。

## 第一优先级：Prompt Intelligence

解决：

> 用户脑中有一个画面，但不知道怎样把它写成最适合 Anima 的 Prompt。

这是 AirPaint 的核心。

---

## 第二优先级：Prompt Knowledge

解决：

> 模型本身知道什么？已有词典知道什么？哪些 tag 已验证？哪些知识需要在线发现？

让 AirPaint 的知识随着使用不断增长，而不是永远依赖一份静态 YAML。

---

## 第三优先级：LoRA Intelligence

解决：

> 当 Anima 本身解决不了某个人物、风格或特殊概念时，如何让 LoRA 正确介入 Prompt，而不是简单地把 trigger 字符串塞进去。

LoRA 是扩展层，而不是整个产品的中心。

---

## 第四优先级：Workflow Intelligence

解决：

> 当前意图到底应该怎样调用现有 ComfyUI workflow？

包括 txt2img、img2img、detailer、reference image、未来的 ControlNet 等。

这一层建立在前三层成熟之后。

---

# 2. 当前系统基础

当前 AirPaint 已经拥有：

- 中文 → Danbooru tag 的三层 Prompt Engine
- `char_dict.yaml`
- `dict.yaml`
- LLM fallback
- Prompt breakdown
- scene / composition / mood / lighting / style 信息分流
- LoRA Registry
- config 手动 LoRA
- Civitai 自动元数据
- 多 LoRA workflow 注入
- trigger 拼接
- img2img
- detailer
- reroll
- 对话迭代
- ComfyUI Workflow API 注入
- 单 GPU 队列

这些能力全部保留。

新的计划不是推倒重来，而是重新定义这些组件之间的关系。

---

# 3. 核心问题：Prompt Engine 目前仍然更像 Translator

现在大体流程是：

```text
中文
 ↓
角色匹配
 ↓
词典匹配
 ↓
LLM 补全
 ↓
Danbooru Prompt
```

这已经比单纯翻译器强很多，但最终仍然把大量语义压平为字符串。

未来应该变成：

```text
用户意图
 ↓
Intent Understanding
 ↓
Semantic Prompt Plan
 ↓
Prompt IR
 ↓
Prompt Compiler
 ↓
Anima Prompt
```

其中 Prompt IR 是整个系统最重要的新抽象之一。

---

# 4. Prompt IR

Prompt IR 不是最终 Prompt。

它是 AirPaint 内部用于保存“用户真正想表达什么”的中间表示。

初期建议：

```json
{
  "subjects": [],
  "appearance": [],
  "clothing": [],
  "action": [],
  "pose": [],
  "interaction": [],
  "scene": [],
  "composition": [],
  "lighting": [],
  "mood": [],
  "style": [],
  "constraints": [],
  "negative_intent": []
}
```

未来可以继续扩展。

---

# 5. TAG 与 NL 必须并存

AirPaint 不应该把目标定义成：

> “尽可能把所有东西都翻译成 tag。”

Anima 需要的是**模型能够理解的完整描述**。

因此 Prompt Engine 应明确区分：

## TAG Layer

适合：

- 角色
- 发色
- 发型
- 服饰
- 常见外观
- 明确物体
- 常见构图
- 常见风格
- 标准化属性

这些优先使用 canonical tag。

---

## NL Layer

适合：

- 复杂动作
- 姿态关系
- 多人物互动
- 空间关系
- 连续行为
- 复杂叙事
- 难以稳定压缩成标准 tag 的画面关系

例如：

```text
一只手撑着脸
另一只手拿着茶杯
身体转向窗户
回头看向镜头
```

不应该强迫系统把所有语义拆成互相独立的 tag。

---

# 6. Prompt Compiler

最终 Prompt 必须由代码层统一编译。

```text
Prompt IR
+
Knowledge
+
Model-specific rules
+
LoRA context
+
User overrides
↓
Prompt Compiler
↓
Final Anima Prompt
```

Compiler 负责：

- canonicalization
- 去重
- tag 排序
- TAG / NL 合并
- quality prefix
- safety
- 模型特定规则
- LoRA context 处理
- trigger 注入
- 用户锁定项
- 用户修改项

LLM 不直接决定最终字符串的全部细节。

---

# 7. Prompt Knowledge：重新定位 `dict.yaml`

当前 `dict.yaml` 不应该被视为：

> “永远正确的 Prompt 真理。”

它应该被视为：

> **已经积累的大量候选 canonical mapping。**

因为普通属性到底是：

```text
dictionary tag
```

还是：

```text
LLM 临场生成 tag
```

哪个更好，目前不能凭感觉决定。

因此未来应该支持：

```text
用户输入
    ↓
Dictionary candidate
    ↓
LLM candidate
    ↓
必要时外部知识
    ↓
验证 / 评估
    ↓
更可靠的 canonical representation
```

---

# 8. Dictionary 与 LLM 不设绝对优先级

不同类型的 Prompt 信息可能有不同的最优来源。

例如：

```text
固定属性
→ dictionary 往往更可靠

复杂关系
→ LLM / NL 往往更合适

角色 canonical tag
→ 专门知识库 / 外部验证

新出现的表达
→ LLM 先产生 candidate
```

因此未来不再坚持：

```text
dictionary > LLM
```

或者：

```text
LLM > dictionary
```

而是：

> **根据语义类型、知识置信度和验证结果决定来源。**

---

# 9. Character Knowledge：角色词典自动成长

这是 Prompt Knowledge 的第一项重点功能。

当前：

```text
用户输入角色名
 ↓
char_dict.yaml
 ↓
canonical tag
```

长期目标：

```text
用户输入角色
 ↓
角色识别
 ↓
检查本地 char_dict
 ↓
命中 → 直接使用

未命中
 ↓
在线检索 / 外部知识
 ↓
寻找该角色在当前模型生态中使用的 canonical tag
 ↓
验证
 ↓
加入自动知识缓存
 ↓
后续使用
```

核心目标：

> **不要求提前维护一份庞大的角色数据库，让 AirPaint 随着实际使用自动成长。**

---

# 10. Character Knowledge 必须区分“候选知识”和“稳定知识”

不要让外部搜索结果直接覆盖正式 `char_dict.yaml`。

建议分为：

```text
char_dict.yaml
```

和：

```text
knowledge_cache/
```

例如：

```text
knowledge_cache/
    characters.json
    tags.json
    aliases.json
```

流程：

```text
未知角色
 ↓
candidate knowledge
 ↓
验证
 ↓
成功使用 / 用户确认 / 多次一致
 ↓
promote
 ↓
char_dict
```

这样自动更新不会直接污染稳定知识库。

---

# 11. Character Knowledge 数据结构

未来角色条目应逐渐从：

```yaml
甘雨: ganyu_(genshin_impact)
```

扩展为：

```yaml
ganyu:
  aliases:
    - 甘雨
    - Ganyu
    - ...
  series:
    - genshin_impact
  canonical_tag:
    - ganyu_(genshin_impact)
  source: ...
  confidence: ...
  verified: true
```

重点不是增加字段数量，而是让：

> **“用户叫它什么”**

与：

> **“模型应该使用什么 canonical tag”**

解耦。

---

# 12. 普通 Tag Knowledge 自动成长

与角色知识类似，普通 `dict.yaml` 未来也可以逐渐变成：

```text
Candidate
 ↓
LLM 提议
 ↓
外部知识 / 当前模型资料
 ↓
本地验证
 ↓
Knowledge Cache
 ↓
稳定后进入正式词典
```

不过：

**普通 tag 自动更新的优先级低于角色自动发现。**

原因：

- 角色 tag 更容易明确验证；
- 普通描述存在大量语义等价表达；
- 需要更多实际生图比较才能判断 canonical tag 是否更好。

---

# 13. Prompt Knowledge Evaluation

最终系统需要回答：

> “这个词典里的 tag 真的比模型自己生成的更好吗？”

因此未来可以建立小型 Evaluation Set：

```text
Chinese input
×
Dictionary candidate
×
LLM candidate
×
Prompt context
×
Seed
```

评价：

- Prompt adherence
- concept accuracy
- visual relevance
- canonical tag consistency
- redundant tag rate
- 生成结果稳定性

目标不是建立学术级 benchmark。

而是避免 AirPaint 靠主观感觉不断修改 Prompt Engine。

---

# 14. LoRA 的真正定位

LoRA 不再作为第一产品主线。

AirPaint 对 LoRA 的长期理解是：

> **当 Anima 本身无法提供某个人物、某种风格或某种特殊概念时，LoRA 是额外的知识 / 概念注入。**

因此：

```text
正常情况下
User Intent
 ↓
Prompt Intelligence
 ↓
Anima
```

只有当存在需求时：

```text
User Intent
 ↓
Prompt Intelligence
 ↓
LoRA Extension
 ↓
Anima
```

---

# 15. LoRA-aware Prompt

当前最大问题：

```text
选择 LoRA
 ↓
拼 trigger
 ↓
prompt
```

这会把 LoRA 当成字符串。

未来应该是：

```text
LoRA
 ↓
它提供了什么概念？
 ↓
Prompt 还需要描述什么？
```

即：

> **LoRA 提供概念，Prompt 描述剩余意图。**

---

# 16. LLM 必须知道当前 LoRA Context

选中 LoRA 后，LLM 应得到结构化上下文：

```text
LoRA:
type = character
name = ...
description = ...
known_concepts = ...
trigger_candidates = ...
```

而不是：

```text
xxx.safetensors
```

LLM 不直接决定文件名。

代码负责：

```text
LoRA ID
 ↓
Registry
 ↓
metadata
```

模型负责：

```text
如何利用这个概念
```

---

# 17. Trigger 不再视为普通 Prompt 字符串

当前：

```python
trigger = ", ".join(triggers)
```

可以继续作为旧路径 fallback。

长期则应该建立：

```text
Trigger Profile
```

支持：

```text
primary
optional
semantic
preferred_strategy
```

例如：

```yaml
trigger:
  primary:
    - xxx

  optional:
    - xxx
    - yyy

  semantic:
    character:
      - ...
    outfit:
      - ...
    style:
      - ...
```

---

# 18. Trigger 不采用统一截断规则

不规定：

```text
前 N 个 tag
```

也不规定：

```text
最多 N token
```

因为不同 LoRA 的训练数据、caption、概念绑定方式可能不同。

初期建立：

```text
Full
Minimal
Semantic
Manual
```

四种策略。

---

# 19. Trigger Strategy Evaluation

为常用 LoRA 建立小型实验集：

```text
LoRA
×
Trigger strategy
×
Prompt
×
Seed
```

评价：

- LoRA concept adherence
- Prompt adherence
- Style adherence
- Character adherence
- Composition adherence
- 是否吞噬其他 Prompt 意图

最终把实验结果写回 Registry。

例如：

```yaml
trigger_profile:
  preferred: minimal
  confidence: 0.8
```

这是可选 metadata，不是硬编码规则。

---

# 20. 多 LoRA

当前：

```text
character ≤ 1
style ≤ 1
```

可以作为现阶段 UI / 业务限制。

但底层数据结构不要写死。

未来可以支持：

```text
Character
A
B

Style
C

Outfit
D

Concept
E
```

真正的限制来自：

- compatibility
- VRAM
- concept interference
- prompt conflict

而不是：

```text
character_dropdown == 1
style_dropdown == 1
```

---

# 21. 当前 LoRA 数量少时的原则

**不要为了“Agent 化”而提前构建大规模 LoRA 推荐系统。**

当前优先把：

- Registry
- metadata
- trigger
- LoRA context
- Prompt interaction

做好。

当实际 LoRA 数量和需求增长后，再考虑：

- semantic retrieval
- embedding
- ranking
- compatibility
- LoRA composition

---

# 22. 自动 LoRA Discovery

当前已有：

```text
safetensors
 ↓
SHA256
 ↓
Civitai
 ↓
metadata cache
```

但自动更新脚本目前存在实现问题。

近期目标：

- [ ] 确认扫描触发时机
- [ ] 确认缓存是否正确写入
- [ ] 确认新增 LoRA 是否自动进入 registry
- [ ] 确认 Civitai 查询失败时的状态
- [ ] 确认服务重启后的 cache 恢复

之后再考虑：

- [ ] trigger inference
- [ ] semantic description
- [ ] automatic classification
- [ ] keyword extraction

自动发现是基础设施，不是当前核心产品功能。

---

# 23. Agent / Model Architecture

当前模型分工：

```text
Reasoning / Intent:
DeepSeek-V4-Flash

Vision / Reference Image:
Qwen3-VL-8B-Instruct
```

未来不把模型名称写死在架构中。

抽象为：

```text
Reasoning / Intent Model
Vision Model
```

这样未来可以更换更强模型，而无需重写 Prompt Engine。

---

# 24. 模型输出协议

LLM 不直接负责最终 Prompt。

建议输出：

```json
{
  "intent": {},
  "prompt_plan": {
    "subjects": [],
    "appearance": [],
    "clothing": [],
    "action": [],
    "pose": [],
    "interaction": [],
    "scene": [],
    "composition": [],
    "lighting": [],
    "mood": [],
    "style": []
  },
  "knowledge_queries": [],
  "lora_hints": [],
  "modifications": [],
  "constraints": []
}
```

代码负责：

```text
validation
↓
knowledge lookup
↓
canonicalization
↓
Prompt compilation
↓
LoRA binding
↓
workflow injection
```

---

# 25. 用户修改必须基于结构，而不是字符串

用户可能说：

> “其他都不要动，只把衣服换成黑色。”

系统应该识别：

```text
LOCKED
character
appearance
scene
lighting
composition

CHANGE
clothing
```

然后只修改对应字段。

这比“重新生成整个 Prompt”可靠得多。

---

# 26. Prompt State

未来 Prompt 不再只有：

```text
current_prompt: string
```

而应该维护：

```text
PromptState
 ├── semantic_ir
 ├── compiled_prompt
 ├── knowledge_used
 ├── lora_context
 ├── locked_fields
 ├── user_overrides
 └── generation_history
```

这样可以支持：

```text
“换衣服”
“动作不变，只改光”
“保持角色”
“保持构图”
“换成另一种画风”
```

---

# 27. Decision Layer

Decision Layer 是执行层的一部分，不是产品目的。

最终：

```text
Prompt Intelligence
+
Knowledge
+
LoRA Extension
 ↓
Decision
 ↓
Workflow
```

Decision 可处理：

- LoRA
- trigger strategy
- weight
- detailer
- size
- workflow
- img2img
- denoise
- 其他参数

遵循：

> **LLM 表达意图，代码决定实际实体和数值。**

---

# 28. Workflow Intelligence

这是后期。

当 Prompt Intelligence 成熟以后，再让系统判断：

```text
txt2img
img2img
detailer
reference image
ControlNet
其他 workflow
```

形成：

```text
Intent
 ↓
Prompt Plan
 ↓
Workflow Decision
 ↓
ComfyUI
```

而不是一开始为了“Agent”强行自动选择 workflow。

---

# 29. Phase 1 — Prompt IR

### 目标

**把 Prompt Engine 从 Translator 升级成 Planner。**

任务：

- [ ] 定义 Prompt IR
- [ ] 将现有 breakdown 映射到 IR
- [ ] 增加 action
- [ ] 增加 pose
- [ ] 增加 interaction
- [ ] 增加 constraints
- [ ] 区分 TAG / NL
- [ ] LLM 输出结构化 Prompt Plan
- [ ] Prompt Compiler 初版

验收：

复杂动作、多主体关系、空间关系能够稳定表达，而不是全部压平成互不相关的 tag。

---

# 30. Phase 2 — Prompt Knowledge

### 目标

**让 AirPaint 的知识库开始自己成长。**

任务：

- [ ] char_dict candidate lookup
- [ ] 角色 alias discovery
- [ ] 外部 tag 查询
- [ ] character knowledge cache
- [ ] candidate → verified promotion
- [ ] 普通 tag candidate cache
- [ ] dictionary / LLM candidate comparison
- [ ] 建立小型 Evaluation Set

优先顺序：

```text
Character Knowledge
 >
普通 Tag Knowledge
```

---

# 31. Phase 3 — Prompt State / Incremental Editing

### 目标

**让 AirPaint 真正开始超越 ComfyUI 的 Prompt 文本框。**

任务：

- [ ] PromptState
- [ ] field-level modification
- [ ] locking
- [ ] selective rewrite
- [ ] history
- [ ] 修改原因可见

验收：

```text
“换衣服”
```

不会导致：

```text
角色
场景
构图
光影
```

全部改变。

---

# 32. Phase 4 — LoRA Context

### 目标

**让 LoRA 从一个 trigger 字符串变成 Prompt 的语义上下文。**

任务：

- [ ] Registry semantic metadata
- [ ] LoRA context
- [ ] LoRA-owned concept
- [ ] prompt-owned concept
- [ ] redundant concept suppression
- [ ] LoRA-aware Prompt generation

验收：

同一用户意图：

```text
无 LoRA
```

和：

```text
使用角色 LoRA
```

生成的 Prompt Plan 应表现出明显不同的职责分工。

---

# 33. Phase 5 — Trigger Engine

### 目标

**解决 trigger 的实际使用问题。**

任务：

- [ ] trigger profile
- [ ] full
- [ ] minimal
- [ ] semantic
- [ ] manual
- [ ] trigger conflict detection
- [ ] A/B test
- [ ] registry 持久化实验结果

验收：

常用 LoRA 不再简单地把完整 trigger 全部塞进 Prompt。

---

# 34. Phase 6 — Joint Prompt / LoRA Compiler

### 目标

形成：

```text
User Intent
      ↓
Prompt IR
      ↓
Knowledge
      ↓
LoRA Context
      ↓
Residual Prompt
      ↓
Trigger Strategy
      ↓
Prompt Compiler
      ↓
Final Prompt
```

这是 AirPaint Prompt Intelligence 的第一阶段完整形态。

---

# 35. Phase 7 — LoRA Composition

仅在前述系统稳定以后进行。

任务：

- [ ] multiple LoRA
- [ ] compatibility
- [ ] ordering
- [ ] weight policy
- [ ] trigger composition
- [ ] concept conflict detection
- [ ] prompt conflict detection

目标从：

> LoRA Selection

升级为：

> **LoRA Composition**

---

# 36. Phase 8 — Workflow Intelligence

最后再加入：

- [ ] txt2img 自动选择
- [ ] img2img 自动选择
- [ ] detailer 自动选择
- [ ] reference image 路径
- [ ] ControlNet 等扩展路径
- [ ] 参数决策

---

# 37. 暂时明确不做

近期不做：

- [ ] 面向小白用户的教学产品
- [ ] “一句话什么都不用管”的产品路线
- [ ] 大规模 LoRA marketplace
- [ ] 社交 / 社区
- [ ] 用户画像
- [ ] 大规模云端服务
- [ ] 自训练基础模型
- [ ] 为了 Agent 而 Agent 化
- [ ] 在 LoRA 数量不足时提前建设复杂推荐系统
- [ ] 为了“高级”而提前引入 embedding / vector DB

---

# 38. 最终产品形态

AirPaint 最终不是：

```text
“告诉我你想画什么，我替你画。”
```

而是：

```text
“你告诉我你脑中的画面。

我理解你想表达的东西；
知道什么应该写成 canonical tag；
知道什么更适合用自然语言描述；
知道这个人物在当前模型里应该用什么 tag；
不知道的知识可以自己去找；
已经验证过的知识会留下；
如果你用了 LoRA，我知道它已经提供了什么；
因此我不会再重复描述；
需要 trigger 时，我知道应该怎样使用；
你想修改时，我只修改你真正想改的部分；

最后，把这些东西编译成适合当前模型和 ComfyUI workflow 的 Prompt 与执行参数。”
```

---

# 39. 最终成功标准

AirPaint 不以：

> “完全自动生成图片”

作为成功标准。

真正的成功是：

> **一个已经会用 ComfyUI 的人，会觉得 AirPaint 比直接打开 ComfyUI 更适合写 Prompt、管理语义和迭代修改。**

核心指标：

```text
Prompt 质量更高
Prompt 编写更快
复杂动作表达更好
复杂关系表达更稳定
角色识别更准确
知识库随着使用增长
Dictionary 与 LLM 能互相验证
LoRA 不再污染 Prompt
Trigger 使用更合理
修改 Prompt 更可控
最终 Workflow 执行仍由 ComfyUI 完成
```

---

# 40. 总体技术路线

```text
                         AirPaint
                            │
                 ┌──────────┴──────────┐
                 │                     │
        Prompt Intelligence      Extension Layer
                 │                     │
        ┌────────┴────────┐            │
        │                 │            │
   Prompt IR        Prompt Knowledge   LoRA
        │                 │            │
   TAG + NL        Character / Tag     │
        │                 │            │
        └────────┬────────┘            │
                 │                     │
                 └──────────┬──────────┘
                            ↓
                    Prompt / Decision
                         Compiler
                            ↓
                    Workflow Intelligence
                            ↓
                         ComfyUI
```

**核心原则：先把“画好图”做好，再把 LoRA 做成最强的扩展辅助。**
**先让 AirPaint 更懂 Prompt，再让它更懂 LoRA。**
**先解决真正存在的问题，再考虑 Agent 化程度。**