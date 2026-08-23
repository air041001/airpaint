# PLAN-LORA — LoRA Context / Binding 工程（项目最终大工程）

> 状态：首版已完成并通过用户人眼验收（2026-08-23）
> 本计划取代 2026-08-19 版本。合并 PLAN-v5 的 Phase 5（LoRA Context）/ Phase 6（Trigger Engine）/ Phase 7（LoRA Composition），但仍按验证门逐步落地，不因“最终工程”而一次性铺开未验证能力。
> 核心决定见 D39：**LLM 选择 LoRA 语义/Profile，代码确定性编译 exact trigger。**
>
> 完成边界：LoRA Context、Profile/Trigger Binding、API/session 状态与角色×1 + 风格×1 的现有组合链已落地；跨文件多角色 LoRA composition 因没有真实资产与人眼证据，仍不宣称完成。

---

## 0. 最终目标与硬约束

用户选中 LoRA 后，Prompt 规划模型必须在翻译前知道：

- 选中了什么类型的 LoRA；
- 它提供哪些人物、服装、风格或概念；
- 哪些内容已经由 LoRA 提供，不应在普通 Prompt 中重复或冲突；
- 画面仍需要补足哪些动作、场景、构图、光影和氛围。

目标链路：

```text
User Intent + Selected LoRA/Profile
                ↓
        LoRA-aware Painter LLM
        （理解能力与语义，不决定文件/权重/exact trigger）
                ↓
       IR + PROMPT + semantic binding
                ↓
        LoRA Binding Compiler
        （校验 Profile、编译 exact trigger、去重、冲突护栏）
                ↓
       Final Prompt + Workflow LoRA Injection
```

### Hard Rules

1. **不割裂**：不能先生成一套与 LoRA 无关的 Prompt，再在生成阶段偷偷拼另一套 trigger。
2. **不串概念**：LoRA 已提供的角色/服装/风格必须进入 LLM 上下文；Prompt 不得无意识加入相冲突的身份或外观。
3. **LLM 不复制 exact trigger**：LLM 只返回 registry 中允许的 `profile_id` / 可选语义选择；反斜杠、括号、文件名和精确 tag 由代码处理。
4. **代码不猜语义**：代码不根据字符串长度或 tag 前 N 项判断角色变体；语义选择由用户显式选择或 LLM 在候选 ID 中选择。
5. **没有“默认第一个角色”**：无法匹配时只允许使用 registry 明确声明的 `default_profile`，否则返回 warning/要求用户选择，不静默取数组第一项。
6. **有 LoRA 时必须 LoRA-aware**：文本快速路径、词典全命中、参考图、reroll、暗房 redo/tweak 都必须携带同一份 LoRA selection；不得只有普通文本 LLM 路径生效。
7. **不让 LLM 决定数值**：LoRA strength 仍来自 registry 默认值或前端滑块；LLM 不生成 `(tag:1.2)` 等任意权重。
8. **Prompt 空间留给画面**：每个 Profile 自己声明经验证的 minimal tags，不设跨 LoRA 通用“前 N 个 tag”规则；剩余空间优先留给动作、场景、构图、光影和氛围。

---

## 1. 当前事实与问题诊断

### 1.1 已验证的现有能力

- `build_prompt()` 已能向 LoraManager 节点 5 的 `loras.__value__` 注入多条 `{name,strength,clipStrength,active}`；节点含义与连接以 `docs/workflow-anatomy.md` 为权威，不再重复猜节点或无必要翻 custom node 源码。
- 前端已支持角色 LoRA ×1 + 风格 LoRA ×1，以及独立 strength。
- `get_lora_registry()` 已合并 config 与 Civitai cache。
- 当前 LoRA 实际有效，问题集中在知识质量、选择语义和 Prompt 编译，不是 workflow 加载格式。

### 1.2 当前失败不是单一“未注册”

