# AirPaint 「中文意图 + LoRA 用法」P0 契约（rev.2）

日期：2026-09-11（rev.2，逐条回应 `docs/LORA_USAGE_CONTRACT_REVIEW.md` R1–R6）
状态：**设计契约，不代表功能已实现或验证**。
依据：`docs/LORA_USAGE_EXECUTION_PLAN.md` + 审阅文件 R1–R6；代码以本次只读定位为准（HEAD `f8981da`）。真实事实与设计判断分开标注；找不到证据处明确写「未知」。

> rev.2 修正：R1 新增普通路径隔离；R2 修订编辑后语义；R3 统一最终化步骤；R4 纠正 revision/备份结论并固定正文持久化方案；R5 补齐暗房 `/api/dialog/turn` 链路、更正 detailer 结论、固定负面写入点；R6 放开资料表示并拆来源/验证维度。并修正 rev.1 三处事实错误：①「换 seed/重绘统称 `/api/jobs` 直投」②「备份天然覆盖、无需改动」③「detailer 无独立文本来源」。

## 0. 事实基线与修订摘要

- `git status --short` 仅有未跟踪的 `docs/LORA_USAGE_EXECUTION_PLAN.md`、`docs/LORA_USAGE_CONTRACT_REVIEW.md`（本契约文档亦未跟踪）；HEAD = `f8981da`；分支 `main`。
- `git ls-files -v server/lora_registry.yaml` = `S`：本机 Registry 带 skip-worktree 标记。**契约期内不修改、不暂存、不取消该标记、不覆盖、不自动同步真实 Registry**。
- 本机 ComfyUI：`E:\ComfyUI_windows_portable\ComfyUI\`。本轮已读源码：LoraManager `py\nodes\lora_loader.py`；Impact Pack `modules\impact\impact_pack.py`（`ImpactWildcardProcessor` L2671、`EditDetailerPipe`/`FaceDetailerPipe` wildcard）、`impact_server.py`（`onprompt` L603、`onprompt_populate_wildcards` L511）、`wildcards.py`（`process` L537）、`core.py`（`enhance_detail` L267-278）。
- 测试/检查入口（`.tools/`）：`test_prompt_unit.py / test_lora_composition.py / test_lora_onboard_agent.py / test_persistence_recovery.py / test_image_iteration.py / check_frontend.js / test_frontend_state.cjs`；`compileall -q server`。不跑泛化 `test_e2e.py` 或批量生图脚本凑验证。

| 审阅项 | 处理位置 |
|---|---|
| R1 普通路径隔离 | §4.8 + §7 替身校对 |
| R2 预设与编辑 | §4.1 + §4.2 |
| R3 统一最终化 | §4.3 |
| R4 revision/备份/正文 | §3.4 + §4.5 + §5 |
| R5 链路补齐/detailer/负面写入点 | §2.2 + §1.4 + §4.2 + §2.4 |
| R6 资料表示/来源/验证维度 | §4.4 |

## 1. 已核实事实：workflow 文本编码路径与真实节点行为

### 1.1 正向文本（实际生效）

- `build_prompt()`（`server/workflow_engine.py` L154-155）把 `quality_prefix + prompt_en` 作为**字面值写入节点 54 `CLIPTextEncode.text`**，切断 `[48,0]` 上游连接；节点 46/48/51 仍执行但不接入输出（节点 58 已在 `sanitize_for_api` 删除）。
- **当前唯一生效的正向写入点 = 节点 54 text 字面值**；质量前缀只存在于该注入值里，不落 `state_snapshot`，仅 `request_snapshot_json` 保留字节证据。
- KSampler(6).positive = `[54,0]`；ToDetailerPipe(19).positive = `[54,0]`。

### 1.2 负向文本（事实 + 关键机制 + 缺口）

- 节点 4（ImpactWildcardProcessor, NEGATIVE）→ 节点 55 CLIPTextEncode.text = `[4,0]` → KSampler(6).negative = `[55,0]`；ToDetailerPipe(19).negative = `[55,0]`。
- **本机 worklow 里节点 4 的 `wildcard_text` 与 `populated_text` 不同**（已 dump）：`wildcard_text` 末尾为 `… watermark, artist name,`；`populated_text` 在其后多 `multiple views, split view, grid view, cropped, out of frame, `。
- **关键机制（已读源码）**：`ImpactWildcardProcessor.doit` 处理的是 `populated_text`（`impact_pack.py` L2701）；而 `impact_server.py::onprompt_populate_wildcards`（L511-551）在 ComfyUI `onprompt` hook（L603-608）中，对 `mode == "populate"` 的节点执行 `inputs['populated_text'] = wildcards.process(inputs['wildcard_text'], seed)` 并把 `mode` 改为 `reproduce`；**该 hook 在 `/prompt` 提交时执行（含 bare API 直投）**，seed 取节点 4 的 seed 连接（节点 34 = 本次 seed）。
- 结论：**默认负面的真实生效值 = `wildcards.process(node4.wildcard_text, seed)`，不是 JSON 里静态保存的 `populated_text`**（后者是上次前端写入、会被 hook 覆盖）。当前 `wildcard_text` 无 `{a|b}`/`__wildcard__` 随机语法 → 展开近似原值；**若将来含随机语法，默认负面会随 seed 变化**。
- `build_prompt()` 的 `negative_text` 分支（L120-133）仅在 config 配备 `negative_text_node` 时把文本**追加**到模板尾部；**生产 config 未配备 → 当前无负面覆盖链路、无空值清空语义、前端无完整负面字段**。

### 1.3 LoRA 加载与 trigger（真实节点源码）

- LoraManager `LoraLoaderLM.load_loras()`（`lora_loader.py` L149-165）：`text` required 输入被 `del text` **丢弃**；真正加载 = 上游 `lora_stack` + widget `loras`（`{"__value__": [...]}`）。后端注入走 widget（`build_prompt` L149）。
- 返回 `(MODEL, CLIP, trigger_words, loaded_loras)`；`#3` 只进已删除的 metadata；`#2` 接节点 37→46，**不参与当前生效文本**（54 已切断 48）。
- **trigger 生效目前是纯文本层**：`compile_lora_bindings(prompt_en, bindings)`（`server/lora.py` L743-775，幂等：先剔除已有同 key 段再插入 count 之后）。物理文件加载只认服务端 bindings，**不存在从文本标签加载任意文件**。

