# -*- coding: utf-8 -*-
"""运行两个固定 Prompt 来源/表达方式的盲评对照。"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
DEFAULT_CASES = EXPERIMENT_DIR / "dict_llm_cases.yaml"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "output" / "dict_llm"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EXPERIMENT_DIR))

from server import main as engine
from run_experiment import write_review_files


def load_cases(path: Path):
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {}
    return data.get("cases", [])


async def render(case: dict, key: str, label: str) -> dict:
    seed = int(case["seed"])
    prompt = str(case[key])
    width, height = map(int, str(case["size"]).split("x"))
    random.seed(seed)
    try:
        image = await engine.submit_and_wait("anima", prompt, width, height)
        exists = (engine.IMAGES / image).exists()
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": label, "prompt_en": prompt,
            "negative_extra": "", "size": str(case["size"]), "seed": seed,
            "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "status": "OK" if exists else "FAIL", "image": image if exists else "",
            "error": "" if exists else "image missing",
        }
    except Exception as exc:
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": label, "prompt_en": prompt,
            "negative_extra": "", "size": str(case["size"]), "seed": seed,
            "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "status": "FAIL", "image": "", "error": str(exc),
        }


async def run(cases_path: Path, output_dir: Path, left_key: str, right_key: str,
              left_label: str, right_label: str) -> int:
    cases = load_cases(cases_path)
    rows = []
    for case in cases:
        for key, label in ((left_key, left_label), (right_key, right_label)):
            print(f"[{case['id']}/{label}]", flush=True)
            rows.append(await render(case, key, label))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    review_cases = [{"id": str(case["id"]), "category": str(case["category"]),
                     "input": str(case["input"]), "variants": {left_label: "", right_label: ""}}
                    for case in cases]
    write_review_files(rows, review_cases, output_dir)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"pair experiment: {ok}/{len(rows)} generated")
    print(f"review: {output_dir / 'review.html'}")
    return 0 if ok == len(rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--left-key", default="dictionary_prompt")
    parser.add_argument("--right-key", default="llm_prompt")
    parser.add_argument("--left-label", default="dictionary")
    parser.add_argument("--right-label", default="llm")
    args = parser.parse_args()
    cases = args.cases if args.cases.is_absolute() else ROOT / args.cases
    output = args.output if args.output.is_absolute() else ROOT / args.output
    return asyncio.run(run(cases, output, args.left_key, args.right_key,
                           args.left_label, args.right_label))


if __name__ == "__main__":
    raise SystemExit(main())
