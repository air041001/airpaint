# AirPaint

AirPaint 是面向 ComfyUI 用户的 Prompt / Intent / Knowledge Intelligence Layer：把用户的画面意图编译成适合当前 Anima 模型与工作流的生成请求。它不替代 ComfyUI，也不隐藏 Prompt、LoRA 或 Workflow 的控制权。

## 项目入口

- `docs/BUILDHANDOFF.md`：一份文件了解当前能力、验证状态、边界和下一步。
- `AGENTS.md`：项目开发规约。
- `docs/architecture.md`：当前系统结构。
- `docs/api.md`：HTTP API 契约。
- `docs/decisions.md`：设计取舍与修订关系。
- `docs/DEVLOG.md`：开发演进记录。
- `ROADMAP.md`：仍有效的未来事项。
- `docs/workflow-anatomy.md`：当前 `AnimaFull.json` 节点与注入依据。

## 运行结构

```text
浏览器 https://airpaint.xyz
  -> cloudflared 命名隧道
  -> FastAPI 127.0.0.1:8000
     - 静态托管 web/index.html
     - Prompt / LoRA / Workflow 编译
     - 单并发任务队列
  -> ComfyUI 127.0.0.1:8188
     - server/workflows/AnimaFull.json
```

前端现在与后端、文档一起由本仓库追踪；不再依赖原 `air041001/air` GitHub Pages 仓库。

## 快速启动

1. 启动本机 ComfyUI，监听 `127.0.0.1:8188`。
2. 双击 `.tools/start_airpaint.bat`，启动 FastAPI 与 cloudflared 命名隧道。
3. 访问 `https://airpaint.xyz`，输入邀请码。

后端或配置已经运行时，只补隧道可使用 `.tools/start_tunnel.bat`。

## 配置

复制 `server/config.example.yaml` 为 `server/config.yaml` 后填写本地路径、token 与 API key。`config.yaml` 已被 Git 忽略，敏感值不得写入代码或文档。

当前关键配置：

```yaml
comfy_url: http://127.0.0.1:8188
comfy_dir: "E:/ComfyUI_windows_portable/ComfyUI"
host: 127.0.0.1
port: 8000
allow_origins: ["https://airpaint.xyz"]
tokens: ["friend-xxxx"]
daily_limit: 30
translate: siliconflow
siliconflow_model: "deepseek-ai/DeepSeek-V4-Flash"
siliconflow_vision_model: "Qwen/Qwen3-VL-8B-Instruct"
workflows:
  anima:
    file: workflows/AnimaFull.json
```

人工维护的 LoRA 真相源是 `server/lora_registry.yaml`。新增 LoRA 使用 `.tools/start_lora_onboard_agent.bat` 或：

```text
python .tools/register_lora.py --agent
```

服务启动时不会自动扫描 LoRA 目录，也不会把 Civitai trainedWords 自动写入正式 Registry。

## 常用验证

```text
python -m compileall -q server
node .tools/check_frontend.js
python .tools/test_prompt_unit.py
python .tools/test_lora_composition.py
python .tools/test_lora_onboard_agent.py
python .tools/register_lora.py --validate
python .tools/inspect_wf.py
python .tools/test_e2e.py
```

确定性测试验证协议、解析、Binding 与 Workflow 结构；生成图片质量仍需固定条件出图和人眼判断。

## 当前边界

- 用量、任务与对话状态仍是内存态，后端重启后清零。
- 单卡任务队列并发为 1。
- PromptState、字段级增量编辑和 Workflow Intelligence 只有在真实使用数据证明需要时才启动。
- 多 Profile/多 LoRA 的选择、Binding、强度与单文件去重已经可用，但多人关系和属性绑定仍是 best-effort，结构测试不等于画质验证。
