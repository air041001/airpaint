# -*- coding: utf-8 -*-
"""LoRA Context legacy/aware 固定条件盲评。"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
DEFAULT_CASES = EXPERIMENT_DIR / "lora_context_cases.yaml"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "output" / "lora_context"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXPERIMENT_DIR))

from server import main as engine
from run_experiment import write_review_files


def load_cases(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("cases", [])


def legacy_trigger(case: dict) -> str:
    if case.get("legacy_key"):
        legacy = engine.CFG.get("loras", {}).get(case["legacy_key"])
        if not legacy:
            raise RuntimeError(f"legacy LoRA 不存在: {case['legacy_key']}")
        return str(legacy.get("trigger") or "").strip()
    groups = case.get("legacy_trained_words") or []
    return ", ".join(str(group).strip() for group in groups if str(group).strip())


async def prepare_case(case: dict) -> tuple[dict[str, str], list[dict], str]:
    intent = str(case["input"])
    selection = [dict(case["selection"])]
    legacy_prompt = (await engine.translate(intent))[0]
    legacy_prompt = ", ".join(x for x in (legacy_trigger(case), legacy_prompt) if x)
    aware_prompt, _, _, meta = await engine.translate(
        intent, lora_selections=selection, include_meta=True
    )
    bindings = meta.get("lora_bindings") or []
    revision = meta.get("registry_revision")
    if not bindings or not revision:
        raise RuntimeError(f"{case['id']} 未得到 LoRA binding snapshot")
    prompts = {"legacy": legacy_prompt, "aware": aware_prompt}
    if case.get("author_control_prompt"):
        prompts["author-control"] = str(case["author_control_prompt"])
    return prompts, bindings, revision


async def render(case: dict, variant: str, prompt: str,
                 bindings: list[dict], revision: str) -> dict:
    seed = int(case["seed"])
    size = str(case["size"])
    width, height = map(int, size.split("x"))
    random.seed(seed)
    try:
        image = await engine.submit_and_wait(
            "anima", prompt, width, height,
            lora_bindings=bindings, registry_revision=revision,
        )
        exists = (engine.IMAGES / image).exists()
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": variant,
            "prompt_en": prompt, "negative_extra": "", "size": size,
            "seed": seed,
            "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "lora_bindings": bindings,
            "registry_revision": revision,
            "status": "OK" if exists else "FAIL",
            "image": image if exists else "",
            "error": "" if exists else "image missing",
        }
    except Exception as exc:
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": variant,
            "prompt_en": prompt, "negative_extra": "", "size": size,
            "seed": seed,
            "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "lora_bindings": bindings,
            "registry_revision": revision,
            "status": "FAIL", "image": "", "error": str(exc),
        }


async def run(cases_path: Path, output_dir: Path, only: set[str] | None) -> int:
    cases = load_cases(cases_path)
    if only:
        cases = [case for case in cases if str(case["id"]) in only]
    rows = []
    for case in cases:
        print(f"[{case['id']}] preparing legacy/aware prompts", flush=True)
        try:
            prompts, bindings, revision = await prepare_case(case)
        except Exception as exc:
            variants = ["legacy", "aware"]
            if case.get("author_control_prompt"):
                variants.append("author-control")
            for variant in variants:
                rows.append({
                    "case_id": str(case["id"]), "category": str(case["category"]),
                    "input": str(case["input"]), "variant": variant,
                    "prompt_en": "", "negative_extra": "", "size": str(case["size"]),
                    "seed": int(case["seed"]), "workflow_seed": "",
                    "status": "FAIL", "image": "", "error": f"prepare: {exc}",
                })
            continue
        for variant, prompt in prompts.items():
            print(f"[{case['id']}/{variant}] fixed seed={case['seed']}", flush=True)
            rows.append(await render(case, variant, prompt, bindings, revision))

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    review_cases = []
    for case in cases:
        variants = ["legacy", "aware"]
        if case.get("author_control_prompt"):
            variants.append("author-control")
        review_cases.append({
            "id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variants": {variant: "" for variant in variants},
        })
    write_review_files(rows, review_cases, output_dir)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"LoRA context A/B: {ok}/{len(rows)} generated", flush=True)
    print(f"review: {output_dir / 'review.html'}", flush=True)
    await engine.CLIENT.aclose()
    return 0 if ok == len(rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    cases_path = args.cases if args.cases.is_absolute() else ROOT / args.cases
    output_dir = args.output if args.output.is_absolute() else ROOT / args.output
    return asyncio.run(run(cases_path, output_dir, set(args.only) or None))


if __name__ == "__main__":
    raise SystemExit(main())
