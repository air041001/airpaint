# PLAN-v5 — AirPaint Prompt Intelligence

> 状态：正式路线（2026-08-16 修订，Phase 2.6 Prompt Expansion 已完成）
> 取代 PLAN-v4（v4 保留作历史参考）
> 实施计划批准文件：`.workbuddy/plans/electric-forging-babbage.md`
>
> **核心原则**：
> 1. **NSFW-first，Prompt-first，LoRA 后置** — 首要目标是在当前 base Anima 上把成人虚构内容的 Prompt 与出图质量做到最好；普通绘图 Prompt 是顺带做强的基础能力，LoRA 是 extension layer，不是当前主线。
> 2. **LLM 是大脑，代码是脊髓** — LLM 负责意图/语义/关系/偏好；代码负责 canonical tag/知识库/LoRA ID/文件/数值/ordering/validation/workflow injection。模型不直接决定 `xxx.safetensors` 或 `CFG=6.7`。
> 3. **渲染策略必须实证** — Prompt IR 是内部语义表示，不规定固定的最终 Prompt 格式；TAG、NL、权重、空间锚点和语义负面如何组合，必须用固定工作流/seed 的人眼结果验证，不能凭格式整洁推断质量。
> 4. **单人长期开发，最小可用优先** — 不铺开 8 Phase。先证明核心价值，再扩展 Character Knowledge / LoRA Intelligence / Workflow Intelligence。

---

## 0. 产品定位

**AirPaint = 面向 ComfyUI 用户的 Prompt Intelligence Layer。**

ComfyUI 负责执行生成工作流。AirPaint 负责理解用户想表达的画面，并把它编译成更适合当前模型与工作流的 Prompt。

AirPaint 不隐藏专业概念（Prompt / Danbooru tag / LoRA / trigger / weight / checkpoint / detailer / img2img / workflow）。目标不是让用户"不需要懂这些"，而是让**已经懂**的用户少做机械工作，获得更好的 Prompt 操作体验。

最终结构：

```
用户意图
    ↓
Intent Understanding
    ↓
Prompt Intelligence (Prompt IR + Knowledge)
    ↓
Prompt Compiler
    ↓
LoRA / 其他扩展辅助
    ↓
Workflow Engine
    ↓
ComfyUI
```

当前首要验证场景是成人虚构内容生成。系统必须保持专业的 Danbooru/Anima NSFW 表达能力，但不把 NSFW 语义与工作流文件、节点 ID 或任意数值混在一起。

---

## 1. 产品价值排序

1. **Prompt Intelligence** — 用户脑中有画面，但不知道怎样写成最适合 Anima 的 Prompt。这是核心。
2. **Prompt Knowledge** — 模型知道什么 / 词典知道什么 / 哪些 tag 已验证 / 哪些知识需在线发现。让知识随使用增长。
3. **LoRA Intelligence** — Anima 解决不了的人物/风格/概念，让 LoRA 正确介入 Prompt，而非简单塞 trigger 字符串。
4. **Workflow Intelligence** — 当前意图该怎样调用现有 ComfyUI workflow。建立在前三层成熟之后。

---

## 2. 当前系统基础（代码验证）

以下能力全部保留（`server/main.py` 代码验证属实）：

- 中文→Danbooru tag 三层 Prompt Engine（`translate()` L637：match_characters→match_dict_words→siliconflow_translate→_parse_structured_output→_strip_char_bare_names→normalize_tag_order）
- `char_dict.yaml` / `dict.yaml`（扁平 `中文: tag` 映射，HotDict 热更新）
- LLM fallback（DeepSeek-V4-Flash）
- Prompt breakdown（5 字段：scene/composition/mood/lighting/style，`_STRUCTURED_FIELDS` L375）
- TAG/NL 信息分流（D28，HARD RULE：NL 不复述 TAGS）
- LoRA Registry（config.yaml loras + Civitai 自动元数据 `scan_loras()` L159）
- 多 LoRA workflow 注入（`build_prompt` L807-827，trigger `", ".join(triggers)` L827）
- img2img / detailer / reroll / 对话迭代（dialog_turn redo/tweak/vibe）
- ComfyUI Workflow API 注入 + 单 GPU 队列

新计划不是推倒重来，而是重新定义这些组件之间的关系。

---

## 3. 核心问题：Prompt Engine 目前仍然更像 Translator

当前流程（`translate()` L637-727）：

```
中文 → 角色匹配 → 词典匹配 → LLM 补全 → Danbooru Prompt 字符串
```

