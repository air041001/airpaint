# PLAN-LORA — LoRA Context 工程（项目最终任务）

> 状态：定稿待执行（2026-08-19）
> 合并 PLAN-v5 的 Phase 5（LoRA Context）/ Phase 6（Trigger Engine）/ Phase 7（LoRA Composition）为一个任务。
> 本文件是执行蓝本：下一个 agent 按 Step 0-8 顺序执行，遇到与本文冲突的旧文档以本文为准。

---

## 0. 核心问题（用户实测痛点）

1. **Prompt 空间失衡（最重要）**：全触发词盲拼 → 人物细节占满 prompt → 场景/光影/构图被挤压 → base Anima 的 dropout 只画好人物，画面不堪入目。**本质：不是"选哪些 trigger"，是"在有限 prompt 空间内平衡 LoRA tag 和场景描述"。**
2. **Onboarding 失效**：`deepseek_maid_outfit_illustrious_v10.safetensors` 从未注册成功。根因：loras 目录混入 Wan 视频 .safetensors（1.2GB×2），SHA256 现算超时 + Civitai trainedWords 大量为空 + LoraManager `.metadata.json` 的 civitai 字段一直是空。
3. **调用顺序错误**：当前先翻译（LLM 不知道选了什么 LoRA）→ 后盲拼 trigger。LLM 必须先知道 LoRA 上下文才能写出正确的 prompt。

## 1. 真实 LoRA 结构分析（基于两个实例）

### 实例 A：denia（鸣潮三变体，已在项目 config）

```
达妮娅（白）  trigger: denia \(wuthering waves\)
             快速使用: denia \(wuthering waves\), denia black headband, blue headpiece
             可选细节: white dress, frills, jewelry choker, fur-trimmed sleeves, ...
西格莉卡      trigger: sigrika \(wuthering waves\)
             快速使用: sigrika \(wuthering waves\), headrest, white headwear
达妮娅（黑）  trigger: blackdenia \(wuthering waves\)
             快速使用: blackdenia \(wuthering waves\), denia black headband, blue headpiece
独立部件:     arm tattoo / jewelry choker / blue chest tattoo（可正可负注入）
作者建议:     "想要快速产出，用快速使用的 tag 即可"
```

### 实例 B：辉夜姬三人组（三合一 VTuber LoRA）

```
辉夜        trigger: kaguya liver        + 长特征列表（发饰/发色/服装）
酒寄彩叶    trigger: Sakayori Iroha liver + 长特征列表
月见八千代  trigger: Yachiyo Runami       + 长特征列表
支持: 单人生成 + 组合触发词实现三人同框
```

### 实例 C：无触发词 LoRA

选择即生效（风格烘进权重），没有任何 trigger。

### 结构结论

LoRA 的正确抽象是**角色集合**，不是触发词列表：

```
LoRA
├── type: character / style / action / expression / none-trigger
├── description（作者原文，供人参考，不直接全文喂 LLM）
├── characters[]          # 风格/无触发词 LoRA 为空
│   ├── name（中文名，用于 LLM 语义匹配）
│   ├── trigger（核心触发词，必须逐字复制）
│   ├── quick_use（作者推荐最小组合 = trigger + 1~2 关键特征）
│   ├── features[]（可选细节 tag，用户明确描述时才加）
│   └── notes（可选：该角色的特殊用法，如独立部件说明）
└── multi_character: bool # 是否支持多人同框（组合 trigger）
```

**用户判断的黄金法则**（写进 LLM 规则）：大部分情况下人物 LoRA 只用一个人物 tag 最多 + 一两个细节就够用了。

## 2. Registry Schema：`server/lora_registry.yaml`

新文件，HotDict 热更新（存盘即生效，不重启），gitignore（机器本地路径）。

