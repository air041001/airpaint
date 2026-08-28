# 合并工作流 AnimaFull 解剖 (2026-08-12)

> **本文件是当前线上工作流的权威节点参考**。后续 agent 涉及节点 id / 注入点 / 链路, 先查这里, **不用再打开 ComfyUI 读节点源码**。
>
> **当前形态**: `server/workflows/AnimaFull.json` (55 节点) 一份覆盖 txt2img / img2img / 4路精修 (D32 合并)。build_prompt 运行时按 `detailer:{face,hand,nsfw,eyes}` **删未选 detailer 节点** + 重连, 不是"拨 MUTE 组开关"。
>
> 历史: 旧 AnimaStandardV7「拨组开关 + 换 JSON 文件」模型已被 D32 取代; inpaint 曾试做 (加节点 200-206) 后撤销 (D33)。**inpaint 节点已删, 不要按旧文档找它们。**

## 节点分组 (AnimaFull.json, 55 节点)

### A. 核心出图链 (全部活跃)
| id | 类型 | 作用 | 我们注入? |
|---|---|---|---|
| 1 | UNETLoader | 载 Anima 模型 | - |
| 18 | CLIPLoader | Qwen3-0.6B 文本编码器 | - |
| 23 | VAELoader | VAE | - |
| 5 | LoraLoader(LoraManager) | LoRA 加载 | ✅ `loras` widget (D16) |
| 3/4 | ImpactWildcardProcessor | 正/负向 wildcard | 负向 4=常量 |
| 54 | CLIPTextEncode | 正向编码 | ✅ `text` (build_prompt 覆盖) |
| 55 | CLIPTextEncode | 负向编码 | - |
| 56 | EmptyLatentImage | txt2img 空白 latent | ✅ width/height |
| 6 | KSampler | 主采样 | ✅ seed + img2img denoise |
| 43 | VAEDecode | latent->图 (detailer 链源) | - |
| 13 | ImageSaverSimple | 存图 | sanitize 换成 SaveImage |

### B. img2img 路由 (活跃, 同文件)
| id | 类型 | 作用 |
|---|---|---|
| 0 | LoadImage | 上传图输入 (img2img 用) |
| 31 | ImageResizeKJv2 | 缩放上传图 |
| 33 | VAEEncode | 图->latent (img2img) |
| 39/47 | easy int | 共享请求宽/高；节点 31 与节点 56 原始连接均读取这里 |
| 32 | PrimitiveInt | ImpactSwitch 42 的 select（工作流安全默认值 1） |
| 42 | ImpactSwitch | latent 路由: input1=56(EmptyLatent/txt2img), input2=33(VAEEncode/img2img) |

> `build_prompt()` 不依赖工作流默认值：每次都显式设置 `select`，无 `image_filename` 时写 1 走 txt2img；有图片时写 2 走 img2img。历史上只在 img2img 时覆盖 select，导致 txt2img 继承节点 32 的旧值 2，错误地把 `salt.jpg` 经 Resize/VAEEncode 送入采样器，节点 56 的请求尺寸因而被绕过；见 D43。
>
> 请求尺寸必须同时同步到节点 39/47。节点 56 的 `width/height` 会被字面值覆盖供 txt2img 使用，但节点 31 的 `width/height` 必须继续保留到 39/47 的连接供 img2img 使用；只改节点 56 会让图生图静默回落到工作流默认 `832x1216` 并产生补边。见 D51。

### C. 精修 detailer 链 (活跃, build_prompt 按需删节点)
链: `43 VAEDecode -> [27 Hand] -> [28 NSFW] -> [29 Face] -> [30 Eyes] -> 13 SaveImage`

| id | 类型 | 检测器模型 | 精修路 |
|---|---|---|---|
| 27 | FaceDetailerPipe | bbox/hand_yolov9c.pt (节点9) | 手 |
| 28 | FaceDetailerPipe | segm/ntd11_anime_nsfw_segm_v5-variant1.pt (节点10) | NSFW |
| 29 | FaceDetailerPipe | bbox/face_yolov9c.pt (节点11) | 脸 |
| 30 | FaceDetailerPipe | bbox/Eyeful_v2-Individual.pt (节点12) | 眼睛 |

支撑: 7 UltralyticsDetector + 8 SAMLoader + 19 ToDetailerPipe + 26 DifferentialDiffusion + 14-17 EditDetailerPipe (wildcard 提示词作者已填: 手=hand perfect hands / 脸=face detailed face / NSFW=含 [NIPPLES] 等)。参数节点: 35(max_size, 值 1024)/38(guide_size 512)/45(bbox crop)。seed 接 34。

