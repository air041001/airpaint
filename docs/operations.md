# AirPaint 运维、备份与恢复

> 适用于最终封版的单机 Windows 环境。数据库和图片是一个整体；不要只复制正在写入的 SQLite 文件当作备份。

## 运行前提

封版环境记录：

- Windows + PowerShell
- Python 3.10.10
- FastAPI 0.128.8、httpx 0.28.1、Pillow 12.1.1、PyYAML 6.0.3、uvicorn 0.35.0
- ComfyUI 位于本机并监听 `http://127.0.0.1:8188`
- 可选 cloudflared 命名隧道 `airpaint`

当前单页界面的 Tailwind、GSAP 和字体从固定外部 CDN 加载。断网不会破坏 SQLite 或图片，但页面样式/动效可能降级；仓库内 README 截图和 `docs/demo.md` 可在 GPU、API 或 CDN 不可用时离线说明项目。封版不为此引入新的前端构建链。

Python 依赖以根目录 `requirements.txt` 为准：

```text
python -m pip install -r requirements.txt
```

复制 `server/config.example.yaml` 为 `server/config.yaml`。真实邀请码与 API key 只写在这个已忽略文件中。程序会在启动时逐项检查配置、workflow 文件和 LoRA Registry；启动脚本还会检查 ComfyUI 与可选的 cloudflared。

## 启动与关闭

公网模式：双击 `.tools/start_airpaint.bat`。

仅本机模式：

```text
powershell -NoProfile -ExecutionPolicy Bypass -File .tools/start_airpaint.ps1 -NoTunnel
```

日志和 PID 位于 gitignored 的 `server/state/logs/` 与 `server/state/run/`。脚本不会启动 ComfyUI；ComfyUI 不可达时会直接说明缺失服务。

正常关闭：双击 `.tools/stop_airpaint.bat`。停止脚本只处理自己记录且命令行匹配的 AirPaint/cloudflared PID。后端先读取本地 stop marker，让 uvicorn 执行 shutdown、数据库 checkpoint 和连接关闭；20 秒内未退出才强制终止。ComfyUI 始终保留运行。

## 数据布局

| 路径 | 内容 |
|---|---|
| `server/state/airpaint.db` | 任务、会话、轮次、每日用量和请求快照的权威数据库 |
| `server/state/identity.key` | owner ID 与登录 cookie 的本机签名密钥 |
| `server/state/source_images/` | Img2Img/历史分支需要的输入图片 |
| `server/images/` | 任务输出图片 |
| `server/backups/` | 默认备份与恢复前回滚包 |

上述路径全部不进 Git。丢失 `identity.key` 会使既有 owner 归属无法由当前邀请码和 cookie 恢复，因此它必须和数据库一起备份。

数据库保存 ComfyUI `prompt_id`、任务生命周期、实际 seed/尺寸、Prompt/Concept/IR、LoRA Binding/Registry revision、父任务/会话、输入输出引用及去敏后的实际请求快照。不会保存原始邀请码或 API key。

## 任务与错误状态

| 状态 | 含义 | 是否自动重新提交 |
|---|---|---|
| `queued` | 已落盘、等待单 worker | 是，尚未送达 ComfyUI |
| `waiting_for_comfy` | 已确认 ComfyUI 连接不可用 | 恢复连接后可安全继续 |
| `dispatching` | 已分配 prompt ID，正在发送 | 重启后先核对，不盲目重发 |
| `submitted` / `running` | ComfyUI 已接收或运行 | 只查询现有 prompt ID |
| `result_pending` | AirPaint 等待超时，GPU 未必停止 | 继续核对现有任务 |
| `reconcile_pending` | 请求可能送达但证据不足 | 不自动重发，等待人工“重新核对” |
| `result_ready` | ComfyUI 已出图但下载失败 | 只重新下载，不重新生成 |
| `done` | 图片已保存且可读取 | 无 |
| `failed` | ComfyUI 明确拒绝/执行失败或输入已丢失 | 无 |

浏览器网络中断不会把任务标成失败，也不会产生虚假的生成百分比。相同 `client_request_id` 和相同请求返回原任务；同一个 key 用于不同请求返回 409。主动“换一版”使用新 key，因此仍会创建新任务。

## 用量规则

- 默认每个邀请码每天 90 张，以后端本地日期为界。
- 新任务和一次用量增加在同一 SQLite 事务中完成。
- 参数、Prompt 或 LoRA 校验失败不计数。
- 任务落盘后即计一次；后续 ComfyUI 失败或不确定状态不退次数，以避免重启和并发造成难以审计的配额回滚。
- 同请求重试、刷新查询、重启恢复、状态核对和已有结果重新下载不再扣次。
- `/api/translate`、历史、会话和图片读取不计数，也不会因达到上限而被禁止。

## 在线备份

AirPaint 可运行时执行：

```text
python -m server.maintenance backup
```

工具使用 SQLite online backup API 获取一致视图，然后只收集数据库引用的源图和结果图，再加入 `identity.key`、文件大小及 SHA-256 manifest。任何引用文件缺失都会中止，不生成一个自称完整的备份。

也可指定文件：

```text
python -m server.maintenance backup --output E:\AirPaintBackups\airpaint.zip
python -m server.maintenance verify E:\AirPaintBackups\airpaint.zip
```

建议把 zip 复制到另一块磁盘；仅留在项目盘不构成灾难恢复。

## 恢复

1. 正常停止 AirPaint；ComfyUI 可继续运行。
2. 校验备份。
3. 执行覆盖恢复。
4. 重新启动并检查历史图片。

```text
python -m server.maintenance verify E:\AirPaintBackups\airpaint.zip
python -m server.maintenance restore E:\AirPaintBackups\airpaint.zip --replace
```

工具检测到 AirPaint 仍在运行时拒绝恢复。覆盖前会在线备份当前数据库和其引用图片到 `server/backups/pre-restore-*.zip`；随后移除目标数据库的精确 `-wal/-shm` sidecar，原子替换 DB/身份密钥，并校验完整性和记录数量。

恢复不会删除目标目录里多余的旧图片。这些文件没有数据库归属记录，不能通过受保护图片接口访问；确认不再需要后可另行人工归档。

## 旧数据边界

封版前的内存任务、旧浏览器 localStorage 列表和仅剩图片文件没有可信 owner、Prompt、seed 或父子关系，不能凭空补齐。前端首次迁移时删除旧本地历史索引，但不删除图片文件。需要保留时可将旧图作为普通文件另行归档，不要写入数据库冒充可恢复任务。

## 故障定位

- 启动立即失败：阅读控制台指出的缺少配置/文件/服务；后端详细错误在 `server/state/logs/backend.err.log`。
- `waiting_for_comfy`：先恢复 ComfyUI，再在界面重新核对；它尚未提交，不需要新建任务。
- `reconcile_pending`：不要再点一次生成。先恢复 ComfyUI 历史可见性，然后点“重新核对”。
- `result_ready`：点“重新核对”只会重新取图，不消耗新配额。
- 备份失败提示引用图片缺失：先从现有磁盘/ComfyUI 输出找回该文件，或接受当前数据已不完整并单独处理；工具不会静默漏备份。
