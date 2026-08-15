# -*- coding: utf-8 -*-
"""第二层固定 Prompt + fixed seed 生图验收。

直接复用真实 Workflow Engine 和 ComfyUI 客户端。Prompt、seed、尺寸均来自
image_cases.yaml，避免 LLM 随机输出污染视觉回归结果。
"""
import asyncio
import random
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server import main as engine


CASES_PATH = ROOT / ".tools" / "eval_set" / "image_cases.yaml"


def load_cases() -> list[tuple[str, str, str, str, int]]:
    data = yaml.load(CASES_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or []
    return [
        (str(case["id"]), str(case["input"]), str(case["prompt_en"]),
         str(case["size"]), int(case["seed"]))
        for case in data
    ]


async def run_case(case_id: str, text: str, prompt_en: str, size: str, seed: int) -> bool:
    width, height = map(int, size.split("x"))
    print(f"\n=== {case_id}: {text[:35]}... size={size} seed={seed} ===", flush=True)
    print(f"prompt_en: {prompt_en}", flush=True)
    try:
        # build_prompt 的第一个随机调用就是最终工作流 seed。
        random.seed(seed)
        expected_seed = random.Random(seed).randint(1, 2**31 - 1)
        image_name = await engine.submit_and_wait("anima", prompt_en, width, height)
        image_path = engine.IMAGES / image_name
        print(f"workflow_seed: {expected_seed}", flush=True)
        print(f"image_path: {image_path}", flush=True)
        return image_path.exists()
    except Exception as exc:
        print(f"FAILED: {exc}", flush=True)
        return False


async def run_all() -> int:
    results = []
    for case in load_cases():
        results.append(await run_case(*case))
    print(f"\nimage acceptance: {sum(results)}/{len(results)} passed", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_all()))
