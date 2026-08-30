# CLAUDE.md

> **兼容入口**：项目级开发规约已迁移到 [`AGENTS.md`](./AGENTS.md)，请参阅 AGENTS.md 了解：
>
> - 项目核心方向与优先级（Prompt-first / LoRA 后置）
> - LLM 架构原则（LLM 大脑 / 代码脊髓）
> - Prompt IR 12 字段（含 interaction）
> - TAG/NL / Dictionary vs LLM / Character Knowledge / PromptState / LoRA 规则
> - 当前优先级与文档闭环
> - **Push 触发条件**（阶段完成后验收通过即 push，§14）
> - ComfyUI 节点注入铁律
> - 测试要求与敏感信息
> - 当前模块边界与敏感信息
>
> 本文件保留是为了兼容引用 CLAUDE.md 的工具，**实际内容以 AGENTS.md 为准**，不得形成两套冲突规则。

---

**如果你是接手的新 Agent**：先读 `AGENTS.md` 和 `docs/BUILDHANDOFF.md`。前者规定怎么开发，后者是一份文件的当前项目摘要；再按任务路由读取 `architecture.md`、`api.md`、`workflow-anatomy.md` 或相关 ADR，不要把全部历史文档无差别载入上下文。