```yaml
denia:
  type: character
  name: "达妮娅/西格莉卡"
  file: "denia_lorav4-000005.safetensors"
  description: "（作者描述原文，人看）"
  multi_character: false
  characters:
    - name: 达妮娅（白）
      trigger: "denia \\(wuthering waves\\)"
      quick_use: "denia \\(wuthering waves\\), denia black headband, blue headpiece"
      features: [white dress, frills, jewelry choker, fur-trimmed sleeves, detached sleeves, sleeveless dress, red gloves]
      notes: ""
    - name: 西格莉卡
      trigger: "sigrika \\(wuthering waves\\)"
      quick_use: "sigrika \\(wuthering waves\\), headrest, white headwear"
      features: [white fingerless gloves, thigh strap, black shorts, off-shoulder dress, sandals, detached collar]
      notes: ""
    - name: 达妮娅（黑）
      trigger: "blackdenia \\(wuthering waves\\)"
      quick_use: "blackdenia \\(wuthering waves\\), denia black headband, blue headpiece"
      features: [blue gloves, black footwear, sleeveless dress, black dress, white bow, layered dress, blue thigh-high stockings, arm tattoo, blue chest tattoo]
      notes: "arm tattoo / blue chest tattoo 是独立部件，可按需注入正/负面"
  strength_model: 1.0
  strength_clip: 1.0
  preview: "/images/lora_previews/denia.png"

kaguya_trio:
  type: character
  name: "辉夜姬三人组"
  file: "kaguya_trio.safetensors"
  multi_character: true
  characters:
    - name: 辉夜
      trigger: "kaguya liver"
      quick_use: "kaguya liver, crescent hair ornament, rabbit ears"
      features: [long hair, low-tied long hair, very long hair, animal ears, crescent, blonde hair, yellow eyes, japanese clothes, kimono]
    # ... 彩叶/八千代同理

some_style:
  type: style
  name: "某种风格"
  file: "xxx.safetensors"
  characters: []        # 空 = 无角色
  trigger: ""           # 空 = 无触发词，选了就生效
  description: "无触发词，选择即生效"
```

### 兼容旧 config.yaml 条目

`get_lora_registry()` 三层合并，优先级：
```
lora_registry.yaml（新，热更新）> config.yaml loras（旧，重启生效）> civitai cache（自动）
```
- 同一 `file` 被 lora_registry 覆盖时，config 条目被 shadow（新结构优先）
- config 旧条目（单 `trigger` 字符串）自动转成内部结构：`characters: [{name, trigger, quick_use: trigger, features: []}]`
- 前端展示合并结果，`source` 字段标明来源

## 3. Onboarding 脚本：`.tools/register_lora.py`

### 用法

```
python .tools/register_lora.py                              # 列出未注册文件，交互选择
python .tools/register_lora.py deepseek_maid_outfit_illustrious_v10.safetensors
python .tools/register_lora.py --civitai https://civitai.com/models/xxxxx   # 自动抓描述+预览图
```

### 交互流程（核心：把作者描述蒸馏成结构）

```
1. 检测 type: character / style / action / expression / none-trigger
2. [无触发词 LoRA] 只填 name + description，结束
3. [有触发词] 逐角色录入:
   - 角色中文名: 达妮娅（白）
   - 核心触发词: denia \(wuthering waves\)     ← 要求逐字从描述复制
   - 快速使用组合: denia \(wuthering waves\), denia black headband, blue headpiece
   - 可选细节 tag（逗号分隔，回车跳过）: white dress, frills, ...
   - 继续录下一个角色?（多角色 LoRA 循环）
   - multi_character?（是否支持同框，如辉夜姬三人组）
4. 可选: strength 默认值
5. 写入 server/lora_registry.yaml → HotDict 下次访问即生效
```

### Civitai URL 自动抓取（可选增强，代理开着时用）