### 1.4 detailer 局部文本（rev.1 结论更正）

- **更正**：rev.1 称「detailer 无独立文本来源」不准确。事实：
  - detailer 基础正负 conditioning 来自 ToDetailerPipe(19) ← 54/55（与主采样同源）。
  - 但 EditDetailerPipe(14-17) 的 `wildcard` 会在 detailer 阶段**追加局部文字**：14 `[CONCAT] hand, perfect hands`；15 `[LAB]\n[ALL] nsfw\n[NIPPLES] nsfw, nipples\n…`；16 `[CONCAT] {face|face,detailed face}`；17 `[CONCAT] {eyes|eyes,detailed eyes}`。
  - 源码：`FaceDetailerPipe.do_detail`（`impact_pack.py` L281-286）识别 `[CONCAT]` → `wildcard_concat_mode='concat'`；`core.enhance_detail`（`core.py` L267-278）concat 模式下 `ConditioningConcat().concat(positive, wildcard_positive)` → **局部文字追加到该 SEG 的 positive conditioning**。
- 结论：detailer 每个检测区域的正面 = 主正向 conditioning + **EditDetailerPipe 固定局部标签**；这些标签**后端从不修改**，也**不在用户编辑的「主正向文本」里**。契约只对主采样文本（节点 54/55）承诺「最终正负可见/可编辑」；detailer 局部标签单独说明，不在本轮接管范围。
- 负面：detailer 复用 19.negative = `[55,0]`，**故覆盖节点 55 后 detailer 自动继承同一负面**，无需 detailer 专用负面注入。

### 1.5 双重文本编译（职责重叠，P1 消除）

- `_enqueue`（`api.py` L749）已 `compile_lora_bindings(prompt_en, resolved_bindings)` 并存入 `job["prompt_en"]`；`_job_request`（L163-174）把该值 + `lora_bindings` 传给 `build_prompt`，其 L137-150 再次 resolve + 编译。
- 因 compiler 幂等，**不重复注入 trigger**；但同一文本经两次「resolve + 编译」。契约（§4.3）：**文本编译只在一次最终化步骤发生；workflow builder 不再改文本**。

## 2. 已核实事实：全部生成/分支链路（rev.1 §2 更正并补齐）

### 2.1 工坊主界面（`/api/jobs` 直投）

`/api/jobs` 只要求非空 `prompt_en`（L867），不强制翻译、不要求 `prompt_ir`；`prompt_raw`（中文）仅存档。前端 confirm 受 `translationStaleReasons()` 约束（`web/index.html` L1342-1353），`prompt_edited` 置位（L1147）。文本通常来自 `/api/translate`。

### 2.2 暗房迭代（`POST /api/dialog/turn`，`api.py` L1016-1168）—— rev.1 遗漏

| action | 行为 |
|---|---|
| `start-image`（L1027-1046） | 从已 `done` 任务开启新 session，不生成 |
| `start`（L1065-1077） | 新链：中文 → `translate` → 新 session |
| `redo` 无 delta（L1078-1131） | 从 source `state_snapshot` 继承 `prompt_en/prompt_ir/bindings`；**不调 translate**；seed_strategy 默认 `random`；`prompt_edited` 继承 |
| `redo` 有 delta（L1127-1131） | `translate(delta, prior_state=state, lora_selections=...)` → 重编译 prompt_en + 新 bindings |
| `tweak`（L1116-1139） | 需 `denoise`；seed_strategy 默认 `inherit`；用源图像素；有 delta 时同上走 translate |
| `vibe`（L1118-1126） | 总是 `translate(delta or raw, image_b64=源图, reference_scope="composition_vibe")` |
| 主界面参考上传 | `/api/translate` 带 `reference_image/source_image` → Vision 契约 → Composer |

- **共同点**：全部经 `_enqueue`（L1146）入队；**固定迭代链 LoRA**（body 带不同 selections → 409「当前迭代链固定 LoRA」，L1088-1091）。
- **Registry revision 校验**：`redo/tweak/vibe` 先 `resolve_lora_selections(selections, expected_revision=source.registry_revision)`（L1087）→ 当前 revision ≠ source revision 即 409；`_enqueue` 内 `expected_revision=registry_revision`（来自 `meta`）再次校验。→ **Registry 变化后，任何暗房分支都被拒**（现状）。
- 会话源：`_session_source`（L980-994）要求 source 属于当前 session 且 `status=done` 且有图。

### 2.3 状态快照流向

`state_snapshot`（`_state_snapshot`，`api.py` L571-572，字段集 `_SNAPSHOT_FIELDS` L563-568）保存 `prompt_raw/prompt_en/prompt_ir/prompt_edited/concept/completion_level/lora_bindings/registry_revision/width/height/seed/parent_job_id/generation_mode/denoise/fit_mode/crop_position/change_fields/reference_contract/reference_scope/detailer`。**无模式、无最终正负文本**。`request_snapshot_json` 是唯一最终文本字节证据（含节点 54/55 实际输入），不结构化、不对前端展示。

