# -*- coding: utf-8 -*-
"""Phase 2.6 三路 Prompt Expansion 实验 runner.

prepare 只调用翻译模型并保存固定 Prompt，不生成图片；用户审查 A2 后再执行 render。
A3 的画师协议只存在本文件，不修改生产 SILICONFLOW_SYSTEM_PROMPT。
"""
import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
EXPANSION_DIR = EXPERIMENT_DIR / "expansion"
CASES_PATH = EXPANSION_DIR / "phase26_cases.yaml"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "output" / "phase26"
RESOLVED_NAME = "resolved_cases.json"
MAX_PAINTER_ELEMENTS = 20
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXPERIMENT_DIR))

from server import main as engine
from run_experiment import expected_workflow_seed, write_review_files


PAINTER_SYSTEM_PROMPT = """You are an experimental painter-style prompt planner for the base Anima anime image model.
Turn the user's short Chinese image idea into one compact positive prompt for direct image generation.
Output EXACTLY one line beginning with `PROMPT:` and then a comma-separated English prompt. Output nothing else.

Use five completion layers, in this order:
1. Lock the explicit subject, count, named character, core object, and requested action.
2. Add only a plausible appearance, clothing, pose, or body-language detail that supports the explicit subject and action.
3. Anchor the scene and add at most one conservative supporting prop when the setting naturally requires it; never add an
   unrequested character, weapon, named IP, or a new main action.
4. Choose a readable camera/framing/composition that serves the input, rather than an elaborate cinematic effect.
5. Add restrained lighting, mood, material, and anime line/shading details that make the scene drawable.

Rules:
- Use lowercase Danbooru-like tags and short English clauses; put a count tag such as `1girl` first when a person is implied.
- Keep the whole result to about 20 comma-separated elements or fewer. Prefer concrete, drawable details over adjectives.
- Preserve every explicit constraint. Do not invent a second subject, conflicting clothing, unrelated prop, or different location.
- Negative suppression is internal: do not emit a negative prompt, quality/score tags, or words such as `text`, `watermark`, `extra person`,
  and do not describe things that should be absent. Keep the positive prompt clean.
- Do not use realistic, photorealistic, 3d, render, or other non-anime style terms.
- For a clearly NSFW input, retain its explicit content and use natural `1girl` or `woman` wording without adding age labels.
  Improve quality through restrained pose, gaze, facial expression, body language, fabric/skin material, and reveal pacing.
  Add subtle sensual expression/tension language when appropriate, but do not invent a new sex act, extra person, or unrelated fetish.
- For SFW input, do not sexualize the subject.
"""


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {}
    return data.get("cases", [])


def _clean_painter_prompt(raw: str) -> str:
    text = raw.strip().strip("`").strip()
    for line in text.splitlines():
        if line.strip().lower().startswith("prompt:"):
            text = line.split(":", 1)[1].strip()
            break
    text = re.sub(r"^(?:positive prompt|prompt)\s*:\s*", "", text, flags=re.IGNORECASE)
    elements = []
    seen = set()
    for item in text.replace(";", ",").split(","):
        item = re.sub(r"\s+", " ", item.strip().strip(". "))
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            elements.append(item.lower())
    if not elements:
        raise RuntimeError("画师补全返回空 Prompt")
    return ", ".join(elements[:MAX_PAINTER_ELEMENTS])


async def translate_current(text: str) -> dict:
    prompt, breakdown, prompt_ir = await engine.translate(text)
    if not prompt.strip():
        raise RuntimeError("当前翻译返回空 Prompt")
    return {
        "prompt_en": prompt,
        "source": "production_translate",
        "source_text": text,
        "breakdown": breakdown,
        "prompt_ir": prompt_ir,
    }


async def expand_painter(text: str) -> dict:
    api_key = engine.CFG.get("siliconflow_api_key", "").strip()
    if not api_key:
        raise RuntimeError("siliconflow_api_key 未在 config.yaml 中配置")
    model = engine.CFG.get("siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash")
    thinking = bool(engine.CFG.get("translate_enable_thinking", False))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PAINTER_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.4,
        "max_tokens": 320,
        "enable_thinking": thinking,
    }
    response = await engine.CLIENT.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=40,
    )
    if response.status_code != 200:
        raise RuntimeError(f"画师补全服务返回 {response.status_code}: {response.text[:200]}")
    content = response.json()["choices"][0]["message"]["content"].strip()
    if "</think>" in content:
        content = content.split("</think>", 1)[1].strip()
    prompt = _clean_painter_prompt(content)
    return {
        "prompt_en": prompt,
        "source": "phase26_painter_prototype",
        "source_text": text,
        "element_count": len(prompt.split(", ")),
    }


