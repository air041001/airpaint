# -*- coding: utf-8 -*-
"""汇总人工 failure taxonomy 标签。"""
import argparse
from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LABELS = ROOT / ".tools" / "eval_set" / "render_exp" / "labels.yaml"
DEFAULT_TAXONOMY = ROOT / ".tools" / "eval_set" / "taxonomy.yaml"


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def aggregate(labels: dict, taxonomy: dict) -> tuple[Counter, Counter, list[str]]:
    known = {item["id"] for item in taxonomy.get("taxonomy", [])}
    failures = Counter()
    verdicts = Counter()
    errors = []
    for experiment in labels.get("experiments", []):
        for case_id, variants in (experiment.get("cases") or {}).items():
            for variant_id, row in (variants or {}).items():
                verdict = row.get("verdict")
                if verdict not in {"pass", "review", "fail"}:
                    errors.append(f"{experiment.get('id')}/{case_id}/{variant_id}: invalid verdict")
                verdicts[verdict] += 1
                for failure in row.get("failures") or []:
                    if failure not in known:
                        errors.append(f"{experiment.get('id')}/{case_id}/{variant_id}: unknown {failure}")
                    failures[failure] += 1
    return failures, verdicts, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    args = parser.parse_args()
    failures, verdicts, errors = aggregate(load(args.labels), load(args.taxonomy))
    print("verdicts:", dict(verdicts))
    print("failures:", dict(failures))
    if errors:
        print("errors:")
        for error in errors:
            print("-", error)
        return 1
    print("taxonomy labels valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
