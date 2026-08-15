# Prompt Rendering Strategy Experiment

## 目标

在当前 base Anima、固定 workflow 和固定 seed 下，比较同一语义的四种 Prompt 表达方式，回答“什么表达更容易让模型画对”，而不是验证某种格式是否更整洁。

## 固定变量

- checkpoint：当前唯一可用的 base Anima
- workflow：`anima` / `AnimaFull.json`
- sampler、steps、CFG、quality prefix、尺寸、seed
- 默认负面模板
- 不使用 LoRA、不启用 detailer

## 变量

- V1：TAG-only
- V2：TAG + short NL
- V3：TAG + weighted spatial/narrative NL
- V4：NL-dominant
- R2/R4 额外 V5：V3 + semantic negative

## Case

7 个 case：简单单人、单人+道具动作、复杂单人姿态、双人对峙、场景+光影、成人 NSFW 单人、成人 NSFW 双人交互。所有 NSFW case 明确为成人虚构角色，不使用未成年或年龄模糊表述。

## 评审

`run_experiment.py` 生成图片、manifest 和盲评 `review.html`。用户按每组 A/B/C/D 选择胜者；vision agent 只标记明显错误，不能替代人眼判断。胜负接近时，对该 case 的全部变体换一个 seed 重跑。

## 结果门槛

实验必须回答：简单内容 TAG/NL 谁更强、复杂关系是否需要 NL/空间锚、权重是否有效、semantic negative 是否有效、NSFW 是否需要独立渲染策略。没有人工胜者表，不进入 Compiler 2.0 或 PLAN-v6。