async def prepare(cases: list[dict], output_dir: Path) -> Path:
    resolved = []
    for case in cases:
        case_id = str(case["id"])
        print(f"[{case_id}] A1 current translation", flush=True)
        a1 = await translate_current(str(case["input"]))
        print(f"[{case_id}] A2 detailed translation", flush=True)
        a2 = await translate_current(str(case["detailed_input"]))
        print(f"[{case_id}] A3 painter prototype", flush=True)
        a3 = await expand_painter(str(case["input"]))
        resolved.append({
            "id": case_id,
            "category": str(case["category"]),
            "input": str(case["input"]),
            "detailed_input": str(case["detailed_input"]),
            "size": str(case["size"]),
            "seed": int(case["seed"]),
            "variants": {"A1": a1["prompt_en"], "A2": a2["prompt_en"], "A3": a3["prompt_en"]},
            "sources": {"A1": a1, "A2": a2, "A3": a3},
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / RESOLVED_NAME
    path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"resolved prompts: {path}", flush=True)
    print("A2 detailed_input 已写入 fixture；请在 render 前抽查 resolved_cases.json 中的 A2 Prompt。", flush=True)
    return path


async def render_variant(case: dict, variant: str, prompt: str, seed_offset: int = 0) -> dict:
    case_id = str(case["id"])
    size = str(case["size"])
    width, height = map(int, size.split("x"))
    seed = int(case["seed"]) + seed_offset
    print(f"[{case_id}/{variant}] seed={seed} size={size}", flush=True)
    try:
        random.seed(seed)
        image = await engine.submit_and_wait("anima", prompt, width, height)
        exists = (engine.IMAGES / image).exists()
        return {
            "case_id": case_id,
            "category": str(case["category"]),
            "input": str(case["input"]),
            "variant": variant,
            "prompt_en": prompt,
            "negative_extra": "",
            "size": size,
            "seed": seed,
            "workflow_seed": expected_workflow_seed(seed),
            "status": "OK" if exists else "FAIL",
            "image": image if exists else "",
            "error": "" if exists else "output image missing",
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "category": str(case["category"]),
            "input": str(case["input"]),
            "variant": variant,
            "prompt_en": prompt,
            "negative_extra": "",
            "size": size,
            "seed": seed,
            "workflow_seed": expected_workflow_seed(seed),
            "status": "FAIL",
            "image": "",
            "error": str(exc),
        }


async def render(resolved_path: Path, output_dir: Path,
                 case_ids: set[str] | None = None, seed_offset: int = 0) -> int:
    all_cases = json.loads(resolved_path.read_text(encoding="utf-8"))
    cases = [case for case in all_cases if not case_ids or str(case["id"]) in case_ids]
    if not cases:
        raise RuntimeError("没有匹配的实验 case")
    jobs = [(case, variant, prompt)
            for case in cases for variant, prompt in case["variants"].items()]
    workflow = engine.WORKFLOWS["anima"]
    previous_negative_node = workflow.get("negative_text_node")
    workflow["negative_text_node"] = "4"
    try:
        rows = [await render_variant(case, variant, prompt, seed_offset)
                for case, variant, prompt in jobs]
    finally:
        if previous_negative_node is None:
            workflow.pop("negative_text_node", None)
        else:
            workflow["negative_text_node"] = previous_negative_node
    review_cases = [
        {"id": str(case["id"]), "category": str(case["category"]),
         "input": str(case["input"]), "variants": case["variants"]}
        for case in cases
    ]
    write_review_files(rows, review_cases, output_dir)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"\nphase26 render: {ok}/{len(rows)} images generated", flush=True)
    print(f"review: {output_dir / 'review.html'}", flush=True)
    return 0 if ok == len(rows) else 1


async def main_async(args) -> int:
    source = args.cases if args.cases.is_absolute() else ROOT / args.cases
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if args.mode in {"prepare", "all"}:
        await prepare(load_cases(source), output)
    if args.mode in {"render", "all"}:
        resolved = args.resolved if args.resolved else output / RESOLVED_NAME
        if not resolved.is_absolute():
            resolved = ROOT / resolved
        if not resolved.exists():
            raise RuntimeError(f"找不到固定 Prompt: {resolved}，先运行 --mode prepare")
        case_ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
        return await render(resolved, output, case_ids, args.seed_offset)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("prepare", "render", "all"), default="prepare")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resolved", type=Path,
                        help="render 使用的固定 Prompt manifest；补测时可复用主实验 manifest")
    parser.add_argument("--ids", help="只渲染逗号分隔的 case，例如 E1,E6,E7")
    parser.add_argument("--seed-offset", type=int, default=0,
                        help="在已固定 case seed 上增加偏移，只用于换 seed 补测")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
