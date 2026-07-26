# airpaint.xyz - AI 绘图小屋

把本机 ComfyUI 包成「中文描述 -> 出图」的在线小屋, 朋友输入中文即可出图, 无需装任何东西。
前后端共用固定域名 `airpaint.xyz`, 经 cloudflared 命名隧道穿透内网, 不暴露本机端口。

> 文档导航: [架构](docs/architecture.md) · [API](docs/api.md) · [设计决策](docs/decisions.md) · [开发日志](docs/DEVLOG.md) · [路线图](ROADMAP.md) · [运作规约](CLAUDE.md)

## 架构一览

```
访客浏览器  https://airpaint.xyz (网页) / https://api.airpaint.xyz (API)
    ▼  cloudflared 命名隧道 (永久固定)
FastAPI 后端 127.0.0.1:8000  (鉴权/限流/翻译/工作流注入/排队/静态托管)
    ▼
ComfyUI 127.0.0.1:8188  (AnimaStandard V7, 不对公网开放)
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
host: 127.0.0.1
port: 8000
allow_origins: ["https://airpaint.xyz", ...]
tokens: ["friend-xxxx", ...]        # 邀请码 (自己生成, 勿推真实码)
daily_limit: 30
translate: siliconflow               # siliconflow | google | none
siliconflow_api_key: "sk-..."
siliconflow_model: "Qwen/Qwen3-8B"
workflows:
  anima:
    file: workflows/AnimaStandardV7.json
    prompt_node: "54"                # CLIPTextEncode 正面
    seed_node: "6"                   # KSampler
    size_node: "56"                  # EmptyLatentImage
    sizes: ["832x1216", "1216x832", "1024x1024"]
    quality_prefix: "masterpiece, best quality, ultra detailed, "
```

改 token / 限流 / 翻译模型后, **重启后端**生效。

## 加新工作流

1. ComfyUI 里配好, 设置勾选 `Enable Dev mode Options` -> `Save (API Format)`, 存到 `server/workflows/`。
2. 用 `.tools/inspect_wf.py` 扒节点 id (`prompt_node` / `seed_node` / `size_node`)。
3. `config.yaml` 的 `workflows:` 下加一条声明节点映射。
4. 若工作流含前端专属节点 (WidgetToString / Image Saver), 后端 `sanitize_for_api` 会自动处理; 含 Impact Pack 的 seed 需统一, `build_prompt` 已自动处理。

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
