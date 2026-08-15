# -*- coding: utf-8 -*-
"""运行 Phase 1.5 的固定 Prompt 渲染策略实验。"""
import asyncio
import html
import json
import os
import random
import sys
import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = ROOT / ".tools" / "eval_set" / "render_exp"
OUTPUT_DIR = EXPERIMENT_DIR / "output"
CASES_PATH = EXPERIMENT_DIR / "cases.yaml"
sys.path.insert(0, str(ROOT))

from server import main as engine


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {}
    return data.get("cases", [])


def variants_for(case: dict) -> list[tuple[str, str, str | None]]:
    variants = [(variant_id, str(prompt), None)
                for variant_id, prompt in case.get("variants", {}).items()]
    for extra in case.get("negative_variants", []):
        base_id = str(extra["base"])
        prompt = str(case["variants"][base_id])
        variants.append((str(extra["id"]), prompt, str(extra["negative"])))
    return variants


def expected_workflow_seed(seed: int) -> int:
    return random.Random(seed).randint(1, 2**31 - 1)


async def render_variant(case: dict, variant_id: str, prompt: str, negative: str | None) -> dict:
    case_id = str(case["id"])
    size = str(case["size"])
    width, height = map(int, size.split("x"))
    seed = int(case["seed"])
    print(f"[{case_id}/{variant_id}] seed={seed} size={size}", flush=True)
    try:
        # build_prompt 的第一个随机调用就是最终工作流 seed。
        random.seed(seed)
        image_name = await engine.submit_and_wait(
            "anima", prompt, width, height, negative_text=negative or None)
        image_path = engine.IMAGES / image_name
        return {
            "case_id": case_id,
            "category": str(case["category"]),
            "input": str(case["input"]),
            "variant": variant_id,
            "prompt_en": prompt,
            "negative_extra": negative or "",
            "size": size,
            "seed": seed,
            "workflow_seed": expected_workflow_seed(seed),
            "status": "OK" if image_path.exists() else "FAIL",
            "image": image_name,
            "error": "" if image_path.exists() else "output image missing",
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "category": str(case["category"]),
            "input": str(case["input"]),
            "variant": variant_id,
            "prompt_en": prompt,
            "negative_extra": negative or "",
            "size": size,
            "seed": seed,
            "workflow_seed": expected_workflow_seed(seed),
            "status": "FAIL",
            "image": "",
            "error": str(exc),
        }


def write_review_files(rows: list[dict], cases: list[dict], output_dir: Path = OUTPUT_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    grouped = {}
    for row in rows:
        grouped.setdefault(row["case_id"], []).append(row)
    review_key = {}
    sections = []
    for index, case in enumerate(cases):
        case_id = str(case["id"])
        entries = list(grouped.get(case_id, []))
        random.Random(20260815 + index).shuffle(entries)
        labels = {}
        cards = []
        for label, row in zip("ABCDE", entries):
            labels[label] = row["variant"]
            if row["status"] == "OK":
                src = Path(os.path.relpath(engine.IMAGES / row["image"], output_dir)).as_posix()
                body = f'<img src="{html.escape(src)}" loading="lazy">'
            else:
                body = f'<div class="failed">FAILED<br>{html.escape(row["error"])}</div>'
            cards.append(
                f'<figure><figcaption>{label}</figcaption>{body}</figure>')
        review_key[case_id] = labels
        sections.append(
            f'<section><h2>{html.escape(case_id)} · {html.escape(str(case["category"]))}</h2>'
            f'<p>{html.escape(str(case["input"]))}</p><div class="grid">'
            + "".join(cards) + "</div></section>")

    (output_dir / "review_key.json").write_text(
        json.dumps(review_key, ensure_ascii=False, indent=2), encoding="utf-8")
    html_doc = """<!doctype html>
<meta charset="utf-8">
<title>AirPaint Prompt Rendering Experiment</title>
<style>
body{background:#111;color:#eee;font:16px system-ui,sans-serif;margin:24px}
h1{font-size:24px} section{border-top:1px solid #444;padding:20px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
figure{margin:0;background:#1d1d1d;padding:8px;border-radius:8px}
figcaption{text-align:center;font-weight:700;font-size:20px;padding:6px}
img{display:block;width:100%;height:auto} .failed{padding:80px 10px;text-align:center;color:#f88}
</style>
<h1>Prompt Rendering Strategy Experiment</h1>
<p>每组标签已随机化。请只记录每组你认为画面最符合输入意图的标签，不要猜变体名称。</p>
""" + "\n".join(sections)
    (output_dir / "review.html").write_text(html_doc, encoding="utf-8")


async def run(cases_path: Path = CASES_PATH, output_dir: Path = OUTPUT_DIR,
              expected_count: int | None = 30) -> int:
    cases = load_cases(cases_path)
    jobs = [(case, *variant) for case in cases for variant in variants_for(case)]
    if expected_count is not None and len(jobs) != expected_count:
        raise RuntimeError(f"实验数量错误: {len(jobs)}，预期 {expected_count}")

    workflow = engine.WORKFLOWS["anima"]
    previous_negative_node = workflow.get("negative_text_node")
    workflow["negative_text_node"] = "4"
    try:
        rows = []
        for case, variant_id, prompt, negative in jobs:
            rows.append(await render_variant(case, variant_id, prompt, negative))
    finally:
        if previous_negative_node is None:
            workflow.pop("negative_text_node", None)
        else:
            workflow["negative_text_node"] = previous_negative_node

    write_review_files(rows, cases, output_dir)
    ok = sum(row["status"] == "OK" for row in rows)
    print(f"\nrender experiment: {ok}/{len(rows)} images generated", flush=True)
    print(f"review: {output_dir / 'review.html'}", flush=True)
    print(f"manifest: {output_dir / 'manifest.json'}", flush=True)
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--expected-count", type=int, default=30)
    args = parser.parse_args()
    cases_path = args.cases if args.cases.is_absolute() else ROOT / args.cases
    output_dir = args.output if args.output.is_absolute() else ROOT / args.output
    raise SystemExit(asyncio.run(run(cases_path, output_dir, args.expected_count)))