`deepseek_maid_outfit_illustrious_v10.safetensors` 已存在于 `server/lora_cache.json`，并有 4 组 trainedWords；它未出现在前端的直接原因是：

```text
Civitai tags 为空 → type=unknown → /api/loras 只返回 character/style → 前端不可见
```

同时存在以下扫描问题：

- cache 以 stem 为 key，命中后永久跳过；失败项不能正常重试；
- 同 stem 文件被替换时缺少 size/mtime/hash 指纹失效；
- 未优先利用 `.metadata.json` 的 `base_model` 排除 Wan 视频 LoRA；
- `refresh` 对 cached failure 不是实际 force refresh；
- unknown/incomplete 条目被 API 隐藏，而不是展示为“待配置”。

### 1.3 当前 Prompt 痛点

现状：

```text
translate() 不知道 LoRA
→ build_prompt() 把每条 trigger 全量 prepend
→ 人物/服装细节挤压场景、构图与光影
→ Prompt 语义可能与选中的 LoRA 相互冲突
```

真实例子还说明 Civitai `trainedWords` 不能直接使用：一个数组元素本身可能是一整段逗号列表，`", ".join(trainedWords)` 会把整套人物细节全部塞进 Prompt。

---

## 2. 正确抽象：LoRA Asset + Semantic Profile

LoRA 文件与用户想启用的概念不是同一层。

```text
LoRA Asset（一个 safetensors 文件）
├── 文件、类型、默认强度、来源
├── trigger_policy: profile | required | none
└── profiles（文件内可用的人物/服装/概念变体）
    ├── id / name / aliases
    ├── provides（它已经提供什么）
    ├── required_tags（exact，始终需要）
    ├── default_tags（经人工验证的 minimal 补充）
    ├── optional_tags（用户明确要求才加入）
    └── source / verified / notes
```

这比 `characters[] + quick_use 字符串` 更通用：角色、服装、动作、表情、风格都能用 Profile 表达；`required_tags + default_tags` 也避免 trigger 与 quick_use 重复存储。

### 2.1 Registry Schema

新增并纳入版本控制：`server/lora_registry.yaml`。

它是人工蒸馏后的项目知识资产，不含 token/key，不应 gitignore。自动扫描结果 `server/lora_cache.json` 继续 gitignore。

```yaml
schema_version: 1

loras:
  denia:
    name: "达妮娅 / 西格莉卡"
    type: character
    file: "denia_lorav4-000005.safetensors"
    trigger_policy: profile
    default_strength:
      model: 1.0
      clip: 1.0
    selection:
      default_profile: white
      allow_multiple_profiles: false
    profiles:
      white:
        name: "达妮娅（白）"
        aliases: ["达妮娅", "白达妮娅", "白娅"]
        provides: ["character identity", "black headband", "blue headpiece"]
        required_tags: ["denia \\(wuthering waves\\)"]
        default_tags: ["denia black headband", "blue headpiece"]
        optional_tags:
          white_dress:
            name: "白裙完整细节"
            provides: ["white dress", "frilled clothing"]
            tags: ["white dress", "frills", "jewelry choker", "fur-trimmed sleeves"]
        source: "author description"
        verified: curated
        notes: ""
      sigrika:
        name: "西格莉卡"
        aliases: ["西格莉卡"]
        provides: ["character identity", "headrest", "white headwear"]
        required_tags: ["sigrika \\(wuthering waves\\)"]
        default_tags: ["headrest", "white headwear"]
        optional_tags: {}
        source: "author description"
        verified: curated
        notes: "迁移时修复旧 config 中 wutherng 拼写"
      black:
        name: "达妮娅（黑）"
        aliases: ["黑达妮娅", "黑娅", "阿列夫一形态"]
        provides: ["character identity", "black form", "black headband", "blue headpiece"]
        required_tags: ["blackdenia \\(wuthering waves\\)"]
        default_tags: ["denia black headband", "blue headpiece"]
        optional_tags:
          arm_tattoo:
            name: "手臂纹身"
            provides: ["arm tattoo"]
            tags: ["arm tattoo"]
          chest_tattoo:
            name: "蓝色胸口纹身"
            provides: ["blue chest tattoo"]
            tags: ["blue chest tattoo"]
        source: "author description"
        verified: curated
        notes: ""
    legacy_keys:
      denia_white: white
      denia_sigrika: sigrika
      denia_black: black

  blue_archive_style:
    name: "蔚蓝档案风格"
    type: style
    file: "BlueArchiveStyleB1.safetensors"
    trigger_policy: required
    required_tags: ["@BlueArchStyle"]
    provides: ["Blue Archive visual style"]
    default_strength: {model: 1.0, clip: 1.0}
    source: "manual config"
    verified: curated

  no_trigger_style_example:
    name: "无触发词风格"
    type: style
    file: "example.safetensors"
    trigger_policy: none
    required_tags: []
    provides: ["style baked into LoRA weights"]
    default_strength: {model: 1.0, clip: 1.0}
    source: "author description"
    verified: candidate
```