最终仍把大量语义压平为字符串。未来应变成：

```
用户意图 → Intent Understanding → Prompt IR → Prompt Compiler → Anima Prompt
```

**Prompt IR 是整个系统最重要的新抽象**——AirPaint 内部保存"用户真正想表达什么"的中间表示，而非最终 Prompt 字符串。

---

## 4. Prompt IR（12 字段）

```json
{
  "subject": [],
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
  "constraints": []
}
```

字段职责：
- `subject` — 主体计数与角色（1girl/2boys/角色 tag）
- `appearance` — 发色/瞳色/体型等离散属性
- `clothing` — 服饰
- `action` — **单角色**动作
- `pose` — **单角色**姿态
- `interaction` — **多角色**互动与空间关系（保留 interaction 而非压进 action/pose，因多角色互动是 NL 核心语义，压平会丢关系）
- `scene` / `composition` / `lighting` / `mood` / `style` — 环境/构图/光影/氛围/风格
- `constraints` — 用户约束（锁定项/否定项，negative_intent 延后）

未来可按需扩展字段。

---

## 5. TAG 与 NL 必须并存

AirPaint 不把目标定义成"尽可能把所有东西都翻译成 tag"。Anima 需要的是**模型能理解的完整描述**。

**TAG Layer**（canonical tag）：角色 / 发色 / 发型 / 服饰 / 常见外观 / 明确物体 / 常见构图 / 常见风格 / 标准化属性。

**NL Layer**（自然语言）：复杂动作 / 姿态关系 / 多人物互动 / 空间关系 / 连续行为 / 复杂叙事 / 难以稳定压缩成标准 tag 的画面关系。

不强迫系统把所有语义拆成互相独立的 tag。

> 现状：TAG/NL 信息分流已实现（D28，`_parse_structured_output` 解析 TAGS+NL 行，HARD RULE 防复述）。Phase 1 在此基础上引入 Prompt IR 统一承载。

---

## 6. Prompt Compiler

最终 Prompt 由代码层按经过验证的渲染策略编译（LLM 不直接决定最终字符串全部细节，IR 也不是固定模板）：

```
Prompt IR + Knowledge + Model-specific rules + LoRA context + User overrides
    ↓
Prompt Compiler
     ↓
 Rendering Strategy + Prompt Compiler
     ↓
 Final Anima Prompt
```

Compiler 长期负责：canonicalization / 显著性与去重 / tag 排序 / TAG-NL-NL 渲染策略 / 权重与空间锚点 / quality prefix / safety / 模型特定规则 / LoRA context 处理 / trigger 注入 / 用户锁定项 / 用户修改项。具体规则必须由 Rendering Strategy 实验支持。

> 现状：`normalize_tag_order`(L609) 是去重+重排的微型胚胎，`build_prompt`(L834) 做字符串拼接。Phase 1 扩展成统一编译器。

---

## 7. Prompt Knowledge：重新定位 dict.yaml / char_dict.yaml

词典不应被视为"永远正确的 Prompt 真理"，而是"已积累的大量候选 canonical mapping"。

未来支持：用户输入 → Dictionary candidate + LLM candidate + 必要时外部知识 → 验证/评估 → 更可靠的 canonical representation。

**Dictionary 与 LLM 不设绝对优先级**——根据语义类型、知识置信度、验证结果决定来源（固定属性 dictionary 往往更强；复杂关系 LLM/NL 往往更合适；角色 canonical tag 专门知识库/外部验证；新表达 LLM 先产生 candidate）。

---

## 8. Character Knowledge：角色词典自动成长

长期目标：用户输入角色 → 角色识别 → 查本地 char_dict → 未命中则联网检索 → 寻找当前模型生态中 canonical tag → 验证 → 加入自动知识缓存 → 后续稳定后 promote 正式 char_dict。

**区分"候选知识"和"稳定知识"**：外部搜索结果不直接覆盖正式 `char_dict.yaml`，先进 `knowledge_cache/`（characters.json / aliases.json），candidate → verified → promote。

**结构化 char_dict**（长期）：从 `甘雨: ganyu_(genshin_impact)` 扩展为含 aliases/series/canonical_tag/source/confidence 的结构，让"用户叫它什么"与"模型应用什么 canonical tag"解耦。

> 现状障碍：`HotDict` L48 `str(v).strip()` 强制扁平化是结构化 char_dict 的硬障碍，需改 HotDict 或换结构，影响 `match_characters`(L645) 全局——Phase 3 迁移改动。