> **build_prompt 拼接逻辑** (D32): 遍历 `config detailer_nodes` (顺序 hand->nsfw->face->eyes = 图链顺序), 选中的设 `image` 连前一节点、未选的 `del` (不可达节点 ComfyUI 懒执行跳过, 省时)。全不选 -> `13.images = [43,0]` (VAEDecode 直通 SaveImage = 快速版)。调参已固定 max_size 1024 / steps 12 (~90s 全精修, 见 DEVLOG 19/29)。

### D. LoRA 触发词链 (活跃, D16 断过)
`37 TriggerWordToggle` + `46/51 StringConcatenate` + `48 RegexReplace` + `57 WidgetToString`。build_prompt 覆盖节点 54 断链, 触发词手动拼进 prompt (config `loras.<key>.trigger`)。

### E. 前端专用节点 (sanitize_for_api 剔除)
- `57 WidgetToString` / `58 Image Saver Metadata`: 依赖 `extra_pnginfo`, API 提交崩。
- `53/67/68/69/70 Image Comparer (rgthree)` (5 个): 继承 PreviewImage(OUTPUT_NODE), 中间预览图进 /history, 干扰 submit_and_wait 取图。**必须剥**。
- `13 Image Saver Simple`: sanitize 换内置 SaveImage。
- `24 Input Parameters (Image Saver)`: steps/cfg/denoise 来源 (只读)。

### F. 参数 widget
`34 Seed`(rgthree) / `39 Width` / `47 Height` / `52 Batch` / `40/41` showAnything(sampler/scheduler) / `50 PrimitiveBoolean`。

## ⭐ 功能模块现状 (不是 MUTE 表 — 都已在文件里)

| 功能 | 节点 | 状态 |
|---|---|---|
| txt2img | 56->42->6 | ✅ 活跃 (默认) |
| img2img | 0->31->33->42 input2 | ✅ 活跃 (`build_prompt` 显式 select=2) |
| 4路精修 | 27/28/29/30 | ✅ 活跃 (build_prompt 删未选) |
| ~~inpaint~~ | ~~200-206~~ | ❌ 已移除 (D33, 效果不达标) |
| ~~高清放大 hires~~ | ~~59/60~~ | ❌ 不存在 (评估不做) |
| ~~区域提示词/ControlNet/SAM3~~ | - | ❌ 评估不做 (见 ROADMAP 3.2) |

## API 耦合 (我们只动这些)
- **注入**: `54`(正向 text) / `6`(seed, img2img denoise) / `39/47`(共享 width/height) / `56`(txt2img width/height) / `5`(loras) / `0`(image_filename) / `42`(txt2img select=1；img2img select=2)
- **detailer 拼接**: config `detailer_nodes` = {hand:27, nsfw:28, face:29, eyes:30}
- **负面 `4`**: 工作流自带常量, 不注入
- **剔除**: `57`/`58`/`53`/`67-70` (WidgetToString / Image Saver Metadata / Image Comparer) + `13` 换 SaveImage
- **其余活跃节点原样跑, 不碰**

## 关键机制备忘 (查证过, 不用再翻源码)
- **seed 不崩**: 4 路 detailer + KSampler 的 seed 接 34 (rgthree Seed, widget=-1)。build_prompt 统一只覆写 int 型、跳过连接; rgthree 在 seed∈{-1,-2,-3} 时执行时随机成正整数, 不触发 Impact Pack `np.random.default_rng(-1)` 崩。
- **输出链**: txt2img/img2img 都走 `43 VAEDecode` -> (detailer 链, 可删) -> `13 SaveImage`。
- **detailer 参数已调优**: max_size=1024(35), steps=12, denoise 0.4/0.3/0.26/0.24 (手/NSFW/脸/眼)。别再调回社区默认 (1536/16 会超时, 见 DEVLOG 29)。
- **img2img 与参考图是两回事**: 传 `image_filename` = img2img (图上重采样); `/api/translate` 传 image = 参考图 (VL 提氛围走 txt2img, 图不进 ComfyUI)。

## 安全修改工作流的方法
1. **加新节点/功能**: 在 ComfyUI 里改 -> Save (API Format) 导出 -> 替换 `server/workflows/AnimaFull.json` -> 核对节点 id (本文档 + config)。
2. **注入新值走 config**: 加 `<名>_node: "<id>"`, build_prompt `set_input`。不硬编码 id。
3. **查源码定格式**: 新节点类型先查本机 custom_nodes 源码的 INPUT_TYPES/execute (见 D16 教训), 不靠猜。
4. **查连接覆盖**: 目标 input 若是连接 `["id",n]`, set 字面值会断链 (像 D16 触发词链), 检查下游。
5. **本地验**: ComfyUI 里手动跑通再接后端。
6. **不要重新导入 inpaint / hires / 区域提示词**: 已评估不做 (D33 / ROADMAP 3.2), 别再当规划。
