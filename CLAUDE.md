# CLAUDE.md — airpaint.xyz 项目运作规约

> 本文件记录项目架构与开发规约, 供协作开发参考; 详细内容在 `docs/` 下。

## 这是什么

把本机 ComfyUI 包成「中文描述 → 出图」的在线小屋, 发给朋友用。
前后端共用固定域名 `airpaint.xyz`, 经 cloudflared **命名隧道**穿透内网(不暴露本机端口)。
当前阶段: **Release v1.0 -- 三工作流 + 对话迭代 + 参考图 + NSFW detailer + 前端三屏**。

> **最近改动** (每次迭代更新此行): 2026-08-11 工作流合并(txt2img/img2img/精修/inpaint 一份 AnimaFull.json + 后端删节点拼接, D32); 见 DEVLOG 第29条。前档: 暗房 redo 替换意图(D31)/角色裸名去重(D30)/LoRA 工程(D29)。

## 模块地图

`server/main.py` 是单文件后端, 逻辑分块:

| 模块 | 关键符号 | 职责 |
|---|---|---|
| 鉴权/限流 | `auth()` `USAGE` | Bearer token + 日限, **内存计数(重启清零)** |
| 内容过滤 | `check_banned()` | banned_words 子串匹配 |
| **Prompt Engine** | `translate()` `match_characters()` `match_dict_words()` `siliconflow_translate()` `_parse_structured_output()` `normalize_tag_order()` `HotDict` `dict.yaml` `char_dict.yaml` | 三层: 角色->词典->LLM(信息分流: 5字段给人看 + TAGS离散属性 + NL关系叙事不重复, D28, 回传 breakdown) / LRU 缓存 500 / **reroll 跳过缓存高温重抽** / **tag 规范序 count->char->general** / **词典 mtime 热更新不重启** / **dict 子串匹配(NSFW词绕过LLM安全过滤)** |
| **Workflow Engine** | `sanitize_for_api()` `build_prompt()` `upload_image_to_comfy()` | 清洗前端专属节点+ 注入 prompt/seed/size/**多LoRA**/img2img/inpaint, **统一 seed**, **detailer 删节点拼接**(按 detailer:{face,hand,nsfw,eyes} 删未选重连, D32); **一份合并工作流 AnimaFull** (txt2img/img2img/精修/inpaint) |
| **LoRA Registry** | `get_lora_registry()` `scan_loras()` `_civitai_lookup()` `LORA_CACHE_FILE` | 三层合并: config.yaml 手动(type/trigger/服装变体) > Civitai SHA256 hash lookup 自动补全 > 裸文件; `/api/loras` 分组返回(character/style/other) + configured 标记; `/api/loras/refresh` 重扫; D29 |
| ComfyUI 客户端 | `submit_and_wait()` | `/prompt` 提交 + `/history` 轮询 + `/view` 取图 |
| 队列 | `worker()` `QUEUE` | 单并发 asyncio.Queue (GPU 串行) |
| 静态托管 | `/` `/images` | `/` 返回 `web/index.html`, `/images` 出图 |
| **Intent Engine** | `detect_characters()` `char_dict.yaml` `siliconflow_vision_translate()` | 构图/场景/情绪分解 (D18 LLM 结构化); **参考图理解 (③ Qwen3-VL 提氛围, D23)**; **⑤ 对话迭代 (显式路由: 换一版/保氛围, D25)**; 否定解析弃用 (Anima 负面=常量); 待做: 歧义消解/LoRA 自动推荐, 见 decisions.md D12/D13/D18 |

## 运作规则(开发时遵守)

1. 改动前先读相关代码/文档, 不凭印象改。**节俭读法**: 先 `Grep` 定位(最后一条编号 / 符号 / 关键词), 再 `Read` 指定段(offset/limit), 不整读大文件; 模块地图在本文件常驻, 不重读。
2. 改了接口 → 同步 `docs/api.md`; 改了架构 → 同步 `docs/architecture.md`。
3. 有设计取舍 → 记 `docs/decisions.md`(**记原因, 不只记结果**)。
4. 完成一个阶段 → 更新 `ROADMAP.md` 勾选 + `docs/DEVLOG.md` 追加条目(做了什么 / 遇到什么 / 怎么解 / 下一步)。
5. 优先**可维护性** > 快速实现; 模块保持独立(Prompt / Workflow / Intent Engine 解耦)。
6. 若需求偏离「理解用户意图」核心目标, **主动提更贴合的方案**, 不机械执行。
7. **收尾自查**: 哪些文档该同步却没动? 检查是否遗漏。

