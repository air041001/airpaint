# -*- coding: utf-8 -*-
"""生产画师协议落地后的旧 Prompt/新 Prompt 人眼 A/B 验证。"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
EXPANSION_DIR = EXPERIMENT_DIR / "expansion"
RESOLVED_PATH = EXPERIMENT_DIR / "output" / "phase26" / "resolved_cases.json"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "output" / "production_ab"
SELECTED_IDS = ("E1", "E2", "E4", "E6", "E7")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXPERIMENT_DIR))

from server import main as engine
from run_experiment import expected_workflow_seed, write_review_files


def load_resolved(path: Path = RESOLVED_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


async def prepare(output_dir: Path) -> Path:
    old_cases = {str(row["id"]): row for row in load_resolved()}
    rows = []
    for case_id in SELECTED_IDS:
        old = old_cases[case_id]
        print(f"[{case_id}] current production translation", flush=True)
        new_prompt, breakdown, prompt_ir = await engine.translate(str(old["input"]))
        rows.append({
            "id": case_id,
            "category": str(old["category"]),
            "input": str(old["input"]),
            "size": str(old["size"]),
            "seed": int(old["seed"]),
            "old_prompt": old["variants"]["A1"],
            "new_prompt": new_prompt,
            "new_breakdown": breakdown,
            "new_prompt_ir": prompt_ir,
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "resolved_cases.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"resolved production A/B prompts: {path}", flush=True)
    return path


async def render_variant(case: dict, variant: str, prompt: str) -> dict:
    size = str(case["size"])
    width, height = map(int, size.split("x"))
    seed = int(case["seed"])
    random.seed(seed)
    try:
        image = await engine.submit_and_wait("anima", prompt, width, height)
        exists = (engine.IMAGES / image).exists()
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": variant, "prompt_en": prompt,
            "negative_extra": "", "size": size, "seed": seed,
            "workflow_seed": expected_workflow_seed(seed),
            "status": "OK" if exists else "FAIL", "image": image if exists else "",
            "error": "" if exists else "output image missing",
        }
    except Exception as exc:
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": variant, "prompt_en": prompt,
            "negative_extra": "", "size": size, "seed": seed,
            "workflow_seed": expected_workflow_seed(seed), "status": "FAIL",
            "image": "", "error": str(exc),
        }


async def render(resolved_path: Path, output_dir: Path) -> int:
    cases = json.loads(resolved_path.read_text(encoding="utf-8"))
    workflow = engine.WORKFLOWS["anima"]
    previous_negative_node = workflow.get("negative_text_node")
    workflow["negative_text_node"] = "4"
    try:
        rows = []
        for case in cases:
            for variant in ("old", "new"):
                print(f"[{case['id']}/{variant}] seed={case['seed']}", flush=True)
                prompt = case[f"{variant}_prompt"]
                rows.append(await render_variant(case, variant, prompt))
    finally:
        if previous_negative_node is None:
            workflow.pop("negative_text_node", None)
        else:
            workflow["negative_text_node"] = previous_negative_node
    review_cases = [
        {"id": str(case["id"]), "category": str(case["category"]),
         "input": str(case["input"]), "variants": {"old": "", "new": ""}}
        for case in cases
    ]
    write_review_files(rows, review_cases, output_dir)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"production A/B: {ok}/{len(rows)} images generated", flush=True)
    print(f"review: {output_dir / 'review.html'}", flush=True)
    return 0 if ok == len(rows) else 1


async def main_async(args) -> int:
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if args.mode in {"prepare", "all"}:
        await prepare(output)
    if args.mode in {"render", "all"}:
        resolved = output / "resolved_cases.json"
        if not resolved.exists():
            raise RuntimeError(f"找不到固定 Prompt: {resolved}，先运行 --mode prepare")
        return await render(resolved, output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("prepare", "render", "all"), default="prepare")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