### 2.4 恢复旧任务 vs 新建分支（R5 要求固定）

- **恢复/查看/取图**（`/api/jobs/{id}/recover`、`/api/history`、结果重取）：不重新生成、不重新编译，**不受 Registry revision 变化影响**（只用已存 `comfy_prompt_id` 与本地文件）。
- **新建分支/继续迭代**（redo/tweak/vibe）：**行为固定为「拒绝」**——Registry revision 与 source 不一致时返回 409 并提示「LoRA Registry 已更新，请重新翻译或回工坊开启新生成链」。理由：binding 的 `file/strength` 来自 Registry 真相源，旧快照不保证物理文件仍可加载、语义仍一致；静默按新 Registry 重解释比拒绝更危险。
- 契约不再同时承诺「永远复用旧 binding」与「无条件拒」：**旧任务文本/binding 快照仅用于展示与核对，不用于新加载**；新加载一律要求 revision 一致。P1 若要提供更平滑的「显式沿用旧快照」选项，需用户明确点名，属可选增强，不在默认。

## 3. Registry / onboarding / 持久化现状与正文持久化方案

### 3.1 Registry（`server/lora.py`）

- `HotLoraRegistry`：mtime 变化才 reload；`revision = sha256(规范化 JSON YAML)[:16]`；`snapshot()` 触发 reload。**注意：revision 只覆盖 YAML 规范化内容；外置文件内容变化不自动改 revision**（R4）。
- `validate()`：schema_version=1；asset 必填 `name/type/file/trigger_policy`；profile 的 `aliases/provides/required_tags/default_tags` + `optional_tags`。**新字段必须同步扩展 validate 与投影**（否则「YAML 有字段、运行时丢」）。
- `get_lora_registry()` 合并 versioned + legacy config，asset 注 `key/strength_model/strength_clip/preview/source/configured/registry_revision`。
- 公共响应 `GET /api/loras`（`api.py` L441-485）**已去敏**：不含 `file/required_tags/trigger`。新增资料须分离「展示字段」与「执行字段」。

### 3.2 onboarding（`.tools/register_lora.py`）

- `fetch_civitai_candidate` / `show_local_civitai_candidate`：从 Civitai 或本地 `.civitai.info` 提取 `description`（去 HTML）+ `authorCodeBlocks`，**只打印给人看**。
- `call_onboard_agent()`：LLM 把 UNTRUSTED 作者说明转保守 candidate；「新知识一律 candidate，由调用方强置 source/verified」。
- `run_agent_onboarding` → 人确认 → `collect_asset` → `atomic_write_registry`（真实 Registry 唯一写入方）。
- **缺口（P2 落点）**：作者原文 / 负面建议 / 模板**当前不落任何持久结构**，Registry 只有语义 tag 字段。

### 3.3 持久化（`server/persistence.py`）

- jobs 表：少量独立列 + `payload_json TEXT NOT NULL`（除独立列与敏感字段外全量 job 字典）+ `request_snapshot_json` + `comfy_output_json`；`_row_to_job` 由 payload_json 重建；`create_generation` 事务化（job + turn + 配额）。
- `SCHEMA_VERSION = 1`；迁移走 `_MIGRATIONS` 元组 + `PRAGMA user_version`；`_payload.excluded` 固定集合——**不在其中的新 job 字段会自动进 payload_json**。
- 备份（`server/maintenance.py`）：`create_backup` = **SQLite online backup + identity.key + 数据库引用图片（`store.asset_references()` 的 images/source_images）**；**不含 `knowledge_cache/`，也不含 `lora_registry.yaml`**。`verify_backup` 用 manifest hash + `PRAGMA integrity_check`；`restore_backup` 有回滚包 + `counts()` 一致性检查。

### 3.4 用法正文持久化方案（R4 固定：方案 A）

**固定方案 A：作者/社区用法正文进入现有 SQLite 可恢复结构（新表），不走外置文件。**

理由（「最小且完整」）：外置文件方案需同时改 `create_backup`、`verify_backup`、`restore_backup`、`asset_references`/`counts` 四处并新增引用类别，且 `knowledge_cache` 当前完全不在备份范围；而进 DB 只需一张新表 + 迁移，即可**自动复用**现有 online backup、manifest hash、integrity、counts、rollback 全套机制。

表建议（字段形状，P2 实施定稿）：

```sql
CREATE TABLE IF NOT EXISTS lora_usage (
    asset_key       TEXT NOT NULL,
    profile_id      TEXT NOT NULL DEFAULT '',      -- 空串 = asset 级
    content_hash    TEXT NOT NULL,                 -- sha256(body)
    body            TEXT NOT NULL,                 -- 不可变原文（完整、不截断）
    structured_json TEXT NOT NULL,                 -- 结构化候选（可空对象，不伪造）
    source_kind     TEXT NOT NULL,                 -- author | community | user | inferred
    source_url      TEXT,
    background_json TEXT NOT NULL DEFAULT '{}',    -- 样例关联的模型/LoRA 版本/参数背景
    created_at      REAL NOT NULL,
    PRIMARY KEY (asset_key, profile_id, content_hash)
);
```