- 抓 model description + preview 图（下载到 `server/images/lora_previews/`）
- 抓到的描述**展示给用户看**，用户照着它手工填结构字段（**不自动解析描述**——D29 已论证格式不统一，自动解析误判更糟）
- 失败（无代理/超时）→ 降级为纯手动粘贴描述，不阻塞

### 明确不做

- 不自动解析 HTML description 成结构（D29 结论维持）
- 不自动调 Civitai API 的 trainedWords 当真相（大量为空/不全）
- 脚本是"人读描述 → 蒸馏结构"的辅助，人才是判断者

## 4. translate() 接收 LoRA 上下文

### 签名

```python
async def translate(text, reroll=False, image_b64=None,
                    lora_context=None, include_meta=False)
```

### lora_context 构建（`/api/translate` 路由内）

```python
lora_keys = body.get("loras") or []
lora_context = build_lora_context(lora_keys)  # 从 registry 取结构化元数据
```

### 传给 LLM 的上下文段（拼进 siliconflow_translate 的 context）

```
Active LoRA: 达妮娅/西格莉卡 (character)
Available characters:
- 达妮娅（白）: use "denia (wuthering waves), denia black headband, blue headpiece"
- 西格莉卡: use "sigrika (wuthering waves), headrest, white headwear"
- 达妮娅（黑）: use "blackdenia (wuthering waves), denia black headband, blue headpiece"
Optional detail tags (add ONLY if user explicitly describes them):
- 达妮娅（白）: white dress, frills, jewelry choker, ...
Notes: arm tattoo / blue chest tattoo 可独立注入
```

无触发词 LoRA 的上下文段：
```
Active LoRA: 某种风格 (style, no trigger words — active automatically, no tags needed)
```

## 5. 画师 System Prompt 增加 LoRA 规则段

PAINTER_SYSTEM_PROMPT 追加：

```
If Active LoRA context is provided:
- Character LoRA: include the quick-use tag combo for the character matching the
  user's intent, copied EXACTLY as given (backslash-escapes, parentheses, underscores).
  Default to quick-use only — one trigger plus one or two key details is enough.
  Add optional feature tags ONLY when the user explicitly describes those details.
- The LoRA tags must stay a SMALL part of PROMPT. Scene, lighting, composition and
  mood must still be fully described — never let character details crowd them out.
- Multi-character LoRA: if the user mentions several characters from the same LoRA,
  include each mentioned character's quick-use combo and add interaction tags.
- Style LoRA with trigger: always include the style trigger.
- Style LoRA without trigger: no extra tags needed; it is already active.
- If a character LoRA is active but the user's text clearly describes a different
  subject, still include one quick-use combo (the closest or first character).
```

设计要点：
- **"quick-use only by default"** 直接实现"一个 tag + 一两个细节就够用"
- **"stay a SMALL part of PROMPT"** 直接针对 dropout 痛点
- **"copied EXACTLY as given"** 针对格式改写（`denia \(wuthering waves\)` 的反斜杠/括号必须逐字）

## 6. build_prompt 调整 + 代码兜底

### 变化

| 层 | 旧行为 | 新行为 |
|---|---|---|
| prompt 层 | `trigger = ", ".join(triggers)` 盲拼 prepend | **不拼**（LLM 已把 quick_use 写进 PROMPT） |
| 工作流层 | lora_node widget 注入文件+强度 | **不变** |

### 代码兜底（防 LLM 失误，两层）

1. **格式校验**：PROMPT 中的角色 trigger 必须与 registry 逐字匹配（LLM 可能改写 `denia \(wuthering waves\)` → `denia wuthering waves` 丢转义）。不匹配时用 registry 的正确写法替换。
2. **缺席兜底**：选中了 character LoRA 但 PROMPT 里没有任何它的 trigger → 自动 prepend 第一个（默认）角色的 quick_use。防"选了 LoRA 却没出角色"。

### 向后兼容

- **无 lora_context 时**（暗房无 LoRA / 旧调用）：build_prompt 维持旧行为（registry trigger 盲拼），零破坏
- **旧 config 条目**：转内部结构后走同一条路