---

## 9. LoRA 的真正定位

LoRA 不作为第一产品主线。长期理解：**当 Anima 本身无法提供某人物/风格/概念时，LoRA 是额外的知识/概念注入**。

```
正常: User Intent → Prompt Intelligence → Anima
需要时: User Intent → Prompt Intelligence → LoRA Extension → Anima
```

**LoRA-aware Prompt**：LoRA 提供概念，Prompt 描述剩余意图。选中 LoRA 后，LLM 应得到结构化上下文（type/name/description/known_concepts/trigger_candidates），而非 `xxx.safetensors`。LLM 不直接决定文件名，代码负责 LoRA ID→Registry→metadata。

**Trigger 不再视为普通 Prompt 字符串**：建立 Trigger Profile（primary/optional/semantic/preferred_strategy），不采用统一截断规则（前 N 个 tag / 最多 N token），因为不同 LoRA 训练方式不同。初期支持 full/minimal/semantic/manual 四种策略 + 少量 A/B 测试。

> 现状：trigger `", ".join(triggers)` (L827) 字符串拼接；translate() 对 LoRA 完全不知情（L691-697 context 只含 char_dict/dict tag）；Trigger Profile 代码零影子。Phase 5-6 实现。

---

## 10. 模型输出协议

LLM 不直接负责最终 Prompt。建议输出：

```json
{
  "intent": {},
  "prompt_plan": { /* Prompt IR 12 字段 */ },
  "knowledge_queries": [],
  "lora_hints": [],
  "modifications": [],
  "constraints": []
}
```

代码负责：validation → knowledge lookup → canonicalization → Prompt compilation → LoRA binding → workflow injection。

---

## 11. 用户修改必须基于结构，而不是字符串

用户说"其他都不要动，只把衣服换成黑色"→ 系统识别 LOCKED(character/appearance/scene/lighting/composition) + CHANGE(clothing)，只修改对应字段。

未来维护 PromptState：semantic_ir / compiled_prompt / knowledge_used / lora_context / locked_fields / user_overrides / generation_history。支持"换衣服"/"动作不变只改光"/"保持角色"/"保持构图"/"换画风"等增量修改。

> 现状：dialog_turn redo/tweak/vibe 三分支全是字符串累加重翻译（redo L1208 `session["raw"]+=delta` + 整体重翻译），正是此痛点根源。D31 字符串级替换意图检测是微型胚胎。Phase 4 结构化改造。

---

## 12. Phase 路线

### Phase 0: Prompt Baseline（现在做）

**目标**：建立回归基线，防止后续改 Prompt Engine 时凭感觉越改越差。

- 建 Evaluation Set 20-50 条真实需求，覆盖：单角色/角色+服装/场景/复杂动作/姿态/多人物关系/构图/光影/风格/模糊描述
- **第一层（结构回归，不生图）**：每条记录 `中文输入 → prompt_en → breakdown`，验证 JSON 稳定 / IR 完整 / TAG-NL 分流 / 重复率 / canonical / 排序
- **第二层（少量固定 seed 生图）**：选 5-10 条固定 seed 生图存档，只验证影响视觉的 case
- 形式：`.tools/eval_set/` 目录，YAML 格式

基础维护顺手做（不阻塞主线）：
- 修 scan_loras 三处问题（Civitai type 判定用 baseModel/model.type 而非 tags / 缓存 key 用完整文件名 / Wan 残留清理）
- 同步文档模型名（DeepSeek-V4-Flash + Qwen3-VL）
- 修已知 char_dict 错误（amamiya_kokoro 已修；全量审查延后 Phase 3）

### Phase 1: Prompt IR + Compiler（代码实现已完成）

**目标**：把 Prompt Engine 从 Translator 升级成 Planner，证明抽象提升带来出图质量提升。

- 定义 Prompt IR（12 字段 JSON）
- 改 LLM 输出协议：breakdown 5 字段 → Prompt IR JSON
- 验证 DeepSeek-V4-Flash 输出 12 字段 JSON 稳定性（先跑 20-50 条测试，降级兜底保留）
- Prompt Compiler 初版：`normalize_tag_order`(L609) + `build_prompt`(L834) 扩展成统一编译（canonicalization/去重/tag排序/TAG-NL合并/quality/safety/模型规则）
- 破坏性冲突：`_parse_structured_output`(L431) 解析逻辑改（保留旧 5 字段降级路径）；HotDict **不动**
- 验收：结构回归通过；第二层视觉质量由 Phase 1.5 重新按人眼 A/B 实验判定。Phase 1 的 IR/Compiler 通过，但不宣称固定渲染格式已经证明能画好图。