### 2.2 字段边界

- `type` 表示语义类别；“无触发词”是 `trigger_policy:none`，不是一种 type。
- `provides` 给 LLM 理解能力，不直接全文变成 Prompt。
- `required_tags/default_tags` 只能由人工或已验证来源写入；LLM 不改写。
- `optional_tags` 以稳定 option ID 保存 `provides + exact tags`；只有用户明确描述对应细节时，LLM 才能返回允许的 option ID，代码再解析 exact tags。
- `verified:candidate` 不等于生产验证；真实出图人眼通过后再提升为 `verified`。
- `allow_multiple_profiles` 只声明 registry 能否组合，不宣称 base Anima 一定能稳定画好多角色。
- `legacy_keys` 保留旧 API/config key，避免迁移后 `denia_white` 等旧请求突然失效。

### 2.3 Loader

不能复用当前 `HotDict`：它会执行 `str(v).strip()`，把嵌套对象压扁。

新增独立 `HotLoraRegistry`：

- 保留完整 dict/list；
- mtime 变化后整体加载、整体校验、整体替换；
- YAML 半写入或 schema 错误时保留上一份有效 registry；
- onboarding 写入时使用临时文件 + 原子替换；
- 提供稳定的 `registry_revision`（规范化内容 hash），供 translate/jobs 防止上下文版本错位。

---

## 3. Selection 与 Binding 数据契约

### 3.1 用户选择

新请求字段：

```json
{
  "lora_selections": [
    {"key": "denia", "profile": "white", "mode": "explicit"},
    {"key": "blue_archive_style", "mode": "explicit"}
  ]
}
```

- `mode=explicit`：用户明确选择 Profile，LLM 不得换成别的 Profile。
- `mode=auto`：用户只选择 LoRA Asset，LLM 必须从 registry 提供的候选 `profile_id` 中选择。
- 无匹配时：使用明确声明的 `default_profile` 并返回 warning；没有 default 则要求用户选择。
- 旧 `loras:[key]` / `lora:key` 继续接受，并转换为 legacy binding；不得直接破坏旧客户端。

### 3.2 `/api/translate` 返回

```json
{
  "prompt_en": "最终可编辑 Prompt（已包含确定性 LoRA tags）",
  "lora_bindings": [
    {
      "key": "denia",
      "profile": "white",
      "optional": ["white_dress"],
      "resolved_by": "explicit",
      "injected_tags": ["denia \\(wuthering waves\\)", "denia black headband", "blue headpiece"]
    }
  ],
  "lora_warnings": [],
  "registry_revision": "..."
}
```

`prompt_en` 仍是用户看见和编辑的最终正向 Prompt；同时返回结构化 binding，让 `/api/jobs`、暗房和回归工具不必从 Prompt 字符串反推选择。

### 3.3 `/api/jobs` 与 revision

