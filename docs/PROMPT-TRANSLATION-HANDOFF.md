# AirPaint 提示词翻译讨论交接

> 更新日期：2026-08-25  
> 用途：新对话继续讨论中文提示词翻译、Dictionary / LLM 路由、Prompt IR 与 LoRA-aware Prompt。  
> 本文只整理已经核实的事实、用户已经确认的判断和仍待决策的问题；不要把“候选方案”误写成已批准开发任务。

---

## 0. 新对话先读

1. 当前最值得讨论的问题不是继续增加 Prompt Phase，而是：**未经验证的 `dict.yaml` 是否拥有了过高的路由和语义权限。**
2. 用户没有授权重构词典、Prompt IR 或 PromptState；先讨论清楚再改代码。
3. 旧的 30 条 `baseline.yaml` 不是质量基准，已经删除；不要恢复它并用结构比较宣称画质进步。
4. 结构测试只能验证解析、路由、去重和不崩溃；图片质量仍需固定条件出图后由人眼判断。
5. 当前工作区有用户自己的未提交修改：`server/lora_registry.yaml` 中多项 LoRA 显示名称已经改成中文；另有未跟踪 `.opencode/` 和 `opencode.jsonc`。不要覆盖、回退或顺手提交这些内容。

---

## 1. 用户真正想解决的问题

AirPaint 最初的出发点是：用户用简单中文表达画面意图，系统帮助生成更适合 Anima 的英文 Prompt，避免手写英文提示词。

经过多轮真实出图后，用户已经接受一个边界：

- AI 可以补构图、光影、材质、视线和常规画面完整性；
- AI 不能稳定替代用户没有表达的具体创意；
- “一句简单中文必然得到优质图片”不现实；具体视觉构思仍然决定上限；
- 后续不应为了显得智能继续堆 Prompt 抽象和 system prompt 规则。

用户已把 LoRA 工程定义为项目最后一个大工程。虽然 `ROADMAP.md` 仍把 PromptState 写成“延后、使用数据触发”，用户在对话中的实际决定更保守：**冻结后续开放式 Prompt Phase，不因文档中的未来结构自行重启。**

---

## 2. 当前生产翻译链路（以代码为准）

主要实现：`server/main.py::translate()`。

```text
中文原文
  ↓
Character Dictionary：match_characters()
  ↓
Attribute Dictionary：match_dict_words()，中文子串最长优先匹配
  ↓
┌─ 全命中 + 未选 LoRA → 不调用 LLM，词典结果直接编译
├─ 有 misses → LLM 看 Known character tags + Known attribute tags
└─ 选中 LoRA → 即使普通词全命中也强制进入 LoRA-aware LLM
  ↓
Reasoning Model 输出 IR + PROMPT（旧 TAGS/NL 协议仅兼容降级）
  ↓
_prepare_painter_tags() / compile_prompt()
  ↓
LoRA Binding Compiler 确定性补 exact tags
  ↓
build_prompt() 注入 quality prefix、workflow、seed、尺寸和 LoRA
```

关键代码位置（行号可能随以后修改漂移）：

- `match_dict_words()`：`server/main.py` 约 L1313
- 全命中快速路径：约 L1665
- Known tags + Original intent 上下文：约 L1694
- `hits + LLM result` 最终合并：约 L1748
- `resolve_lora_selections()`：约 L617
- `compile_lora_bindings()`：约 L768
- `build_prompt()`：约 L1826

### Dictionary 命中后的真实权限

| 路径 | LLM 是否看到词典命中 | LLM 能否纠正命中项 |
|---|---:|---:|
| 全命中、未选 LoRA | 否，完全跳过 LLM | 否 |
| 部分命中 | 是，同时看到完整中文原意 | 基本不能，命中项仍被代码合并 |
| 已选 LoRA | 是，即使普通词全命中也会运行 | 可以围绕它规划，但不能删除命中项 |

因此，“词典命中后 LLM 完全不理解”只在全命中快速路径成立。其他路径的核心问题是：**LLM 看得到，但命中结果被当作不可修改的既定事实。**

---

## 3. TAG 与 Natural Language 的当前结论

不要把问题简化为“tag 不好、NL 才好”。Anima 能理解 tag，也能理解自然语言。