### Phase 1.5: Rendering Strategy Experiment（首轮实验完成，R4 作为已知限制）

**目标**：回答“同一语义下，什么样的 Prompt 表达更容易让 base Anima 画对”，而不是继续增加格式规则。

- 固定 base Anima、workflow、采样参数、尺寸、seed、negative，只改变 Prompt rendering variant。
- 7 个 case：简单单人、单人+道具动作、复杂单人姿态、双人对峙、场景+光影、成人 NSFW 单人、成人 NSFW 双人交互。
- 每个 case 比较 TAG-only、TAG+short NL、TAG+weighted spatial NL、NL-dominant；R2/R4 额外测试 semantic negative。
- 由项目用户人眼选择胜者；vision agent 只做粗筛，不作为语义终审。
- 实验结果形成语义类型 × 渲染策略表，之后才决定 Compiler 2.0 与 system prompt 规则。
- base Anima 对复杂双人构图可能出现分页、黑线、人体/武器错位；无法稳定自动解决时，保留用户编辑 `prompt_en` 的产品 fallback，不为单个 case 扩张架构。

### Phase 2: Prompt Quality（首轮实现与对照实验完成 2026-08-15）

**目标**：把实验证明有效的 Rendering Strategy 收进 Compiler，而不是继续假设统一格式。

- 按语义类型动态选择 TAG/NL/hybrid/weighted rendering
- 当前首个 profile 证据：明确成人 NSFW、单主体、简单动作可尝试 tag-first；普通 SFW 不默认删除 NL
- 研究 Dictionary vs LLM，不预设全局优先级：canonical 外观/光影/NSFW tag 倾向 Dictionary，开放物体/关系保留 LLM candidate
- 将 canonicalization / salience dedup / ordering / semantic negative 收进 Compiler
- 成人 NSFW 与普通 Prompt 分开建立回归集，固定 Prompt+seed 做第二层生图回归

当前阶段只落地了明确成人 NSFW 单主体的 `tag_first` profile、failure taxonomy 和首轮 Dictionary/LLM 对照；weighted NL、semantic negative、`girl/female` 词汇替换仍是未验证候选，不进入默认策略。

### Phase 2.5/2.6: Prompt Expansion（已完成）

Phase 2 首轮的 case 都是短句翻译或关系失败，没有验证 AirPaint 最初的核心价值：用户只给简单中文，系统能否像画师一样补全高质量画面。

- 比较忠实翻译与画师级补全，不把“更长/更规范”当目标。
- 统一 SFW/NSFW 的补全底层：构图、光影、氛围、材质；NSFW 只在服装状态、身体语言和揭示节奏上分流。
- E1-E7 覆盖角色细节、服装细节、场景锚定、光影、镜头、NSFW 张力和纯氛围→完整画面。
- E6 不在正向视觉 Prompt 强调“成年女性”；使用自然的 `woman/1girl` 表达，NSFW safety 仍由明确内容词和现有规则负责。
- IR 应从“记录用户说了什么”升级为“承载用户意图 + 经过验证的默认补全”；是否落地由固定 seed 人眼结果决定。

第一轮 A1/A2/A3 已完成 21 张图和人眼盲评，关键平局又用 seed offset 100 补测 9 张。盲评中的 `tie` 是 A/B/C 三个盲位中的并列结果，不是第四个 arm。A3 对 A2 为 4 胜、1 平、2 负，进入生产画师补全、IR 来源标注和 reroll 语义改造；生产后 5 case A/B 为 3 胜 2 平 0 负。

### Phase 2.6 最终结论

- 保留提示词增强：生产文本 LLM 使用独立 `IR + PROMPT` 画师协议，旧 TAGS/NL 协议保留降级；`prompt_ir_meta` 标注补全来源，reroll 更换补全方案。
- 自动补全的真实价值是补齐通用构图、光影、材质和可读性，不是凭空决定用户没有表达的服装、道具或叙事意图。
- A2 详细中文在部分 case 的优势说明：输入信息量是质量上限。后续不把“继续自动扩写”当作当前阶段主线，也不立即建设详细输入辅助 UX。
- Phase 2.6 完成后先观察真实使用反馈，再决定是否投入下一阶段。

### Phase 3: Character Knowledge（Phase 2 后）

