# CLAUDE.md — airpaint.xyz 项目运作规约

> 任何 AI(含 Claude Code)在本目录开工前必读。本文件刻意保持精简并常驻上下文;
> 详细内容在 `docs/` 下按需读取, 不要把细节堆进来。

## 这是什么

把本机 ComfyUI 包成「中文描述 → 出图」的在线小屋, 发给朋友用。
前后端共用固定域名 `airpaint.xyz`, 经 cloudflared **命名隧道**穿透内网(不暴露本机端口)。
当前阶段: **MVP 已上线, 单工作流 (AnimaStandard V7)**。

> **最近改动** (压缩后先看这行, 省重读 docs; 每完成一阶段更新此行): 2026-07-26 启动脚本 bat 编码修复 + start_tunnel.bat, 见 DEVLOG 第11条 / decisions D14。

## 模块地图

`server/main.py` 是单文件后端, 逻辑分块(改动时定位用):

| 模块 | 关键符号 | 职责 |
|---|---|---|
| 鉴权/限流 | `auth()` `USAGE` | Bearer token + 日限, **内存计数(重启清零)** |
| 内容过滤 | `check_banned()` | banned_words 子串匹配 |
| **Prompt Engine** | `translate()` `siliconflow_translate()` `dict.yaml` | 中文→danbooru tag, 词典优先 / LLM 兜底 / LRU 缓存 500 |
| **Workflow Engine** | `sanitize_for_api()` `build_prompt()` | 清洗前端专属节点 + 注入 prompt/seed/size, **统一所有 seed** |
| ComfyUI 客户端 | `submit_and_wait()` | `/prompt` 提交 + `/history` 轮询 + `/view` 取图 |
| 队列 | `worker()` `QUEUE` | 单并发 asyncio.Queue (GPU 串行) |
| 静态托管 | `/` `/images` | `/` 返回 `web/index.html`, `/images` 出图 |
| **Intent Engine** | `detect_characters()` `char_dict.yaml` | **部分实现**: 氛围扩写+角色词典; 完整意图解析(否定/歧义/构图)待做, 见 decisions.md D12/D13 |

## 运作规则(开发时遵守)

1. **开工前**先读相关代码/文档, 不凭印象改。**节俭读法**: 先 `Grep` 定位(最后一条编号 / 符号 / 关键词), 再 `Read` 指定段(offset/limit), 不整读大文件; 模块地图在本文件常驻, 不重读。
2. 改了接口 → 同步 `docs/api.md`; 改了架构 → 同步 `docs/architecture.md`。
3. 有设计取舍 → 记 `docs/decisions.md`(**记原因, 不只记结果**)。
4. 完成一个阶段 → 更新 `ROADMAP.md` 勾选 + `docs/DEVLOG.md` 追加条目(做了什么 / 遇到什么 / 怎么解 / 下一步)。
5. 优先**可维护性** > 快速实现; 模块保持独立(Prompt / Workflow / Intent Engine 解耦)。
6. 若需求偏离「理解用户意图」核心目标, **主动提更贴合的方案**, 不机械执行。
7. **收工前自查**: 哪些文档该同步却没动? 主动提醒开发者是否遗漏。

## 上下文节俭(省 token)

每次压缩后上下文清空, 重读 docs 是最大浪费。守则:

- **先 Grep 再 Read**: 同步文档用 `grep "^## "` 找最后编号/标题, 只 Read 那段对格式, 不整读。
- **改代码同理**: `grep` 符号定位 -> Read 该函数段, 不扫全 `main.py`。
- **最近改动行**: 本文件「这是什么」末行是最新进度, 压缩后先看它。
- **不复述已读**: harness 提示 "file unchanged" 就别再 Read 同一文件。
- **编辑后不回读**: Edit/Write 成功就别再 Read 验证(报错即失败)。
- **DEVLOG/decisions 记干货**: 根因 + 解法 + 指针(见 Dxx), 不复述代码, 不写流水。
- **并行批量调用**: 独立操作一次发出。

## 敏感信息

`server/config.yaml` 含 token 与 API key, 已 `.gitignore`。密钥只活在 config.yaml, 不写进任何 md 或代码注释。

- **结构性保证**: config.yaml 在 .gitignore; 新增含密钥文件先确认已 ignore。
- 仓库当前**私有**(air041001/airpaint), 不每次 push 扫密钥(扫描便宜但无谓)。
- **转公开前**统一审计一次: `grep -rnE "sk-|cfk_|friend-[0-9]" --exclude-dir=.git .`, 无真实 key/token 再公开。

## 文档索引

- `docs/architecture.md` — 架构与数据流
- `docs/api.md` — HTTP 接口契约
- `docs/decisions.md` — 设计决策与原因(防遗忘核心)
- `docs/DEVLOG.md` — 开发日志(按阶段)
- `ROADMAP.md` — 阶段规划
- `.tools/` — `start_airpaint.bat`(一键起后端+隧道) `start_tunnel.bat`(单独补隧道) `test_e2e.py`(端到端测试) `bind.sh`(已退役)
