# -*- coding: utf-8 -*-
"""W3 A/B: legacy always-NL vs inferred tag_first/relation_hybrid profile."""
import asyncio
import random
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
CASES_PATH = EXPERIMENT_DIR / "profile_cases.yaml"
OUTPUT_DIR = EXPERIMENT_DIR / "output" / "profile_ab"
sys.path.insert(0, str(ROOT))

from server import main as engine
from run_experiment import write_review_files


def load_cases():
    data = yaml.load(CASES_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {}
    return data.get("cases", [])


async def render(case: dict, strategy: str) -> dict:
    prompt_profile = "relation_hybrid" if strategy == "legacy" else engine.infer_render_profile(case["ir"])
    prompt = engine.compile_prompt(
        [str(item) for item in case.get("char_tags", [])],
        [tag.strip() for tag in str(case["tags"]).split(",")],
        str(case.get("nl") or ""),
        prompt_profile,
    )
    seed = int(case["seed"])
    width, height = map(int, str(case["size"]).split("x"))
    random.seed(seed)
    try:
        image = await engine.submit_and_wait("anima", prompt, width, height)
        exists = (engine.IMAGES / image).exists()
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": strategy,
            "prompt_en": prompt, "negative_extra": "", "size": str(case["size"]),
            "seed": seed, "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "profile": prompt_profile, "status": "OK" if exists else "FAIL",
            "image": image if exists else "", "error": "" if exists else "image missing",
        }
    except Exception as exc:
        return {
            "case_id": str(case["id"]), "category": str(case["category"]),
            "input": str(case["input"]), "variant": strategy,
            "prompt_en": prompt, "negative_extra": "", "size": str(case["size"]),
            "seed": seed, "workflow_seed": random.Random(seed).randint(1, 2**31 - 1),
            "profile": prompt_profile, "status": "FAIL", "image": "", "error": str(exc),
        }


async def run():
    cases = load_cases()
    rows = []
    for case in cases:
        for strategy in ("legacy", "profile"):
            print(f"[{case['id']}/{strategy}]", flush=True)
            rows.append(await render(case, strategy))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "manifest.json").write_text(
        __import__("json").dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    review_cases = [{"id": str(case["id"]), "category": str(case["category"]),
                     "input": str(case["input"]), "variants": {"legacy": "", "profile": ""}}
                    for case in cases]
    write_review_files(rows, review_cases, OUTPUT_DIR)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"profile A/B: {ok}/{len(rows)} generated")
    print(f"review: {OUTPUT_DIR / 'review.html'}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