适合稳定 tag 的内容：

- 发色、瞳色、发型；
- 常见表情；
- 标准服饰和物体；
- 简单动作、人数、常见构图词；
- 已验证的角色 canonical tag。

更依赖语义理解的内容：

- 连续动作；
- 谁对谁做什么；
- 多人物空间关系；
- 复杂姿态和物理关系；
- 氛围如何通过场景、焦点和光影体现；
- 用户约束与多个属性之间的冲突。

即使一个英文短语不是 Danbooru canonical tag，Anima 也可能把它当作逗号分隔的 NL 片段理解。因此：

> “不是 Danbooru tag”不等于“一定画不出来”；但它不能继续被 AirPaint 当作已经验证、不可纠错的 canonical knowledge。

---

## 4. `dict.yaml` 的来源与已确认问题

用户说明：当前 `dict.yaml` 的内容主要由豆包、千问、Grok 生成，没有经过 Danbooru 官方标签验证，也没有逐项真实生图验证。

必须纠正的前提：大模型生成过，不等于大模型当时实际查询过 Danbooru。没有 tool/API 证据时，只能视为模型根据训练记忆生成的候选。

2026-08-24 使用 Danbooru 官方 `tags.json` API 抽查当前词典：

| 中文映射 | 当前值 | 官方抽查 |
|---|---|---|
| 白发 | `white hair` | 有效且大量使用的 `white_hair` |
| 蓝眼睛 | `blue eyes` | 有效且大量使用的 `blue_eyes` |
| 微笑 | `smile` | 有效且大量使用 |
| 孤独 | `lonely atmosphere` | `lonely_atmosphere` 不存在 |
| 黄昏 | `dusk, sunset, golden hour, orange, warm, silhouette` | 前三者/剪影存在；`orange` deprecated；整组不是同义翻译 |
| 少女 | `girl, young, cute, innocent` | `girl/young/cute` deprecated 且 post_count=0；整组还增加年龄与气质语义 |

官方查询示例：

- `https://danbooru.donmai.us/tags.json?search[name]=white_hair&limit=1`
- `https://danbooru.donmai.us/tags.json?search[name]=lonely_atmosphere&limit=1`
- `https://danbooru.donmai.us/tags.json?search[name]=orange&limit=1`
- `https://danbooru.donmai.us/tags.json?search[name]=girl&limit=1`

当前词典混合了三种东西：

1. 真正稳定的 canonical tag；
2. Anima 可能理解的普通英文短语；
3. 模型自行添加的画面扩写和审美决定。

第三类风险最大。例如：

- `黄昏 → silhouette` 会擅自改变人物可见性和构图；
- `少女 → young/cute/innocent` 不只是翻译主体，还添加年龄和气质；
- `孤独 → lonely atmosphere` 可能仍被 Anima 当 NL 理解，但系统错误地把它视为已验证 tag。

### Dictionary 当前真正的结构风险

1. **验证风险**：不存在、deprecated、post_count=0 的词被当作 canonical。
2. **语义风险**：一个中文词被扩成多个并非同义的视觉决定。
3. **路由风险**：只要中文被子串替换到 `remaining` 为空，就直接跳过 LLM。
4. **不可纠错风险**：部分命中时 LLM 能看到错误，但代码仍把 hits 合入最终 Prompt。
5. **遮蔽风险**：Anima 对普通英文容忍度高，偶尔仍能出正常图，反而会掩盖词典知识本身不可靠。

---

## 5. “简单就 TAG、复杂就 NL”并不是当前真实边界

当前路由不是 LLM 判断句子简单或复杂，而是机械判断：

> 词典子串替换后，`remaining` 是否为空。

已验证的例子：

- `白发蓝眼睛少女微笑`：可被词典完全吃掉，直接快速返回；
- 加一个语法助词后，可能留下少量中文并触发 LLM；
- `黄昏少女` 可能快速返回并附带整套暖色/剪影扩写；
- `黄昏下的少女` 因残留“下的”转入 LLM。

所以现有边界不是“语义复杂度”，而是“字典子串覆盖率”。这是潜在正确性问题。

---

## 6. Prompt IR 的实际价值与边界

当前 12 字段：

