# AirPaint Roadmap

> 本文件只保留仍有效的未来事项。当前能力、已完成阶段与验证证据见 `docs/BUILDHANDOFF.md`；历史过程见 `docs/DEVLOG.md` 与 `docs/decisions.md`。

## 当前产品状态

Visual Composer、Character Knowledge、LoRA Context / Binding / Composition 工程链、统一 `AnimaFull` workflow，以及三栏双主题工作台均已进入当前生产版本。

当前没有自动开启的新大阶段。后续工作由真实使用中的可复现问题触发，不因旧 Phase 编号、结构完整度或 Prompt 长度自动启动。

## 近期维护

- 收集“输入 → 中文构思 → 最终 Prompt → LoRA/参数 → 图片表现”的真实失败样本。
- 只对可重复的语义、状态或工作流问题做定向修订。
- 新 LoRA 继续走目标文件索引验收、人工候选确认、真实出图和 verified 提升流程。
- 持续核对多人场景中的主体计数、关系表达、分屏/黑线、第三主体和属性串色。
- 继续处理真实出现的前端空状态、错误状态、历史作品细节与可访问性问题。

## 条件触发方向

### PromptState / 字段级增量编辑

只有暗房使用证明“只换衣服、保持动作/构图”等字段锁定需求高频，或字符串累积再次造成稳定错误时，才启动 PromptState 与 dialog 状态重构。

### Workflow Intelligence

只有现有 Prompt、Knowledge 与 LoRA 层已无法表达真实需求，并且新 workflow 能提供明确用户价值时，才扩展 ControlNet、区域控制或新的工作流分支。修改前必须按 `docs/workflow-anatomy.md` 和本机节点源码核对输入契约。

### 持久化与基础设施

以下事项由真实规模触发，不作为当前产品主线：

- 持久化用量、任务和生成历史。
- 邀请码管理。
- WebSocket 替代轮询。
- Docker 化或远程多实例部署。

## 明确不做

- 不恢复无目标的大批量 Prompt 跑分。
- 不用更长 Prompt、更满 IR 或单测通过代替图片人眼判断。
- 不建设大规模 LoRA 推荐、marketplace、社交、用户画像或向量数据库。
- 不让 LLM 决定 LoRA 文件名、节点 ID、强度或 exact trigger。
- 不把 weighted NL、semantic negative、固定 girl/female 替换或 R4 特判设为默认策略。