前端提交 translate 返回的 `lora_bindings + registry_revision`：

- revision 相同：代码幂等确认 exact tags，并加载对应文件；
- 客户端回传的 `injected_tags` 只用于展示/诊断，不作为真相；jobs 根据 key/profile/optional ID 从当前同 revision registry 重新解析 exact tags；
- registry 在预览与提交之间已变化：返回明确的 stale warning/409，要求重新翻译，避免 Prompt 与权重来自两版知识；
- required tag 被用户手动删除但 LoRA 仍选中：代码重新补回；要完全手动控制必须显式取消 LoRA，第一版不增加隐蔽 manual mode。

---

## 4. LoRA-aware Painter 协议

### 4.1 上下文内容

传给 LLM 的不是文件名或 trigger 长串，而是：

```text
ACTIVE LORA: denia (character)
Selection mode: explicit
Active profile: white / 达妮娅（白）
This LoRA already provides: character identity, black headband, blue headpiece
Optional supported details: white dress, frills, jewelry choker, fur-trimmed sleeves

Rules:
- Plan the remaining action, pose, scene, composition, lighting and mood around this LoRA.
- Do not invent a different character identity or conflicting appearance.
- Do not copy or rewrite trigger strings; the backend injects exact tags.
- Use optional details only when the user explicitly asks for them.
```

风格/无 trigger LoRA 同样进入上下文，让 LLM 知道风格已经由权重提供，不再额外堆一套冲突画风。

### 4.2 输出协议

保留生产 `IR + PROMPT`，有 active LoRA 时允许增加结构化语义行；`mode=auto` 必须返回 Profile，explicit Profile 只能补充允许的 optional option ID，不能改 Profile：

```text
IR: {...12 fields...}
LORA: {"denia":{"profile":"white","optional":["white_dress"]}}
PROMPT: ...
```

- `LORA` 只能引用上下文给出的 key/profile/optional ID；代码严格校验，不接受自由 tag 字符串。
- explicit Profile 的 binding 由请求锁定，模型输出不能覆盖。
- `PROMPT` 不包含 registry exact trigger，由 Compiler 后置注入。
- parser 继续兼容无 `LORA` 的旧 `IR + PROMPT`、旧 TAGS/NL 和视觉协议。

### 4.3 覆盖所有翻译路径

只改 `PAINTER_SYSTEM_PROMPT` 不够，必须统一处理：

1. **文本 + Active LoRA**：即使角色/词典全命中，也强制进入 LoRA-aware painter 路径；否则模型根本不知道 LoRA，无法避免冲突。
2. **参考图 + Active LoRA**：vision context 同样加入 `provides/profile`；视觉模型描述参考图时不得重新引入冲突主体/风格，之后仍走 Binding Compiler。
3. **reroll**：只能改变场景/构图/光影等可变部分；explicit binding 保持锁定。
4. **暗房 redo/tweak/vibe**：session 保存 binding snapshot；重翻译复用，不从当前页面临时选择重新猜。
5. **无 LLM/服务失败降级**：仍可确定性注入 binding，但返回 `lora_context_degraded` warning；不得伪装为完成了语义冲突检查。

### 4.4 Character Knowledge 协同

- LoRA Profile alias 命中后，该人物由 LoRA knowledge 提供，不再把同一名字当未知角色送 Danbooru 查询。
- char_dict 已命中且与 LoRA Profile 是同一人物时，Compiler 去重并保留精确 binding。
- char_dict 人物与 active character LoRA 明显不同：LLM 返回 conflict warning，不静默加入两个身份。
- 代码只处理可验证的 tag 去重/显式 conflict 列表；开放语义冲突仍由 LLM 判断并在 `lora_warnings` 暴露。

### 4.5 缓存

翻译缓存 key 必须包含：

```text
普通翻译 context
+ normalized lora selections
+ resolved explicit profiles
+ registry_revision
+ vision/text mode
```