```text
subject / appearance / clothing / action / pose / interaction
scene / composition / lighting / mood / style / constraints
```

代码中仍有的实际用途：

- 派生前端“AI 理解”展示；
- 主体计数、剪影、默认风格、NSFW 景别等少量护栏；
- 未知角色 candidate 的辅助来源；
- 保存一次 LLM 的语义计划和 metadata。

当前没有的能力：

- 没有保存进暗房会话作为结构化状态；
- redo/tweak/vibe 没有基于字段增量修改；
- img2img 不是按 IR 字段换衣服/换动作；
- 前端没有字段级编辑；
- 没有证据证明“12 字段完整”会让图片更好。

因此当前结论是：**保留现有 IR 兼容实现，但不继续扩展，也不以未来 PromptState 为理由增加复杂度。** 用户已明确质疑并冻结后续相关 Phase。

---

## 7. 旧 Baseline 与 A/B 测试复盘

### 旧 baseline 的问题

最初 Phase 0 的 `baseline.yaml` 是 agent 生成的 30 条输出快照，没有经过真实生图验证，却在 Phase 2 一度被当作比较标准。用户在 Phase 2 做完大量 A/B 后才意识到该前提不成立。

历史文件仍可只读查看：

```text
git show 6ead6c6:.tools/eval_set/baseline.yaml
```

它后来在 `73eb8d4` 清理。已发现的问题包括：

- 角色 canonical tag 与裸名重复；
- `lightning` 在 TAGS/NL 重复，导致画面被闪电分割；
- 发明不存在的 `1teacher`；
- 没有主体的城市描述被擅自加入 `1girl`；
- 模糊输入被补成没有依据的舞台/情节。

结论：它只能叫“当时行为快照”，不能叫质量基准。

### 仍然有效的 A/B 方法

A/B 本身不是错误。有效前提是：

- 固定模型、workflow、尺寸、seed 和负面；
- 对比明确的两个候选策略，而不是把旧输出当黄金答案；
- 最终由人眼盲评图片；
- 结构测试只做廉价预检。

Phase 2.6 的生产 v5 新旧图像 A/B 经用户人眼确认结果为：`3 胜 / 2 平 / 0 负`。这支持保留当前 painter expansion，但同一阶段也发现：用户提供详细中文时，部分 case 仍强于自动补全。因此项目已冻结继续堆扩写规则。

---

## 8. Rating / NSFW 相关决定

旧 `build_prompt()` 会用固定英文关键词判断：命中则自动加 `explicit`，否则自动加 `safe`。翻译结果把明确意图弱化成 `exposing crotch` 时没有命中词表，于是错误追加 `safe`。

已完成并推送的修复（commit `80c245c`）：

- 删除自动 `safe/explicit` 推断；
- Reasoning/Vision Model 不自行输出 rating tag；
- 用户在可编辑英文 Prompt 中手动写入的 `safe/sensitive/questionable/explicit` 原样保留；
- 44 项 Prompt/LoRA 确定性测试和 `py_compile` 通过；
- 对应决定见 `docs/decisions.md::D44`。

仍保留的 `_prepare_painter_tags()` 裸体内容词保真和景别调整不是 rating 分类器，不要混为一谈。

### 最近一次真实使用观察

用户选择 DeepSeek LoRA 后输入“人物在床上没穿衣服”，最终图片确实没有女仆装，但 LLM 自行加入了 `hands on chest` 与 `legs together`，图片因此呈现为遮挡式、偏 SFW 的姿势。

代码核查：

- 这两个词在 `dict.yaml` 中分别对应“捂胸”“双腿并拢”；
- 用户原文没有这两个中文词，词典实际只命中 `on bed`；
- 没有后端安全代码固定注入这两个姿势；
- painter system prompt 会让 LLM 用 body language / reveal pacing 完善未指定姿态。

当前判断：更像 LLM 对模糊姿态进行“审美完整性 + 保守露出”的临场补全，不能仅凭输出证明触发了供应商硬性内容拦截。普通姿势词可以在英文编辑框删除，后端不会重新补回。

---

## 9. DeepSeek LoRA 与服装覆盖

当前 Registry 的 `deepseek_maid/maid`：

```text
required_tags = deepseek_whale_girl + deepseek_maid_outfit
default_tags = []
```