- Registry 的 `usage` 块**只存引用**（`content_hash` + `source_kind` + 已确认结构化字段 + 可选来源/背景摘要），正文与原始候选在 DB。这样：正文变化 → 新 `content_hash` → Registry 引用需更新 → **YAML 变化 → revision 变化**，弥补「revision 不覆盖外置文件」的缺陷。
- `store.counts()` 与 backup manifest 需扩展以覆盖 `lora_usage` 计数（P1/P2 按 `counts()` 实际实现补，不猜）。
- 旧版兼容：新增表属 schema 迁移（§5.2）；**不要求新老进程并行写同库**（本轮明确不做）。

## 4. 行为契约（设计，P1/P2 实现时固定）

### 4.1 模式：`prompt_mode`（assisted / manual，含 R2 编辑后语义）

| 项目 | assisted（缺省，兼容旧行为） | manual |
|---|---|---|
| 文本来源 | 中文构思 → Composer → prompt_en（可被用户编辑） | 用户 `prompt_en` 原文即最终文本 |
| 翻译/Composer | 走 | **零翻译调用**，不构造 IR |
| trigger 编译 | 最终化步骤内编译一次（trigger 属系统注入，展示在最终文本中） | **不编译 trigger**，用户文本即最终 |
| 质量前缀 | 最终化步骤拼 `quality_prefix`（assisted 专属） | **不拼** |
| 负面 | 见 §4.2 | 用户值原样（三态同 §4.2） |
| LoRA 物理加载 | ✓ 服务端 bindings（widget） | ✓ **同样按服务端 bindings**；绝不从文本 `<lora:...>` 加载 |
| IR/旧快照 | 需要时保留 | 不要求；换 seed 等无 IR 操作不得被拒 |

- **R2 编辑后语义**：assisted 结果被用户编辑并提交后，**按已编辑的最终文本生效（等价转 manual 语义）**，不得再以 assisted 为理由补前缀或 trigger。若编辑后仍需系统管理前缀/trigger，用户需重新走 assisted（重新编译）。
- **记录来源 ≠ 执行时改写**：编辑正向后仍记录辅助来源（usage/concept），但执行文本以编辑值为准。
- 非法值 → 400，不隐式降级。

### 4.2 完整负面：`negative_prompt`（三态 + 写入点固定，R5/R6）

**写入点固定为节点 55 `CLIPTextEncode.text` 字面覆盖**（不是节点 4 的 wildcard 字段）：

- 依据：节点 4 是 ImpactWildcardProcessor，其 `populated_text` 会在 `onprompt` 被 `process(wildcard_text, seed)` 重算（§1.2），**写节点 4 会经过 wildcard 处理并可能随 seed 变化**；直接写字面值到 `CLIPTextEncode(55)` 可绕开 wildcard，保证「用户看到/编辑的最终负面 == 实际生效文本」（R5 建议，采用）。
- 覆盖 55.text 会切断 `[4,0]` 连接（AGENTS.md §12 铁律，实施前本地打印确认），此时节点 4 变孤立、不参与输出。

| 值 | 语义 | 动作 |
|---|---|---|
| 缺省 / null | 保持旧行为 = 工作流默认负面 | **不写节点 55**，保留 `[4,0]` 连接 |
| `""`（显式空串） | **确实清空**；不得 `if negative or default` 复活默认 | 写空串到 55.text（切断连接） |
| 非空字符串 | **完整覆盖**（非追加） | 写字面值到 55.text（切断连接） |

- **缺省负面的真实值固定进快照（R6）**：缺省时，服务端记录**工作流默认负面的真实值 = `wildcards.process(node4.wildcard_text, seed)`**（服务端按 Impact Pack populate 语义展开，或在不含随机语法时取 `wildcard_text` 原值并标注「可能动态」），写入 `final_negative` / `negative_source="workflow_default"`。**不得把静态 `populated_text` 当最终值，也不得把未执行的模板冒充最终文本**。
- 前端标签为「最终负面提示词」，不得叫「补充栏」；展示**完整最终负面**。移除默认内容时给一次简短中文说明；auto 应用作者负面建议 ≠ 宣称效果已验证（见 §4.4 三维）。
- 幂等：`negative_prompt`（含空串）随 body 进入 `_request_fingerprint` → 同 `client_request_id` 改负面 → 409；指纹逻辑不改。

### 4.3 唯一最终化步骤（R3 统一）

**指定唯一最终化函数**（建议 `server/prompt_engine.py` 或新 `finalize.py`，P1 定名）：

```text
finalize_generation_text(prompt_mode, prompt_body,   # assisted：Composer 结果；manual：用户原文
                         bindings, quality_prefix, default_negative, negative_spec, usage_refs)
    -> { final_prompt_en, final_negative, negative_source, usage_refs }
```

- **唯一的组装位置**：完成 前缀 + trigger + 正文 + 负面 的组装；**不落盘、不排队**，纯函数。
- **调用点 1**：`_enqueue` 内、落 job 与调度之前（最终化结果进 job）。
- **调用点 2**：`/api/translate`（或其他明确的「准备」接口）在返回里给出**同一函数产出的最终文本预览**（需接收 workflow 名或使用默认 workflow 以取 `quality_prefix`/默认负面）；不得「只说最终可见、直到排队才补词」。
- **workflow builder（`build_prompt`）只写已完成文本**：新增入参 `final_prompt_en` / `final_negative`，只做字面写入（54/55）+ 物理 LoRA widget 注入 + seed/尺寸/switch/detailer；**不再 resolve/compile 文本**。
- **旧内部调用方显式兼容**：`build_prompt` 未收到 final 文本时，回退到旧内部等价路径（供 `submit_and_wait` 等）；改签名前先 grep 全部调用方（`main.py` 重导出、工具、测试）。
- 来源记录与文本改写分离：final 文本确定后不再后台改写；`usage_refs` 与 `registry_revision` 独立保存。