- char_dict 错误清理（按优先级：实际用的/明显错误/高频）
- 联网查询未知角色 canonical tag
- `knowledge_cache/` candidate → verified → promote 正式 char_dict
- 结构化 char_dict（aliases/series/canonical_tag/source/confidence）— 需改 HotDict L48，影响 match_characters 全局

### Phase 4: PromptState + Incremental Editing（Phase 3 后）

- PromptState（semantic_ir/compiled_prompt/locked_fields/user_overrides/history/knowledge_used）
- 字段级修改（"换衣服"只动 clothing）
- 重构 dialog_turn redo/tweak/vibe 从字符串累加 → 结构化 state
- 破坏性冲突：影响三分支 + 前端暗房 + SESSIONS；D31 字符串级替换意图检测会与新 state 冲突

### Phase 5: LoRA Context（Phase 4 后）

- `translate(text, lora_context=...)` 签名加参数
- LoRA context（type/name/description/known_concepts/trigger_candidates）传 LLM，**不伪装 known tags**
- 调用顺序调整：create_job/dialog_turn 把 LoRA 选择前移到 translate 前

### Phase 6: Trigger Engine（Phase 5 后，LoRA 数量增长后）

- Trigger Profile（full/minimal/semantic/manual）
- 少量实际 LoRA 做 A/B 测试，不搞 ML 系统

### Phase 7: LoRA Composition（最后）

### Phase 8: Workflow Intelligence（最后）

---

## 13. 现在做 / 延后 / 不做

### 现在做（Phase 0 + Phase 1）
- Evaluation Set 第一层（20-50 条，结构回归）
- 修 scan_loras / 文档模型名 / 已知 char_dict 错误（顺手）
- Prompt IR 定义（12 字段）
- LLM 输出协议改 Prompt IR JSON + 验证稳定性
- Prompt Compiler 初版

### 延后（Phase 2-8，按顺序）
- Rendering Strategy 实验结论之前的生产级 TAG/NL/权重/semantic negative 规则
- Dictionary vs LLM 策略固化
- char_dict 联网查询 + 知识成长 + 结构化
- PromptState + dialog 重构
- LoRA context / Trigger Engine / LoRA Composition / Workflow Intelligence

### 目前根本不做
- 小白产品 / "一句话不用管"路线
- LoRA marketplace / 社交社区 / 用户画像
- 大规模云端 / 自训模型
- 为 Agent 而 Agent 化
- LoRA 数量不足时建复杂推荐系统
- 为高级提前引入 embedding / vector DB
- 全量人工审查 char_dict（只修已知错误 + Phase 3 按优先级清理）

---

## 14. 关键文件

| 文件 | 角色 | Phase 0/1 是否动 |
|---|---|---|
| `server/main.py` | Prompt Engine / Compiler / dialog_turn | Phase 1 改（IR + Compiler） |
| `server/char_dict.yaml` / `dict.yaml` | 知识库 | Phase 0 只修已知错误 |
| `server/config.yaml` loras / `lora_cache.json` | LoRA registry | Phase 0 修 scan_loras |
| `.tools/eval_set/`（新建） | Evaluation Set | Phase 0 建 |
| `ROADMAP.md` | 阶段规划 | Phase 0 同步指向 v5 |

---

## 15. 成功标准

不以"8 Phase 全部完成"为成功。真正成功是：

> **一个已经会用 ComfyUI 的人，觉得 AirPaint 比直接打开 ComfyUI 更适合写 Prompt、管理语义和迭代修改。**

### 已完成的核心验证

- Phase 1.5/2 已确认：没有一个 TAG/NL/权重格式可全局套用；复杂关系保留用户 Prompt 编辑 fallback。
- 明确成人 NSFW 单主体简单场景可使用 `tag_first`；普通 SFW 不全局删除 NL。
- Phase 2.6 v5 生产画师协议相对旧翻译在 5 个固定 case 上取得 3 胜 2 平 0 负。
- 人眼结果同时确认：自动补全能改善通用构图、光影、材质和可读性，但具体视觉意图仍受用户输入信息量限制。

Phase 2.6 已完成。是否继续 Phase 3-8 不由结构回归自动触发，先根据真实使用反馈决定。

---

## 16. 总体技术路线

```
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

**核心原则：先把"画好图"做好，再把 LoRA 做成最强的扩展辅助。先让 AirPaint 更懂 Prompt，再让它更懂 LoRA。先解决真正存在的问题，再考虑 Agent 化程度。**