作者说明中的正面身份、正面全身、正面腰上、纯背面、侧面长清单已经改成条件 optional 配方，不再全部默认注入。该改动同样在 `80c245c`。

后端行为：

- required tags 由 `resolve_lora_selections()` 和 `compile_lora_bindings()` 确定性补回；
- 用户在英文 Prompt 中删除 `deepseek_maid_outfit`，入队/构建 workflow 时仍会重新出现；
- LLM 可以选择允许的 Profile/optional ID，但不能发明或删除 exact trigger。

用户真实测试又证明：即使 `deepseek_maid_outfit` 仍在最终 Prompt，Anima 也可能让明确的无衣服要求在视觉上压过女仆装。因此需要区分：

- **程序事实**：女仆 trigger 没有被删掉；
- **模型行为**：trigger 不是绝对锁定，模型可能执行用户服装状态；
- **尚未证明**：任意具体换装（西装、运动服、礼服）是否稳定，不混入围裙/头饰。

现阶段不必立即增加 identity-only Profile。先观察一两种差异明显的普通换装；只有频繁残留女仆元素时，再考虑显式“自由服装”Profile。

---

## 10. 下一次讨论最值得做的决策

### 待决策 A：Dictionary 是否继续决定 LLM 路由

候选方向：

1. **保持现状**：零成本，但全命中误判和不可纠错继续存在。
2. **普通中文默认进 Reasoning Model**：裸角色名、明确的原子属性列表保留快速路径；自然句即使全命中也让 LLM看完整语义。
3. **先只清词典，不改路由**：修最危险的宽泛项，风险较小，但路由缺陷仍在。

当前较合理但尚未批准的方向是：保留经过验证的原子 canonical mapping；普通自然句让 LLM参与；Dictionary 命中从“不可修改事实”降为可参考候选。

### 待决策 B：如何验证词典而不重开大工程

不建议逐项手工审计一千多条。可采用有限范围：

1. 机器检查 exact tag 是否存在、deprecated、post_count；
2. 人工检查高频、宽泛、会显著改变画面的 30～50 项是否语义等价；
3. 只对争议且视觉影响大的映射做少量固定条件出图；
4. 不把 API“存在”直接等同于中文映射正确。

### 待决策 C：是否允许 LLM 纠正 Dictionary hit

当前 LLM 被告知 Known tags 不要重复，但代码最终仍强制合并 hits。若开放纠错，需要先决定：

- LLM 是否可返回 `reject/replace` 的有限 ID；
- 还是只把未经验证的宽泛词不再预匹配；
- 如何保持白发/蓝眼/角色 tag 等稳定知识不被 LLM 改坏。

这比新增更多 Prompt IR 字段更直接，因为它回答了项目核心问题：**知识如何帮助模型，而不是如何把错误知识锁死。**

---

## 11. 新对话的建议开场

可以直接从下面这句话继续，不必重读全部历史：

> 请先只读检查 `translate()`、`match_dict_words()`、`dict.yaml` 和这份交接。我们要决定 Dictionary 应该继续作为全命中快速路径，还是降为可纠错候选。先比较最小改法、收益、API 成本和回归风险，不要立即实现，不要恢复旧 baseline，也不要启动 PromptState。

---

## 12. 相关文件

- `server/main.py`：实际翻译、IR、Painter、Dictionary/LLM 路由、LoRA binding
- `server/dict.yaml`：未经系统验证的属性/氛围/动作映射
- `server/char_dict.yaml`：角色 canonical mapping，职责与普通词典不同
- `server/lora_registry.yaml`：LoRA exact trigger、Profile、optional 配方
- `docs/architecture.md`：当前系统事实
- `docs/decisions.md`：D11/D15/D34/D37/D39/D44 等历史决定
- `docs/DEVLOG.md`：Phase 1～2.6、baseline 清理、rating 修复过程
- `docs/PLAN-v5 — AirPaint Prompt Intelligence.md`：历史实施路线；未来 Phase 不自动恢复
- `docs/PLAN-LORA.md`：已完成的 LoRA Context / Binding 工程
- `ROADMAP.md`：当前计划状态，但需结合用户在对话中“冻结后续 Prompt Phase”的最新决定理解