### 4.4 作者/社区资料表示（R6：自由 + 来源 + 三维验证）

- **原文为基础，结构化项可选**：`usage` **不要求**作者模板改写成固定占位符集合才能使用；正文（`body`）完整保留，结构化提取（negative/template 等）失败时**不伪造结构化成功**（`structured_json` 留空并标注解析失败），回退到「只用原文 + 既有 LoRA 语义上下文」。
- **来源枚举**（至少）：`author`（作者说明）/ `community`（社区样例）/ `user`（用户自定义）/ `inferred`（模型/系统推断）。**用户粘贴的社区样例不得标为 `author`**。
- **样例背景保留，不自动应用**：社区样例关联的模型/LoRA 版本与参数存入 `background_json` 作为适用性背景展示；**不因此自动改变用户设置，也不扩展通用调参器**。
- **验证拆成三个独立维度**（不让一个布尔承担三种含义）：①`source_kind`（资料来自谁）；②`source_trust`（来源可信程度，如 `author_declared`/`user_provided`/`inferred`）；③`image_verified`（是否经实图验证）。
- **必需 vs 建议**：属资源必要性的事实与「作者/社区示范配方」分开；无法判定为必需时标 `advisory`，不升级为硬要求；LLM 整理结果**不自动升级为 verified**。
- **越界指令忽略**：正文中要求泄露密钥/操作文件/改选模型等文本一律忽略。
- **模板边界**：作者模板允许自然语言与任意占位符写法，作为 PROMPT 骨架按位填充；**不强制穿过会破坏结构的 tag/IR 处理**；手写模板不被旧 tag 排序器改写成 tag 串。

虚构示例（仅示范字段形状，写入 `docs/examples/lora_usage.example.yaml` 之类独立文件，**不落真实 Registry**）：

```yaml
# 虚构示例，勿写入 server/lora_registry.yaml
loras:
  demo_style_alpha:                     # 虚构 key
    name: "Demo Style Alpha (虚构)"
    type: style
    file: "demo_style_alpha.safetensors"
    trigger_policy: none
    usage:                              # 仅引用 + 已确认结构化项；正文在 DB lora_usage 表
      content_hash: "sha256:..."        # 指向 DB 中不可变正文
      source_kind: author               # author | community | user | inferred
      source_trust: author_declared
      image_verified: false
      advisory: true
      negative: ["blurry background"]   # 已确认的结构化建议（可为空）
      background: { checkpoint: "示例派生模型", size: "832x1216", sampler: "er_sde" }
```

### 4.5 版本与 revision（R4 修订）

- **不使用 `usage_version` 标量**。版本校验复用两层：①请求级 `registry_revision`（已覆盖整个 YAML，含所有 `usage` 引用）——沿用现有 `expected_revision` 409 机制；②binding 级 `usage_refs`：**集合**，每个 LoRA 一项 `{asset_key, profile, content_hash}`。
- 资料内容变化 → `content_hash` 变 → Registry 引用变 → YAML 变 → `registry_revision` 变 → 不一致被 409 拦截；历史记录保留自身 `usage_refs`，**不因当前文件改写而换新用法**；任务准备后不按最新 Registry 重解释旧任务。
- **资料缺失/hash 不符错误策略**：`content_hash` 在 DB 找不到 → 该 LoRA 按「无资料」处理并**明示**（不静默降级、不伪造）；`user`/`inferred` 低置信资料标 advisory；解析失败标解析失败。
- 关联规则：`usage` 可挂 asset 级（`profile_id=""`）或 profile 级；profile 级优先，缺失回退 asset 级；一 LoRA 多 profile 时各自独立引用。

### 4.6 快照、恢复与继承

- job 新增结构化字段（落 `payload_json`）：`prompt_mode`、`final_prompt_en`、`final_negative`、`negative_source`、`usage_refs`（集合）、`negative_source_detail`（可选）。
- `final_*` 与界面展示对应同一次生成；`request_snapshot_json` 继续作节点级字节证据，P1 增加一致性断言测试。
- 恢复/重取/换 seed 分支：不重新调模型编译、不按最新 Registry 重解释；分支继承父任务 `prompt_mode/final_*`；换 seed 只换 `seed` + 新 `client_request_id`；manual 源任务换 seed 不被拒（无 IR 不阻塞）。
- 幂等：同 `client_request_id` 改任一语义字段（含空负面、模式、最终文本）→ 409；新字段随 body 自动进指纹。
- 旧记录：标记「旧格式/无可信最终负面」，不伪造；配额、owner 隔离、墓碑、删除策略、备份恢复行为不变。
- **旧版字段保留须验证（R4）**：不能从「能读 JSON」推出「新老进程并行写兼容」——P1 需验证含新字段的 job 经旧版 `_payload`/`_job_values`/`_row_to_job` 往返后字段不丢（测试断言）。**本轮不要求新老进程并行写同库**。

### 4.7 API 契约（含暗房 /api/dialog/turn）

| 接口 | 新增/变化 |
|---|---|
| `POST /api/jobs` | 可选 `prompt_mode`、`negative_prompt`（三态）；assisted 可选 `usage_refs`/`registry_revision` 校验（不符 → 409）；manual 下 `prompt_en` 即最终文本 |
| `POST /api/dialog/turn` | 明确 `redo/tweak/vibe` 对模式与负面的继承：**默认继承 source 的 `final_negative`/`prompt_mode`**；有 delta/vibe 时重编译正向 → 负面按 §4.2 规则重算（assisted）或继承（manual）；body 可显式覆盖 `negative_prompt`；Registry revision 不一致 → 409（§2.4） |
| `GET /api/jobs/{id}`、`/api/history` | 增补 `prompt_mode/final_prompt_en/final_negative/negative_source/usage_refs`（`_public_job` 白名单扩展）；manual 无 IR 时不伪造 |
| `POST /api/translate` | 增补 `final_prompt_en/final_negative/negative_candidate/usage_applied`（同一最终化函数产出预览）；仍只编译不排队 |
| `GET /api/loras` | P2 决定「用法摘要/来源」展示字段（执行字段 `file/required_tags` 仍不暴露） |