不同 LoRA、不同 Profile 或 registry 更新后的 Prompt 绝不能共享缓存。

---

## 5. LoRA Binding Compiler

新增纯函数边界（命名可按实现微调）：

```python
resolve_lora_selections(selections, registry, llm_binding=None)
compile_lora_bindings(prompt_en, resolved_bindings, user_intent=None)
```

职责：

1. 校验 key/profile/type/组合限制；
2. 从 registry 取 exact `required_tags + default_tags`；
3. 对 optional tags 只接受 registry 白名单中的语义选择；
4. 删除 LLM 对 provided tags 的重复/近似复述；
5. 按确定性顺序将 LoRA tags 合入最终 Prompt；
6. 幂等：同一 binding 编译两次结果不重复；
7. 返回 `lora_bindings/lora_warnings`，不靠最终字符串保存状态；
8. workflow 层仅根据 resolved binding 写 LoraManager 文件与 strength。

最终边界：

```text
Prompt Compiler：普通画面语义
LoRA Binding Compiler：LoRA 提供概念 + exact tags
build_prompt：quality/safety + 已编译 Prompt + workflow 文件/强度/节点注入
```

`build_prompt()` 不再承担“从一段任意 trigger 字符串猜怎么补”的职责；旧 config 条目先转换为 legacy Profile，再走同一编译器。

---

## 6. Scanner 与 Onboarding

### 6.1 Scanner 修复

先修 inventory，再做 onboarding：

- 读取 `.metadata.json` 的 sha256/base_model/size/mtime，优先排除 Wan 等非图片模型，避免对 1GB 文件现算 hash；
- fingerprint 至少包含 file size + mtime，变化后重新 lookup；
- cache 保存 `status: resolved|incomplete|failed|excluded` 与失败时间；
- `refresh` 可重试 failed/incomplete，提供明确 force 语义；
- unknown/incomplete 进入 `/api/loras.other`，前端显示“待配置”，不再消失；
- Civitai trainedWords 作为 candidate 展示，绝不直接视为 curated quick-use；
- 立即清理现有 `detailz-wan`、`wan_lightx2v_*` 残留，并确认当前后端是否仍运行旧进程。

### 6.2 Onboarding 工具

在 schema/loader/validator 已存在后新增 `.tools/register_lora.py`：

```text
python .tools/register_lora.py --list
python .tools/register_lora.py <filename>
python .tools/register_lora.py --edit <lora_id>
python .tools/register_lora.py --validate
python .tools/register_lora.py --civitai <url>
```

流程：

1. 展示本地 metadata、cache、Civitai candidate 和作者描述；
2. 用户判断 type / trigger_policy；
3. 逐 Profile 录入 name/aliases/provides/required/default/optional tags；
4. 录入 source/verified/default strength；
5. schema 校验；
6. 输出 diff 预览；
7. 原子写入 versioned `lora_registry.yaml`。

明确不做：

- 自动把 HTML description 解析成正式 Profile；
- 自动把 trainedWords promote 为 verified；
- 自动决定任意权重；
- 未经人眼验证自动提升 `verified`。

---

## 7. 前端与暗房

### 7.1 选择体验

- 角色 LoRA ×1 + 风格 LoRA ×1 的现有限制先保留。
- 单 Profile LoRA：直接选择。
- 多 Profile LoRA：显示二级选择；默认可为 `自动判断`，专家用户可明确锁定 Profile。
- 卡片显示 `provides`、minimal tags 摘要、verified 状态；无 trigger 标注“权重生效，无需触发词”。
- unknown/incomplete 显示但禁用生成，提供“待注册”说明。

### 7.2 调用顺序

```text
选择 LoRA/Profile
→ /api/translate 携带 lora_selections
→ 返回 final prompt_en + bindings + revision + warnings
→ 用户可编辑 Prompt
→ /api/jobs 携带同一 bindings + revision
```

