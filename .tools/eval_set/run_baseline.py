# -*- coding: utf-8 -*-
"""Phase 0/1 Evaluation Set 全链路回归跑批。

直接导入 server.main 调用真实 translate()，不再复刻 Prompt Engine 的局部逻辑。
用法: python .tools/eval_set/run_baseline.py [--out .tools/eval_set/candidate.yaml]
默认写 candidate.yaml，不覆盖已提交的 baseline.yaml。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import yaml


BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from server import main as engine


EVAL_DIR = BASE / ".tools" / "eval_set"
DEFAULT_OUT = EVAL_DIR / "candidate.yaml"


def load_yaml(path: Path):
    # BaseLoader keeps ids such as 010 as strings; PyYAML's default YAML 1.1
    # parser interprets them as octal and silently changes the case id.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def load_cases() -> list[tuple[str, str]]:
    data = load_yaml(EVAL_DIR / "cases.yaml") or {}
    cases = []
    for case in data.get("cases", []):
        case_id = str(case["id"]).zfill(3)
        cases.append((case_id, str(case["input"])))
    return cases


def split_compiled_prompt(prompt_en: str) -> tuple[str, str]:
    """拆出当前编译结果中的 TAGS 与可选 NL，供回归文件展示。"""
    tags, separator, nl = prompt_en.partition(". ")
    return (tags, nl) if separator else (prompt_en, "")


def quote(value) -> str:
    """用 JSON 字符串写入 YAML，避免输入里的引号破坏文件格式。"""
    return json.dumps(value, ensure_ascii=False)


async def run_cases(cases: list[tuple[str, str]]) -> list[dict]:
    results = []
    for case_id, text in cases:
        print(f"[{case_id}] {text[:35]}...", flush=True)
        char_tags, _ = engine.match_characters(text)
        try:
            prompt_en, breakdown = await engine.translate(text)
            tags, nl = split_compiled_prompt(prompt_en)
            status = "OK"
            error = ""
        except Exception as exc:
            prompt_en, tags, nl, breakdown = "", "", "", None
            status = "FAIL"
            error = str(exc)
        results.append({
            "id": case_id,
            "input": text,
            "char_tags": char_tags,
            "status": status,
            "prompt_en": prompt_en,
            "TAGS": tags,
            "NL": nl,
            "breakdown": breakdown,
            "error": error,
        })
        # 只限制外部 LLM 请求速率；快速路径不会额外等待。
        await asyncio.sleep(0.5)
    return results


def print_summary(results: list[dict]) -> None:
    ok = sum(1 for result in results if result["status"] == "OK")
    nl_empty = sum(1 for result in results if result["status"] == "OK" and not result["NL"])
    print("\n" + "=" * 60)
    print("验收统计")
    print("=" * 60)
    print(f"成功: {ok}/{len(results)}, NL空: {nl_empty}, NL非空: {ok - nl_empty}")
    for result in results:
        if result["status"] == "FAIL":
            print(f"  [{result['id']}] FAIL: {result['error']}")


def write_results(results: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model = engine.CFG.get("siliconflow_model", "unknown")
    ok = sum(1 for result in results if result["status"] == "OK")
    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(f"# Prompt Engine candidate ({time.strftime('%Y-%m-%d')})\n")
        file.write(f"# model: {model}, cases: {len(results)}, ok: {ok}\n")
        for result in results:
            file.write(f"\n- id: {quote(result['id'])}\n")
            file.write(f"  input: {quote(result['input'])}\n")
            file.write(f"  char_tags: {json.dumps(result['char_tags'], ensure_ascii=False)}\n")
            file.write(f"  status: {result['status']}\n")
            file.write(f"  prompt_en: {quote(result['prompt_en'])}\n")
            file.write(f"  TAGS: {quote(result['TAGS'])}\n")
            file.write(f"  NL: {quote(result['NL'])}\n")
            breakdown = json.dumps(result["breakdown"], ensure_ascii=False) if result["breakdown"] is not None else "null"
            file.write(f"  breakdown: {breakdown}\n")
            if result["error"]:
                file.write(f"  error: {quote(result['error'])}\n")
    print(f"\ncandidate 写入 {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="candidate 输出路径，默认不覆盖 baseline.yaml")
    args = parser.parse_args()
    output_path = args.out if args.out.is_absolute() else BASE / args.out
    cases = load_cases()
    print(f"[setup] model={engine.CFG.get('siliconflow_model', 'unknown')}, {len(cases)} cases")
    results = asyncio.run(run_cases(cases))
    print_summary(results)
    write_results(results, output_path)
    if any(result["status"] != "OK" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