## ComfyUI 节点注入准则 (扩展工作流时遵守)

往工作流节点注入值 (LoRA / ControlNet / 图生图 / inpaint 等) 前, 必须查**本机节点源码**定格式, 不靠工作流 JSON 或前端 widget 猜 (这次 LoRA 的 `del text` / `__value__` 对象格式 / `active:true` / 连接覆盖断链, 全是源码查出来的, 见 D16):

1. **定位节点**: `grep` 工作流 JSON 找 `class_type` -> 读该节点 `inputs`, 看目标 input 现在是字面值 / 连接 `["id",n]` / `{"__value__":...}`。
2. **查源码 (权威)**: 本机 `<comfy>/custom_nodes/<包>/` 里匹配该 class 的类, 读 `INPUT_TYPES` + `execute()`。确认: 实际读哪个 input (有的 required 却被 `del` 忽略)、值的确切 schema (对象 vs 数组、字段名、必填 flag 如 `active`)、是否依赖前端 `extra_pnginfo` (依赖则不能走后端 /prompt, 见 sanitize_for_api)。
3. **config 驱动注入**: workflow config 加 `<名>_node: "<id>"`, 用 `build_prompt` 的 `set_input(key, field, value)`, 不硬编码 id (与 prompt_node/seed_node/lora_node 同套)。
4. **连接覆盖警告**: 目标 input 若是连接 `["id",n]`, set 字面值会**替换连接**, 上游输出作废。检查下游是否依赖该链 (如触发词/wildcard), 依赖则手动补进 prompt。
5. **本地验再跑**: 调 `build_prompt(...)` 打印注入后的节点 inputs + 最终 prompt 确认格式, 再端到端跑 ComfyUI。

> ComfyUI API input 三种形态: 字面值 (str/int/float/bool/dict/list) | 连接 `["源节点id", output_index]` | 复杂 widget 序列化 `{"__value__": <实际值>}` (照抄工作流 JSON 里该 input 现有形态最稳)。实例见 architecture.md「Workflow Engine」+ D16。

## 工作流功能切换 (加 ControlNet / inpaint / 图生图 / 新 detailer 等时)

1. **套路**: ComfyUI UI 里拨 rgthree 组开关(MUTE->活跃)或加节点 -> 重导出 API JSON -> 放 `server/workflows/` -> config `workflows` 加一条(file+label; 注入节点 54/6/56/5 通用)-> 前端下拉自动出现。**换功能=换 JSON 文件**(后端 /prompt 用 API 导出, 只含活跃节点, 不能运行时拨 MUTE 组)。
2. **重启边界**: 工作流 JSON `build_prompt` 每次现读, **改文件不用重启**; config 改要重启(config 不热更新, 故意, 见 D21)。`anima-detailer` 就是这么加的(见 D22)。
3. **/prompt 三个坑**(扩展前必查, 见 workflow-anatomy「API 耦合」):
   - 前端专用节点(WidgetToString / Image Saver Metadata / Image Saver Simple)依赖 `extra_pnginfo`, 后端不带会崩 -> `sanitize_for_api` 剥/换 SaveImage。
   - **展示型 OUTPUT_NODE(Image Comparer / PreviewImage 等)** 的图进 `/history`, `submit_and_wait` 取"第一个有图的节点"会误取中间图 -> 也要 sanitize 剥。
   - seed 连接(如 rgthree Seed widget=-1)运行时自己随机成正整数, 不崩 Impact Pack; build_prompt 的 seed 统一只覆写 int 字面值, 跳过连接。
4. **detailer/二次采样提速**: 时长主因是渲染分辨率(`max_size`), 不是步数; 先降 max_size, steps 次之(12 步左右是下限)。见 D22(186s->90s)。
5. **大工作流 JSON 分析**: 76 节点的完整 UI 工作流较大, 建议用子任务解析+查源码+核磁盘, 只收结论。

## 上下文节俭

- 优先 `Grep` 定位再 `Read` 指定段, 不整读大文件。
- 改完不回读验证(报错即失败)。
- 并行批量调用独立操作。

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
