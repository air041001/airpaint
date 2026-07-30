# 新灵感备忘 (2026-07-29 联网调研)

> 来源: byted-web-search 调研 Anima 生态同类项目。旧 ROADMAP 是项目初期由不完全了解项目的模型生成的, **不必被它束缚** -- 多工作流/Docker/WebSocket 是工程化晚期镀金, 可晾着。下面是更贴「理解用户意图」核心目标的方向。

## 相似项目对比

| 维度 | anima-prompter-forge (opparco) | anima-pipeline (tomotto1296) | 我们 airpaint |
|---|---|---|---|
| 形态 | SD WebUI Forge 插件(本地操作者) | Web 应用(终端用户) | Web 应用(朋友远程) |
| LLM | 本地 LM Studio | Gemini 免费枠 | SiliconFlow Qwen3-8B |
| 结构化 | 结构化 prompt + 原始 JSON 调试 | 角色名->LLM 出 tag | 三层(角色->属性->LLM 结构化 D18) |
| 视觉输入 | ✅ 参考图->提取姿势/配色/氛围 | ❌ | ❌ |
| 多角色 | ❌ | ✅ 多角色同设 | ❌(单角色) |
| 标签 UI | 后处理开关(安全/画师/句点) | ✅ 按钮/滑块选发型瞳色表情服装姿势 | ❌(纯自由文本) |
| 鉴权/限流 | ❌ | ❌ | ✅ token+日限+队列 |
| 中文优先 | ❌ | ❌ | ✅ |

**判断**: anima-pipeline 是真孪生(Web UI + LLM 出 tag -> ComfyUI Anima)。我们护城河 = 朋友远程用 + 鉴权限流 + 中文优先 + 三层词典省 token。它强在标签 UI + 多角色。

## 新灵感 (按 价值/功夫 排序, 均不在旧 ROADMAP)

### ① 抽卡 re-roll (轻, 朋友最爱)
同一句中文, LLM 高温重出一版不同风格分解; 朋友点「再来一版」探索, 不用重打字。来自 ComfyUI-StructPrompt 的 seed 控制扩写方向。半天活。

### ② 标签选择器 UI (中, 降门槛)
发型/瞳色/表情/服装/姿势做成可点 chip, 点一下补进提示词。anima-pipeline + anima-prompt-helper(509 tag/30 分类面板)都这么做。不让用户学 danbooru 黑话, 给可视化选项。

### ③ 参考图理解 (中重, 高 wow)
上传一张图, 视觉 LLM(Qwen3-VL)提取氛围/配色/姿势转 tag。anima-prompter-forge 招牌功能。「画一张跟这张同氛围的」-- 朋友常说不出想要啥但能给参考图。需工作流支持图输入(见 workflow-anatomy)。

### ④ 多角色 + 防属性串色 (中, 质量硬伤)
2+ 人时 danbooru tag 袋会让 A 的发色跑到 B 身上(Concept Bleeding)。ComfyUI-Anima-Prompt-Rewriter 解法: LLM 把 tag 改写成有明确主语的英文自然语言(Anima 的 Qwen3-0.6B 文本编码器原生支持 NL)。我们画双人必踩这坑。

### ⑤ 多轮对话精修 (重, 北极星)
「再亮一点」「把她换成坐姿」对话式迭代出图。AAI DialogDraw(IRPEM 意图识别)。这才是「理解用户意图」终极形态, 跟一堆 tag 工具拉开代差。工程量大。

### ⑥ 规范化 Anima tag 顺序 (轻, 白捡)
anima-prompt-helper 指出 Anima 期望固定序: `quality -> year -> rating -> count -> character -> series -> artist -> general -> 自然语言`。我们没强制, 角色 tag 没排在 general 前。对齐即「规范」收尾。

## 推荐路径
先 ①+⑥(轻量立刻见效), 再赌 ③参考图 或 ⑤多轮对话 作大跨步(真正拉开差距、贴意图本意)。②标签 UI 体验补强, ④多角色质量修补, 见机插。

## Sources
- https://github.com/opparco/anima-prompter-forge
- https://qiita.com/RHU/items/18095cb22281cd027bc4 (anima-pipeline)
- https://github.com/yanadere0549/anima-prompt-helper
- https://github.com/sinanzoo2nd/ComfyUI-Anima-Prompt-Rewriter
- https://github.com/cx2002302-lang/ComfyUI-StructPrompt
- https://ojs.aaai.org/index.php/AAAI/article/download/34661/36816 (DialogDraw)