必须覆盖：直接生成、先看翻译、reroll、参考图、确认生成、暗房 start/redo/tweak/vibe。

### 7.3 防状态错位

- 翻译完成后若用户更换 LoRA/Profile，当前 Prompt 标记为过期，提交前重新翻译。
- `start-image` 从原 JOB 复制 binding snapshot，不读取当前全局选择。
- session 中保存 selections/resolved bindings/revision；每轮 turn 与实际注入保持一致。
- job/status/API 记录 binding key/profile，便于诊断“Prompt 对了但 LoRA 选错”或反之。

---

## 8. 验证计划

### 8.1 确定性单测

至少覆盖：

- nested registry 正常加载、热更新、schema 错误保留旧版本；
- registry revision 稳定、变更后 cache 隔离；
- config legacy 多 key/同 file → Profiles + legacy alias；
- scanner metadata 预过滤、failed 重试、fingerprint 失效、unknown 可见；
- explicit Profile 锁定、auto 候选校验、无匹配不取“第一个”；
- exact trigger 编译、转义保留、幂等去重；
- optional tag 白名单；
- active LoRA 强制覆盖裸角色/词典全命中快速路径；
- text/vision/reroll/dialog 全部携带 binding；
- LoRA alias 不触发重复 Danbooru character lookup；
- preview 后切换 LoRA 的 stale revision；
- 现有 23 个 Prompt 单测继续通过。

### 8.2 Prompt 结构检查

结构检查只证明链路正确，不证明画质：

- LLM context 中能看到 selected LoRA 的 `provides`；
- LLM PROMPT 不复制 exact trigger；
- Compiler 后的 final Prompt 精确包含 required/default tags；
- scene/composition/lighting/mood 没有因 LoRA context 消失；
- binding 与 workflow 注入的文件/profile/strength 一致。

### 8.3 真实 LoRA A/B（固定条件、人眼验收）

优先使用当前真实存在的 LoRA：

| LoRA | 验证点 |
|---|---|
| deepseek_maid | unknown/incomplete 能 onboarding；人物/女仆概念正确，同时场景完整 |
| denia | explicit 白/黑/西格莉卡不串；auto 只从允许 Profile 选择；旧 key 兼容 |
| BlueArchiveStyleB1 | 风格已知，普通 Prompt 不再额外堆冲突风格 |
| 无 trigger style | 仅权重生效，final Prompt 不出现伪 trigger（有真实资产时执行） |

对照：

```text
A = legacy：translate 不知 LoRA + build_prompt 全 trigger 盲拼
B = aware：LLM 知 provides/Profile + Binding Compiler minimal exact tags
```

固定 base Anima/workflow/negative/seed/尺寸，只改变 LoRA Prompt 链。每个关键 case 先 1 个固定 seed；若人物正确但场景/构图差异可能是随机因素，再换 seed 复检，不重建 Phase 2 式批量结构门槛。

人眼分别判断：

1. Profile/人物/风格是否正确；
2. 是否出现身份、发色、服装或风格串线；
3. 场景、构图、光影是否比 legacy 完整；
4. Prompt 中用户明确动作/约束是否保留；
5. 偶发手脚问题与重复的策略失败分开记录。

当前本机没有辉夜姬三人组文件：多 Profile schema 用 fixture 做结构测试，但多人 composition 不作为首版生产验收门；有真实可测资产后再开启组合验证。

---

## 9. 执行步骤与验收门

Step 0-10 已于 2026-08-23 完成。最终实现包含 versioned Registry/热加载、scanner inventory、legacy adapter、Selection Resolver、Binding Compiler、LoRA-aware text/vision、translate/jobs/dialog revision snapshot、前端 Profile/stale UX、onboarding 工具和真实固定条件 A/B。