- 兼容：旧客户端不带新字段 → assisted + 缺省负面 → 工作机制不变（负面默认走节点 4 wildcard 展开）。
- 错误：非法模式/类型/超长 → 400；版本不符 → 409；工作流不支持 LoRA 时 manual bindings 同样 400。

### 4.8 普通路径隔离（R1）

- **无 usage 的 LoRA**：完全走现状——既有语义上下文、trigger/Profile/optional、质量预设、默认负面；**不增加模型调用、不加入特殊模板**。
- **有 usage ≠ 全文强制**：只根据当前中文意图应用相关说明（如用户明确要单幅，不能因资料含多格配方而强行改多格）。
- **必需 / 建议分离**：无法判定是必要条件时标建议（§4.4）。
- **P2 协议向后兼容扩展**：不得迫使**无资料请求**重新生成全部负面或额外调用模型。
- **替身校对**（验证方式，不用真实模型差异判兼容）：用**同一份替身 Composer 输出**分别跑普通路径的新旧代码，比对有效正负文本与物理 bindings 一致；**不得用两次真实模型输出的文字差异直接判定兼容失败**（模型有随机性）。

## 5. 迁移判断（R4 更正）

### 5.1 job 新增字段：不需要 ALTER TABLE（但需验证绑定）

job 新字段落 `payload_json` 全量序列化（`_payload.excluded` 不含即保留）；无按新字段查询/索引需求；仅需 api 层 `_SNAPSHOT_FIELDS`/`_public_job` 白名单扩展。**旧版往返保留须验证**（§4.6），本轮不做新老并行写。

### 5.2 用法正文：需要 SCHEMA_VERSION 1 → 2（新增表）

- 新增 `lora_usage` 表（§3.4）；`SCHEMA_VERSION=2`；`_MIGRATIONS` 追加第 2 项（`CREATE TABLE IF NOT EXISTS lora_usage ...`）；`PRAGMA user_version` 驱动。
- 回退：代码回退到 v1 时数据库 user_version=2 > SCHEMA_VERSION=1 → 现有 `_migrate` 明确抛「数据库版本高于程序支持」，不会误写；需回退时用备份恢复或手工 `DROP TABLE`（新表独立，不影响旧表）。
- 备份/恢复自动覆盖新表（同一 SQLite 文件）；`counts()`/manifest 需扩展覆盖新表计数。
- **不升级 Registry schema_version**：`usage` 为可选块，`validate` 做宽松可选校验；旧 Registry 无该块即「无资料」。

### 5.3 P1/P2 落地入口

- **P1**：`prompt_engine.py`（或新模块）新增 `finalize_generation_text`；`api.py` `_enqueue` 增 `prompt_mode/negative_prompt` 与最终化调用、`_job_request` 传 final 文本、`create_job`/`dialog_turn` 解析与白名单；`workflow_engine.build_prompt` 增 `final_prompt_en/final_negative` 入参并**移除二次文本编译**、按 §4.2 写节点 54/55；`persistence.py` 无 job 迁移；`web/index.html` 最小模式入口 + 最终负面 + 最终文本展示 + 「填入默认预设」一次性操作。测试：`test_persistence_recovery`（幂等含空负面、旧记录缺省）、新「manual 翻译调用为 0」「空负面真实清空」「展示=存档=送入一致」用例、`check_frontend.js`。
- **P2**：`lora.py` `validate`/投影/binding `usage_refs`/`build_lora_context`（带负面/模板摘要，不泄 file/trigger）；`persistence.py` 加 `lora_usage` 表与读写、`counts` 扩展；`register_lora.py` 导出 usage 候选到**独立示例**（不写真实 Registry）；`prompt_engine` assisted 读取资料生成正向 + 完整负面候选 + 依据；`web` 来源展示。测试：`test_lora_onboard_agent`、`test_prompt_unit`（负面/模板/冲突/无资料/解析失败）、`test_lora_composition`；临时夹具隔离，真实 Registry 不参与测试写入。

## 6. 未知项（仍阻塞或需实施前核实）

1. **`store.counts()` / `asset_references()` 的精确结构与 backup manifest 扩展点**：新增 `lora_usage` 表后，`counts()` 与 `restore_backup` 一致性检查需同步扩展 —— 本 P0 未读这两个方法实现，P1/P2 实施前必读 `server/persistence.py`。
2. **`build_prompt` 全部调用方**：签名收敛为「只写已完成文本」前，grep `build_prompt`/`submit_and_wait` 全部调用方（含 `server/main.py` 兼容重导出、`.tools`），确认旧内部路径的兼容适配点。
3. **前端暗房事件流**：`redo/tweak/vibe/start-image` 的具体按钮参数（`seed_strategy`、`delta`、负面继承）未逐行核对；P1 UI 改动前核对，保证不破坏现有 DOM/API 契约。
4. **Composer 结构化输出扩展点**：`siliconflow_translate` 输出（TAGS/BREAKDOWN/NL/IR/CHAR/LORA）当前无负面候选位；P2 需在不破坏 `_parse_structured_output`/`_parse_composer_output` 前提下增补，或采用独立负面向导（P1 先行）。
5. **civitai/`.civitai.info` 说明质量与 HTML 形态**（计划 §2.3 已知风险）：自动提取准确率是最大不确定项，P2 仅以少量真实案例验证。

