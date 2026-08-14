# AnimaStandardV7 工作流解剖 (2026-07-29)

> **现状 (2026-08-12)**: 本文解剖的是旧 AnimaStandardV7「拨组开关 + 换 JSON 文件」模型, 已被 D32 取代(合并一份 AnimaFull.json + build_prompt 运行时删节点拼接)。**节点 id 参考仍适用** (config `workflows.anima` 与 AnimaFull 共用同一套 id)。

> 两种形态要分清:
> - **完整 UI 工作流**: `E:/goole_chrome_downloads/animaWorkflows_v70/AnimaStandardV7.json` -- 76 节点, ComfyUI 里编辑用的, 含大量 MUTE(静音/旁路)的功能模块。
> - **API 导出**: `server/workflows/AnimaStandardV7.json` -- 只有**未静音的活跃节点**(~34 个), 后端 /prompt 用的是这个。用户导出时哪些组开着, 就只导出哪些。
>
> **核心机制**: 作者用 `rgthree Fast Groups Bypasser`(节点 2/61)**按组开关**功能。所谓「修改工作流」的安全方式 = 在 ComfyUI 里**拨组开关 -> 重新导出 API JSON -> 替换 server/workflows/**, 而不是增删节点。

## 节点分组

### A. 核心出图链 (活跃, API 用)
| id | 类型 | 作用 | 我们注入? |
|---|---|---|---|
| 1 | UNETLoader | 载 Anima 模型 | - |
| 18 | CLIPLoader | Qwen3-0.6B 文本编码器 | - |
| 23 | VAELoader | VAE | - |
| 5 | LoraLoader(LoraManager) | LoRA 加载 | ✅ loras widget |
| 3/4 | ImpactWildcardProcessor | 正/负向提示词 wildcard | 负向4=常量(D18改) |
| 54 | CLIPTextEncode | 正向编码 | ✅ text |
| 55 | CLIPTextEncode | 负向编码 | - |
| 56 | EmptyLatentImage | 空白 latent | ✅ width/height |
| 6 | KSampler | 采样 | ✅ seed |
| 43 | VAEDecode | latent->图 | - |
| 13 | ImageSaverSimple | 存图 | sanitize 换成 SaveImage |

### B. 脸部细节修复 (活跃)
`7 UltralyticsDetector`(检测脸区)+ `8 SAMLoader`+ `19 ToDetailerPipe`+ `26 DifferentialDiffusion`+ `42 ImpactSwitch`+ `50 Tiled`+ `35/38/45` 参数。出图后自动二次修脸。**这就是为什么 D18 负面不用堆 bad-hands。**

### C. LoRA 触发词链 (活跃, D16 断过)
`37 TriggerWordToggle`+`46/51 StringConcatenate`+`48 RegexReplace`+`57 WidgetToString`。我们 build_prompt 覆盖 54 断了链, 故手动拼触发词(见 D16)。

### D. 前端专用节点 (sanitize_for_api 剔除)
`57 WidgetToString`/`58 ImageSaverMetadata`/`13 ImageSaverSimple`/`24 InputParameters`。依赖 `extra_pnginfo`(前端 UI 图), 后端 /prompt 不带会崩, sanitize 把 13 换内置 SaveImage。

### E. 参数 widget
`34 Seed` `39 Width` `47 Height` `52 Batch` `40/41 展示`

## ⭐ 已建好但 MUTE 的功能模块 (拨组开关即可启用, 不用新建节点)

| 功能 | 关键节点 (全 MUTE) | 链路 | 启用意义 |
|---|---|---|---|
| **img2img / 图生图** | 0 LoadImage -> 31 ImageResize -> 33 VAEEncode -> 25 KSampler(二采) | 载入图->缩放->编码latent->二次采样 | ⑤多轮对话精修的基础 |
| **手部修复** | 27 FaceDetailerPipe(HandDetailer)+ 9-12 检测器 + 14-17 EditDetailerPipe | 检测手区->重画 | 手画更好的免费质量提升 |
| **眼部修复** | 30 FaceDetailerPipe(EyesDetailer) | 检测眼区->重画 | 眼睛细节 |
| **NSFW 修复** | 28 FaceDetailerPipe(NSFWDetailer) | - | - |
| **高清放大** | 59/60 easy hiresFix | 43 VAEDecode 出图->放大重绘 | 出大图 |
| **CFGZeroStar** | 49 CFGZeroStar | 采样技巧 | - |
| **后期效果** | 62 AdjustContrast / 65 Morphology / 71 ImageQuantize / 72 ImageSharpen / 74 GLSLShader(VHS) | 图像后处理 | 风格特效 |
| **图片对比** | 53/67-70 Image Comparer | 前后对比 | 调试用 |

> 注: 25 KSampler(二采)的 latent_image 接的是节点 6(一采)的输出, 即在首版 latent 上二次采样; 配合 0/33 可切到外部图输入(ImpactSwitch 42 路由)。

### 启用查证 (2026-07-29, 子 agent 查源码+磁盘)

> 结论: **零代码改动** (sanitize 不碰 detailer 组 / build_prompt seed 统一不坑 / 注入点不动 / 输出链自通), 但**不是纯拨开关--卡在模型文件**。
>
> **✅ 已部署 (2026-07-30)**: Face+Hand 启用为 `anima-detailer` 精修工作流(快速版 `anima` 仍为原版)。detailer 调参 max_size 1024 / steps 12 (~90s, 见 D22); Image Comparer(67/69)已剥(sanitize); Eyes/hires 因模型缺未开。

- **seed 不崩的根因**: 4 个 detailer(27/30/28)的 seed 是**连接** `["34",0]`(rgthree `Seed` 节点, widget=-1), 不是字面值。build_prompt 的 seed 统一只覆写 int 型、跳过连接; 但 rgthree `seed.py:main()` 在 seed∈{-1,-2,-3} 时执行时随机成正整数, 故 detailer 拿不到 -1, 不触发 Impact Pack `np.random.default_rng(-1)` 崩。easy hiresFix(59/60)无 seed。
- **输出链自通**: 启用后 `43 VAEDecode -> 59 hires -> 22 -> 27 Hand -> 28 NSFW -> 29 Face -> 30 Eyes -> 60 hires -> 后处理 -> 13 SaveImage`, 串行 refine 不断头。当前全 bypass 时 VAEDecode 直透 13 = 线上"原生图"来源。
- **组内无前端专用节点**: detailer/hires 组无 WidgetToString / Image Saver* / extra_pnginfo 读者, sanitize 安全。
- **模型缺口(真阻塞)**:
  | 组 | 检测器/模型 | 磁盘 | 解法 |
  |---|---|---|---|
  | Hand(27) | hand_yolov9c.pt(节点9) | ❌(有 hand_yolov8s.pt) | UI 改名再导出 |
  | Face(29) | face_yolov9c.pt(节点11) | ❌(有 face_yolov8m.pt) | UI 改名再导出 |
  | Eyes(30) | Eyeful_v2-Individual.pt(节点12) | ❌(无替代) | 下载或放弃 |
  | hires(59/60) | 4x_foolhardy_Remacri.pth | ❌(upscale_models 空) | 下载或放弃 |
  | NSFW(28) | ntd11_anime_nsfw_segm_v5-variant1.pt | ✅ | 模型有, 但需 NSFW wildcard + 跟 check_banned 冲突, 建议不开 |
- **重导出 gotcha**: 当前 `server/workflows/AnimaStandardV7.json` 是手工改过的(34 节点, detailer 全缺席, 节点7 face 检测器已 swap 成 face_yolov8m.pt)。重导出会把节点7改回 face_yolov9c.pt(磁盘没有), **必须重导出后再 swap 回 face_yolov8m.pt**(节点7是 infra, 脸部 detailer 链依赖)。

## API 耦合 (我们只动这些)
- **注入 4 个**: `54`(正向 text)/ `6`(seed)/ `56`(width,height)/ `5`(loras)
- **负面 `4`**: 直接改 JSON 常量
- **剔除**: `57`/`58`(WidgetToString / Image Saver Metadata, 前端专用) + `Image Comparer (rgthree)`(展示节点, 见下); `13` Image Saver Simple 换内置 SaveImage
- **Image Comparer 必剥**: 它继承 PreviewImage(OUTPUT_NODE), 中间预览图进 `/history`; `submit_and_wait` 取"第一个有图的节点", 会误取 comparer 中间图而非最终 SaveImage。sanitize_for_api 已加 `Image Comparer (rgthree)` 到剔除集。
- **其余活跃节点原样跑, 不碰**

## 安全修改工作流的方法
1. **首选: 拨组开关**。在 ComfyUI 里用 rgthree Fast Groups Bypasser 把目标组(如 img2img / HandDetailer / hiresFix)从 MUTE 切回活跃 -> 重新导出 API -> 替换 `server/workflows/AnimaStandardV7.json`。**不增删节点**, 只是开关, 风险最低。
2. **注入新值走 config**: 加 `<名>_node: "<id>"` 到 config, 用 `build_prompt` 的 `set_input`。不硬编码 id(见 CLAUDE.md 节点注入准则)。
3. **查源码定格式**: 注入前查本机 custom_nodes 源码的 INPUT_TYPES/execute, 不靠猜(见 D16)。
4. **查连接覆盖**: 目标 input 若是连接 `["id",n]`, set 字面值会断链(像 D16 触发词链那样), 检查下游依赖。
5. **本地验**: ComfyUI 里先手动跑通再接后端。

## 对新灵感的含义 (修正先前判断)
| 灵感 | 先前判断 | 修正后 |
|---|---|---|
| ③参考图理解 | 要改工作流(img2img) | **不用改** -- 这是视觉 LLM 侧(图->Qwen-VL->tag), 走正常 txt2img 即可。anima-prompter-forge 的视觉输入就是这路, 不是 img2img |
| ⑤多轮对话精修 | 要新建 img2img 节点, 高风险 | **链已存在(MUTE)** -- 启用 img2img 组 + 重导出即可, 比新建低风险; 仍需注入 LoadImage 的图片 + 切 ImpactSwitch 路由 |
| 质量免费提升 | - | **拨开关即得**: 启用 HandDetailer/EyesDetailer/hiresFix, 手眼细节和大图质量白送, 不写一行代码(只需重导出工作流) |

## 不改工作流就能做的灵感 (最安全)
①抽卡 re-roll ②标签选择器 UI ④多角色(纯 prompt/NL 改写) ⑥tag 顺序规范 -- 全在 prompt/前端层, 不碰工作流。
