# airpaint.xyz - AI 绘图小屋

面向 ComfyUI 用户的 **Prompt Intelligence Layer**：把脑中的画面编译成比手动写更适合当前模型（Anima）与工作流的 Prompt。不是「小白一句话出图」，是「懂 ComfyUI 的人更快更准」。
前后端共用固定域名 `airpaint.xyz`, 经 cloudflared 命名隧道穿透内网, 不暴露本机端口。

> 文档导航: [开发规约](AGENTS.md) · [长期路线](docs/PLAN-v5%20—%20AirPaint%20Prompt%20Intelligence.md) · [架构](docs/architecture.md) · [API](docs/api.md) · [设计决策](docs/decisions.md) · [开发日志](docs/DEVLOG.md) · [路线图](ROADMAP.md) · [CLAUDE.md 兼容入口](CLAUDE.md)

## 架构一览

```
访客浏览器  https://airpaint.xyz (网页) / https://api.airpaint.xyz (API)
    ▼  cloudflared 命名隧道 (永久固定)
FastAPI 后端 127.0.0.1:8000  (鉴权/限流/翻译/工作流注入/排队/静态托管)
    ▼
ComfyUI 127.0.0.1:8188  (Anima 合并工作流 AnimaFull, 不对公网开放)
```

## 快速启动

需要本机三样同时运行:

1. **ComfyUI** - 用 `E:\ComfyUI_windows_portable\run_nvidia_gpu_fast_fp16_accumulation.bat` 启动 (监听 127.0.0.1:8188)。
2. **后端 + 隧道** - 双击 `.tools\start_airpaint.bat` (起 FastAPI 8000 + cloudflared 命名隧道)。
3. 访问 `https://airpaint.xyz`, 输入邀请码即可。

> 隧道地址永久固定, **重启不用改任何配置** (这是命名隧道相对临时隧道的核心收益)。

## 配置

`server/config.yaml` (含密钥, 已 `.gitignore`, 不进公开仓库):

```yaml
comfy_url: http://127.0.0.1:8188
comfy_dir: "E:/ComfyUI_windows_portable/ComfyUI"   # 扫 models/loras/ 自动发现新 LoRA
host: 127.0.0.1
port: 8000
allow_origins: ["https://airpaint.xyz", ...]
tokens: ["friend-xxxx", ...]        # 邀请码 (自己生成, 勿推真实码)
daily_limit: 30
translate: siliconflow               # siliconflow | google | none
siliconflow_api_key: "sk-..."
siliconflow_model: "deepseek-ai/DeepSeek-V4-Flash"
siliconflow_vision_model: "Qwen/Qwen3-VL-8B-Instruct"   # 参考图理解
reroll_temperature: 0.9              # 再来一版的高温
workflows:
  anima:                             # 一份合并工作流: txt2img/img2img/精修
    file: workflows/AnimaFull.json
    prompt_node: "54"                # CLIPTextEncode 正面
    seed_node: "6"                   # KSampler
    size_node: "56"                  # EmptyLatentImage
    lora_node: "5"                   # LoraLoader (LoRA)
    image_node: "0"                  # LoadImage (img2img)
    switch_node: "42"                # ImpactSwitch (img2img 路由)
    denoise_node: "6"                # 主 KSampler denoise
    detailer_nodes: { hand: "27", nsfw: "28", face: "29", eyes: "30" }
    sizes: ["832x1216", "1216x832", "1024x1024"]
    quality_prefix: "masterpiece, best quality, newest, absurdres, "
```

改 token / 限流 / 翻译模型后, **重启后端**生效。

## 加功能分支 (合并工作流 AnimaFull, D32)

1. ComfyUI UI 里把功能分支(如 ControlNet / 新 detailer)排好(全活跃), 设置勾选 `Enable Dev mode Options` -> `Save (API Format)`。
2. 并入 `server/workflows/AnimaFull.json` (与现有节点 id 不冲突即可)。
3. `config.yaml` 的 `workflows.anima` 加 `<名>_node` 节点映射 (参考 `image_node`/`detailer_nodes` 写法)。
4. `server/main.py` 的 `build_prompt` 写拼接逻辑(运行时删未选节点/重连), 不是换 JSON 文件。
5. 若分支含前端专属节点 (WidgetToString / Image Saver), 后端 `sanitize_for_api` 会自动处理; 含 Impact Pack 的 seed 需统一, `build_prompt` 已自动处理。

## 常用命令

```bash
# 本机健康检查
curl http://127.0.0.1:8000/api/health        # {"ok":true,"comfy":true}

# 端到端测试
python .tools/test_e2e.py

# 看队列/任务: 后端 stdout
```

## 工具 (.tools/)

- `start_airpaint.bat` - 一键起后端 + 命名隧道 (ComfyUI 需先启)
- `start_tunnel.bat` - 后端已在跑、隧道挂了时单独补隧道 (不碰后端, 避免 8000 端口冲突)
- `test_e2e.py` - 端到端生图测试
- `inspect_wf.py` - 工作流节点 id 校准
- `bind.sh` / `bind.README.md` - **已退役** (旧临时隧道换地址用, 命名隧道后不再需要)

## 注意

- `config.yaml` 含 token 与 API key, **不要**提交公开仓库。
- 单卡串行, 一张约 15~60s, 高峰期前端会显示排队位置。
- 用量/任务状态为内存态, 后端重启清零 (Phase 3 计划 SQLite)。