## 7. 验证矩阵（P0 rev.2 完成项 vs 保留项）

| 类别 | 本 P0（rev.2）状态 |
|---|---|
| workflow 正负/trigger 节点链路 | ✅ AnimaFull.json 全节点 + LoraManager + Impact Pack wildcard/onprompt + `sanitize_for_api/build_prompt` |
| 负面默认真实值机制 | ✅ 已读 `onprompt_populate_wildcards`，确认 `populated_text` 被执行期覆盖 |
| detailer 局部文本 | ✅ 已读 `EditDetailerPipe [CONCAT]/[LAB]` + `core.enhance_detail`（更正 rev.1） |
| 全部生成/分支链路 | ✅ 工坊直投 + 暗房 redo(±delta)/tweak/vibe/start-image + 参考上传（§2） |
| Registry 变更下继续行为 | ✅ 固定为拒绝并提示（§2.4） |
| onboarding/Registry 字段投影 | ✅ §3.1/§3.2 |
| 持久化/迁移/备份范围 | ✅ §3.3/§3.4/§5（含备份范围更正） |
| 是否运行模型/GPU/Reasonix | ❌ 未运行（0 次） |
| 未运行检查 | 历史测试未重跑；`counts()`/调用方/前端事件流待 P1 核实（§6） |

> 本文件是设计与事实记录，不是已实现功能。P1 停在 P0 之后，由主 Agent 审阅本契约后下发。

## 8. P1 补充条款（优先于前文歧义）

> 依据 `docs/LORA_USAGE_P1_TASK.md` §2，落实 A–E；与前文冲突时以本节为准。

### 8.A 文本状态字段（真实字段名）

- 请求：`prompt_mode`（`assisted`|`manual`，缺省 `assisted`）、`prompt_state`（`body`|`final`，缺省 `null` = 旧路径）、`prompt_en`（依 state 为待组装正文或已最终化文本）、`negative_prompt`（三态：缺省/null、`""`、非空）、`prompt`（中文 raw）、`workflow`（`/api/translate` 预览取前缀用）。
- job：`prompt_mode`、`prompt_state`、`prompt_en`（= 执行用最终主正向；新任务与 `final_prompt_en` 同值）、`final_prompt_en`、`final_negative`（`str` 或 `null`）、`negative_source`。
- 状态**只用显式字段判定**，不用字符串 `startswith` 或搜索质量词。
- `prompt_state=null`（旧客户端）→ 兼容入口做一次最终化（assisted+body 语义），负面保留工作流 wildcard 行为。
- 新前端提交 `prompt_state="final"` + 已确认值 → 后端只校验与持久化，**不再组装**（编辑/删除前缀与 trigger 不会被补回）。

### 8.B 默认负面

- 新路径（`prompt_state` 非空）在准备阶段即确定默认负面**真实文字**并随生成保存；执行时**字面写入节点 `negative_node`**（显式空串同样写入）。
- 默认真实值的唯一来源 = workflow JSON 中 `negative_node` 的 `text` 连接上游节点的 `wildcard_text`（**不是**静态 `populated_text`，后者会被 `onprompt` 覆盖），经 `workflow_engine.default_negative_text()` 读取；含 `{`/`|`/`__wildcard__` 等动态语法时，新路径**明确报错**（400「请提供完整负面」），不静默展开、不把模板冒充最终文字。
- 旧路径（`prompt_state=null`）不写 `negative_node`，保留原 wildcard 行为。
- 之后改 seed **不改变**已保存的 final 文字。

### 8.C 手动来源进入分支

- `redo`/`tweak` 无 delta：同 revision 下继承已保存 final 正负面，**零 Composer 调用**；manual 无 IR 可执行。
- `redo`/`tweak` 有中文 delta、`vibe`：明确重新编译 → 新结果标记 **assisted 来源**（不得称「manual 且零翻译」）；默认继承源 `final_negative`，body 显式覆盖时用新值（不恢复用户删过的默认词）。
- Registry revision 改变后的新分支沿用拒绝策略（§2.4）。

### 8.D 恢复

- 已提交（有 `comfy_prompt_id`、`result_pending`、`result_ready`）：只核对与重取，**不重编译、不重复生成/扣次**。
- `queued`/`waiting_for_comfy`：只用已落盘 `final_prompt_en`/`final_negative`，不重加前缀、不调 Composer。
- 其 Registry 已变且无法可靠加载 → 记录**明确 `error_kind`**（如 `pipeline_config_changed`），不静默用最新 binding。
- 排队期间工作流模板/默认预设变化**不改变**已保存 final（测试覆盖）。
- 旧任务无 final 字段 → 兼容处理，展示历史时不伪造当时的负面。

### 8.E 公开语义与安全

- 「完整正负」仅指**主采样**文本；开启 detailer 时保留其固定局部增强文字，设置处需简短说明（不能宣称整个 workflow 无其他文本）。
- 客户端 final 文本**不可信**：服务端继续校验 selection/binding/revision，拒绝伪造资源；文本标签不加载文件。
- 不使用客户端提供的 usage/来源证明；本批**不启用 usage 功能、不暴露可伪造来源字段**。
- 本批**不升级数据库版本、不建资料表**；§3.4 的资料表与 §5.2 的 `DROP TABLE` 回退留待 P2 另行审阅。

