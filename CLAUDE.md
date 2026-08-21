# CLAUDE.md

> **兼容入口**：项目级开发规约已迁移到 [`AGENTS.md`](./AGENTS.md)，请参阅 AGENTS.md 了解：
>
> - 项目核心方向与优先级（Prompt-first / LoRA 后置）
> - LLM 架构原则（LLM 大脑 / 代码脊髓）
> - Prompt IR 12 字段（含 interaction）
> - TAG/NL / Dictionary vs LLM / Character Knowledge / PromptState / LoRA 规则
> - 开发顺序（Phase 0-8）与文档闭环
> - **Push 触发条件**（阶段完成后验收通过即 push，§14）
> - ComfyUI 节点注入铁律
> - 测试要求与敏感信息
> - 当前模块地图（仓库现状 §17）
>
> 本文件保留是为了兼容引用 CLAUDE.md 的工具，**实际内容以 AGENTS.md 为准**，不得形成两套冲突规则。

---

**如果你是接手的新 Agent**：直接读 `AGENTS.md` + `docs/PLAN-v5 — AirPaint Prompt Intelligence.md` + `docs/BUILDHANDOFF.md` 即可对齐项目方向、当前状态和下一步。当前主线已完成 Phase 2.6（Prompt Expansion 生产协议）与 Phase 3（Character Knowledge 自动缓存），最终任务为 `docs/PLAN-LORA.md`（LoRA Context 工程）。