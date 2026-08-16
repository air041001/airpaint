# Phase 2.5/2.6 Prompt Expansion

上一轮 E1-E7 的 V1/V2 是历史两路固定 Prompt 对照。Phase 2.6 新增三路实验：

- `A1 current`: 短中文输入，走当前生产翻译链路。
- `A2 detailed`: agent 代写的口语化中文长描述，执行前必须人工抽查，再走当前生产翻译链路。
- `A3 painter`: 短中文输入，走 `run_phase26.py` 内的原型画师协议，直调 API，不改生产 system prompt。

统一底层标准：SFW/NSFW 都按高质量二次元插画处理。NSFW 只在服装状态、身体语言和揭示节奏上做题材分流，不把裸露 tag 当作质量地基。

E6 不在正向 Prompt 强调“成年女性”，使用自然 `woman/1girl` 与明确 NSFW 语义词；年龄安全不靠堆叠 `female/adult woman` 解决。

## 执行

```bash
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode prepare
```

先检查输出目录中的 `resolved_cases.json`，尤其是 7 个 A2 Prompt。确认后再固定 Prompt 生图：

```bash
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode render
```

`render` 固定每个 case 的 seed、尺寸、默认负面和 workflow，生成 21 张图、manifest、盲评页和 review key。输出位于被 gitignore 的 `render_exp/output/phase26/`。

关键平局换 seed 时复用已解析 Prompt，只改变 seed，例如：

```bash
python .tools/eval_set/render_exp/expansion/run_phase26.py --mode render --ids E1,E6,E7 --seed-offset 100 --resolved .tools/eval_set/render_exp/output/phase26/resolved_cases.json --output .tools/eval_set/render_exp/output/phase26_tie_seed
```