## 9. P2A 实施契约：用法资料不可变入库与关联读取（2026-09-12）

> 记录 P2A 的**实际**字段与不变量；与 §3.4/§4.4/§5.2 冲突处以本节为准（§8.E 的「本批不建资料表」已由本批取代）。

### 9.1 实际数据表示（SQLite 表 `lora_usage`，SCHEMA_VERSION 2）

| 字段 | 含义 |
|---|---|
| `usage_id` | **版本ID = 规范化完整记录的 sha256**（含正文与正文 hash，见 9.2） |
| `asset_key` / `profile_id` | 关联；`profile_id=''` = asset 级共享 |
| `body` / `body_hash` | 原文（完整不截断）/ 原文字节 hash；**hash 一律由服务端从正文生成**，写入时核对、读取时复核 |
| `source_kind` | `author｜community｜user｜inferred`（社区样例不得标 author） |
| `source_url` | 仅元数据，不抓取 |
| `background_json` | 样例关联的模型/LoRA 版本/参数背景 |
| `candidate_json` | 结构化候选（可为空对象；提取失败不伪造） |
| `advisory_json` | 应用条件/建议属性 |
| `verified` | `unverified｜source_confirmed｜image_verified`（与 source_kind、来源可信度分开表达） |

Registry 只存**引用**：`usage.ref`（单值）或 `usage.refs`（列表）→ 指向 `usage_id`；不重复保存 negative/template（避免两套真相）。`HotLoraRegistry.validate` 校验引用形状：`usage` 必须是对象且提供非空 `ref` 或非空字符串数组 `refs`，**类型错误的字段直接拒绝加载，不会静默退化成「该 Asset 没有资料」**；完全没有 `usage` 的旧 Asset 照旧合法。

### 9.2 不变量

1. **不可变**：`usage_id` 由 `asset_key/profile_id/body/body_hash/source_kind/source_url/background/candidate/advisory/verified` 规范化 JSON 的 sha256 决定；更新正文或任一结构化候选都会得到**新版本号**，旧记录仍可读（`save_lora_usage` 同 ID 返回既有记录、不覆盖）。`body_hash` 由服务端从正文生成，调用方传入只作一致性核对，不符即拒绝写入（不静默修正、不落盘）。
2. **正文优先**：`body` 完整保存；结构化提取失败不影响正文入库。
3. **先记录后引用**：写入不可变记录成功后才产生可合并的 Registry 引用；引用不会指向不存在记录。
4. **读取状态明确**（`resolve_lora_usage(asset, profile_ids, fetch)`）：`no_ref` / `not_applicable`（有引用，但无一适用于当前 Profile 选择）/ `ok` / `partial`（部分引用损坏）/ `invalid`（全部损坏）；`errors[]` 逐条给出 `missing` / `hash_mismatch` / `asset_mismatch`；**不属于当前 Profile 选择的资料不返回**（不算损坏）；asset 级记录为 `shared` 共享建议，profile 级记录归属 `profiles[pid]`。
5. **读取即核验**：除引用与 `usage_id` 相符外，还要核验记录的正文 hash 与**完整版本ID**；被改写正文、只改正文 hash、或伪造版本号的记录一律报 `hash_mismatch`，不进入 `shared`/`profiles`。
6. **坏资料不冒充成功**：任何缺失/不符状态都不会让使用方当作「已应用资料」；`not_applicable` 明确表示「有引用但无适用资料」，仅存在未选 Profile 记录时**不得声称已应用**。

### 9.3 迁移、备份与回退

- 迁移：`_MIGRATIONS` 第 2 项新增 `lora_usage` 表与索引；`executescript` 事务包裹，失败回滚且 `user_version` 不前进。
- 备份：整库 online backup 含新表；`counts()` 增 `lora_usage`，manifest 与恢复一致性检查覆盖资料记录。
- 回退：新表独立；代码回退到 v1 时 `user_version=2` 会被现有 `_migrate` 明确拒绝（需迁移前备份恢复），**不得只 DROP 表或手改 user_version 冒充完整回退**。
- **测试隔离**：`AIRPAINT_STATE_DIR` / `AIRPAINT_LORA_REGISTRY` 环境变量可把 DB / Registry 指向临时位置；所有 import `server` 的测试已设临时 state，导入不再触碰或迁移生产 `server/state/airpaint.db`（本批验证其 mtime 不变）。`.tools/test_lora_usage.py` 另有子进程用例：`--help`、`--usage`（缺 `--db`）、`--usage --db <临时库>` 三种调用都不创建默认 state 库，且 `--help/--usage` 不加载 `server.main/api/runtime`。

### 9.4 维护者入口

`python .tools/register_lora.py --usage --asset-key <KEY> --source-kind <...> --body-file <PATH> [--profile <PID>] [--candidate-file <JSON>] --db <PATH>`：写不可变记录并打印可合并的 Registry `usage` 片段；**必须显式 `--db`**，不自动写生产库或真实 Registry。字段形状示例见 `docs/lora_usage.example.yaml`。

工具默认按需加载：`--help` 与 `--usage` 不导入生产 runtime（预览/入库/Agent 等路径也只在真正需要时才加载 `server.main` 与预览生成器），因此这些调用不初始化、不迁移、不创建默认 `server/state`。

### 9.5 供 P2B 读取的字段（本批不消费）

`resolve_lora_usage` 返回的 `shared`（asset 级共享建议）与 `profiles[pid]`（Profile 专属）记录中的 `body`、`candidate`（negative/template 候选）、`advisory`、`verified`、`source_kind`。本批**生成路径未读取资料**，`build_prompt` 输出不变。