| Step | 内容 | 通过条件 |
|---|---|---|
| 0 | 资产/缓存审计 + D39 + 迁移映射确认 | deepseek/wan/denia 根因与旧 key 映射明确 |
| 1 | Registry schema + validator + HotLoraRegistry | nested/热更新/失败保旧/版本 hash 单测通过 |
| 2 | Scanner/inventory 修复 | 非图片预过滤、失败可重试、unknown 可见 |
| 3 | config 迁移 + legacy adapter | 现有 LoRA 列表和旧 key 零破坏 |
| 4 | Selection resolver + Binding Compiler | exact/幂等/无默认第一项/冲突 warning 单测通过 |
| 5 | LoRA-aware text/vision 协议 + cache | 所有翻译路径知道 selection；cache 不串 |
| 6 | API/jobs/dialog binding snapshot | translate→job→workflow 使用同一 binding/revision |
| 7 | 前端 Profile/auto/stale UX | 直接/预览/reroll/参考图/暗房完整 smoke |
| 8 | Onboarding 工具 | deepseek 可人工蒸馏并原子写 registry |
| 9 | 真实 A/B + 用户人眼 | LoRA 不串、人物正确、场景/构图不退化 |
| 10 | 文档闭环 + push | D39 验证、architecture/api/DEVLOG/ROADMAP/BUILDHANDOFF 同步，worktree clean |

### 9.1 最终验收结果

- 确定性验证：`41 prompt unit tests passed`；覆盖 nested registry、坏 YAML 保留 last-good、legacy key、Profile/optional 白名单、revision 409、scanner 预过滤与本地 `.civitai.info`、Binding 幂等、text/vision/cache/jobs/dialog 贯通。
- 前端验证：内联 JavaScript 语法通过，81 个元素引用无缺失；直接生成、先看翻译、reroll、参考图、Profile 自动/锁定、切换后 stale 提示和暗房 binding snapshot 已做 smoke。
- 真实 A/B：5 组最终为 aware `1 胜 / 4 平 / 0 负`。唯一明确胜出是服装 Profile 语义组；其余人物、风格、光影与 DeepSeek Anima 组均为平局，没有 LoRA-aware 导致场景/构图退化。
- DeepSeek：旧 Illustrious 资产换为 Anima 专用 LoRA；同作者身份/女仆装语义以 0.85 绑定，图书馆 aware/legacy 两张均被用户接受，且 Anima 整体优于原 IL 版本。作者水下花园 Prompt 只作 LoRA 控制组，不参与图书馆语义胜负。
- 光影反馈：`明亮午后` 从旧的 golden/lazy 午后词条中分离为 clear daylight/high sun/crisp shadows；Blue Archive 两张复测光线均正常。
- 边界：多 Profile schema 已验证，角色×1 + 风格×1 可组合；没有真实多人 LoRA 资产，因此跨文件多角色 composition 仍留待未来证据触发。

开发时可用内部 feature flag/实验参数保留 `legacy` 与 `aware` 两条链，A/B 通过后再把 `aware` 设为生产默认；验证失败不 push 失败状态。

---

## 10. 首版明确不做

- 不让 LLM 决定文件名、节点 ID、strength 或任意权重；
- 不要求 LLM 逐字复制/改写 trigger；
- 不自动解析 HTML description 成正式知识；
- 不把 Civitai trainedWords 当正式 quick-use；
- 不做 LoRA marketplace、推荐 ML、冲突 ML 或 embedding/vector DB；
- 不做跨文件两个 character LoRA 的自由组合（前端仍限制角色 ×1）；
- 没有真实多人 LoRA 资产前，不宣称完成多角色 composition；
- 不借 LoRA 工程启动 PromptState、semantic negative、weighted NL 或 Workflow Intelligence；
- 不用“Prompt 更长”“IR 更满”“结构通过”替代真实图片人眼验收。

最终成功标准只有一个：

> **用户选择的 LoRA、模型规划的 Prompt、代码编译的 trigger 与工作流实际加载的权重表达同一套画面意图；人物/风格不串，同时给动作、场景、构图和光影留下足够表达空间。**
