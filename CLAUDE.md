# CLAUDE.md — airpaint.xyz 项目运作规约

> 任何 AI(含 Claude Code)在本目录开工前必读。本文件刻意保持精简并常驻上下文;
> 详细内容在 `docs/` 下按需读取, 不要把细节堆进来。

## 这是什么

把本机 ComfyUI 包成「中文描述 → 出图」的在线小屋, 发给朋友用。
前后端共用固定域名 `airpaint.xyz`, 经 cloudflared **命名隧道**穿透内网(不暴露本机端口)。
当前阶段: **MVP 已上线, 单工作流 (AnimaStandard V7)**。

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

1. **开工前**先读 `docs/` 相关文档 + 对应代码, 不凭印象改。
2. 改了接口 → 同步 `docs/api.md`; 改了架构 → 同步 `docs/architecture.md`。
3. 有设计取舍 → 记 `docs/decisions.md`(**记原因, 不只记结果**)。
4. 完成一个阶段 → 更新 `ROADMAP.md` 勾选 + `docs/DEVLOG.md` 追加条目(做了什么 / 遇到什么 / 怎么解 / 下一步)。
5. 优先**可维护性** > 快速实现; 模块保持独立(Prompt / Workflow / Intent Engine 解耦)。
6. 若需求偏离「理解用户意图」核心目标, **主动提更贴合的方案**, 不机械执行。
7. **收工前自查**: 哪些文档该同步却没动? 主动提醒开发者是否遗漏。

## 敏感信息

`server/config.yaml` 含 token 与 API key, 已 `.gitignore`, **永不推公开仓库**。
密钥只活在 config.yaml, 不要写进任何 md 文档或代码注释。

## 文档索引

- `docs/architecture.md` — 架构与数据流
- `docs/api.md` — HTTP 接口契约
- `docs/decisions.md` — 设计决策与原因(防遗忘核心)
- `docs/DEVLOG.md` — 开发日志(按阶段)
- `ROADMAP.md` — 阶段规划
- `.tools/` — `start_airpaint.bat`(一键起后端+隧道) `test_e2e.py`(端到端测试) `bind.sh`(已退役, 旧临时隧道用)