## 7. 前端适配

### 调用顺序（用户确认：这才是该有的工作流）

```
旧: 选LoRA → 翻译(LLM不知LoRA) → 提交(trigger盲拼)
新: 选LoRA → 翻译(带lora keys, LLM知LoRA, 写好quick_use) → 提交(不再拼trigger)
```

### 具体改动（web/index.html）

- `doTranslate()` 请求体加 `loras: getSelectedLoras()`
- "先看翻译"结果里能看到 LLM 选的 quick_use（可编辑）
- "直接生成"：translate（带 lora）→ submit
- 暗房 redo/tweak：session 里带 lora keys，重翻时传
- LoRA 选择器仍为 角色×1 + 风格×1（多角色同框靠单 LoRA 内多角色，不靠多选角色 LoRA）
- 预览卡片展示 characters 列表（名 + quick_use），无触发词 LoRA 标注"选择即生效"

## 8. 验证计划

### 单测（确定性，零成本）

- registry 三层合并优先级 + config→结构转换
- lora_context 构建（含无触发词/多角色/单角色）
- 格式校验（trigger 逐字匹配 + 错误写法替换）
- 缺席兜底（有 LoRA 无 trigger 时自动补 quick_use）
- 现有 23 测全部继续通过

### 真实 LoRA A/B（人眼，固定 seed）

| LoRA | 类型 | 验证点 |
|---|---|---|
| deepseek_maid | character 单人 | 脚本能注册；出图有女仆装且**场景完整**（不是只有人物） |
| denia | character 三变体 | "白毛达妮娅" → 只出白娅 quick_use 不堆细节；"黑皮" → 黑娅 |
| BlueArchiveStyleB1 | style | always-include 风格 trigger |
| 无触发词 style（如有） | none-trigger | 选了就生效，无多余 tag |

A/B 对照：
- A = 现状（全 trigger 盲拼）
- B = 新链路（LLM 精选 quick_use + 场景留空间）

**判定标准（用户人眼）**：B 的场景/光影/构图是否明显比 A 完整，人物是否仍正确。A 只有人物好场景烂即证明痛点、B 两者兼得即成功。

## 9. 执行步骤

| Step | 内容 | 依赖 |
|---|---|---|
| 0 | 扫描过滤 wan_/detailz 非 LoRA 文件；诊断 deepseek_maid 未注册原因（读 lora_cache.json） | 无 |
| 1 | `register_lora.py` 脚本（交互 + 可选 Civitai 抓取） | 无 |
| 2 | `lora_registry.yaml` + HotDict + registry 三层合并 | Step 1 |
| 3 | `translate(lora_context=...)` + `build_lora_context()` | Step 2 |
| 4 | PAINTER_SYSTEM_PROMPT LoRA 规则段 | Step 3 |
| 5 | build_prompt 不拼 trigger + 格式校验 + 缺席兜底 | Step 4 |
| 6 | 前端 doTranslate 带 lora keys + 预览展示 characters | Step 5 |
| 7 | 单测 + 4 LoRA 真实 A/B 人眼 | Step 6 |
| 8 | D39 ADR / DEVLOG 41 / ROADMAP / PLAN-v5 标注合并 / architecture / api.md / BUILDHANDOFF + push | Step 7 |

预估 2-3 个会话；LLM API 每次翻译 ~$0.001；Civitai 抓取免费。

## 10. 明确不做

- 不自动解析 HTML description（D29 维持）
- 不做 LoRA 冲突 ML 检测（两个 character LoRA 同选：前端限制角色×1 即可）
- 不做 trigger 权重自动优化（LLM 可写 `(tag:1.2)`，代码不干预）
- 不做 Phase 8 Workflow Intelligence / PromptState
- 不重建 30 条 baseline 类批量结构门槛（AGENTS.md §13 原则）
