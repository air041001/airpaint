# -*- coding: utf-8 -*-
"""比较 Phase 0 baseline 与候选结果的结构不变量。

不比较 LLM 输出字符串是否逐字相同；只检查解析成功、角色保护、tag 去重、排序和 IR 完整性。
用法: python .tools/eval_set/compare.py --candidate .tools/eval_set/candidate.yaml
"""
import argparse
import math
import re
from pathlib import Path

import yaml


BASE = Path(__file__).resolve().parents[2]
EVAL_DIR = BASE / ".tools" / "eval_set"
DEFAULT_BASELINE = EVAL_DIR / "baseline.yaml"
DEFAULT_CANDIDATE = EVAL_DIR / "candidate.yaml"
IR_FIELDS = (
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
)
COUNT_RE = re.compile(r"^(?:solo|solo focus|\d+\+?(?:girls?|boys?|others?)|multiple (?:girls|boys|others))$")


def normalize_id(value) -> str:
    return str(value).zfill(3)


def load_rows(path: Path) -> dict[str, dict]:
    # BaseLoader keeps ids such as 010 as strings; PyYAML's default YAML 1.1
    # parser interprets them as octal and silently changes the case id.
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or []
    if isinstance(data, dict):
        data = data.get("cases", [])
    return {normalize_id(row["id"]): row for row in data}


def tag_list(row: dict) -> list[str]:
    text = str(row.get("TAGS") or row.get("prompt_en") or "")
    return [tag.strip() for tag in text.split(",") if tag.strip()]


def bare_name(tag: str) -> str | None:
    for separator in ("_(", " ("):
        if separator in tag:
            return tag.split(separator, 1)[0].strip().lower()
    return None


def check_ir(row: dict, case_id: str, errors: list[str], warnings: list[str]) -> bool:
    ir = row.get("prompt_ir", row.get("ir"))
    if ir is None:
        return False
    if not isinstance(ir, dict):
        errors.append(f"[{case_id}] IR 不是 object")
        return False
    valid = True
    missing = [field for field in IR_FIELDS if field not in ir]
    if missing:
        errors.append(f"[{case_id}] IR 缺字段: {', '.join(missing)}")
        valid = False
    bad_types = [field for field in IR_FIELDS if field in ir and not isinstance(ir[field], list)]
    if bad_types:
        errors.append(f"[{case_id}] IR 字段不是数组: {', '.join(bad_types)}")
        valid = False
    if not any(ir.get(field) for field in IR_FIELDS):
        warnings.append(f"[{case_id}] IR 全空")
    return valid


def compare(baseline: dict[str, dict], candidate: dict[str, dict], require_ir: bool = False) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(set(baseline) - set(candidate))
    extra = sorted(set(candidate) - set(baseline))
    if missing:
        errors.append(f"候选缺少 case: {', '.join(missing)}")
    if extra:
        warnings.append(f"候选多出 case: {', '.join(extra)}")

    ok = 0
    nl_baseline = 0
    nl_candidate = 0
    ir_valid = 0
    ir_seen = 0
    for case_id, base_row in baseline.items():
        row = candidate.get(case_id)
        if row is None:
            continue
        if row.get("status") != "OK":
            errors.append(f"[{case_id}] status={row.get('status')}")
            continue
        prompt = str(row.get("prompt_en") or row.get("TAGS") or "")
        if not prompt:
            errors.append(f"[{case_id}] prompt_en 为空")
            continue
        ok += 1
        tags = tag_list(row)
        lowered = [tag.lower() for tag in tags]
        duplicates = sorted({tag for tag in lowered if lowered.count(tag) > 1})
        if duplicates:
            errors.append(f"[{case_id}] TAGS 重复: {', '.join(duplicates)}")
        for char_tag in base_row.get("char_tags") or []:
            if char_tag.lower() not in lowered:
                errors.append(f"[{case_id}] 缺少角色 tag: {char_tag}")
            bare = bare_name(char_tag)
            if bare and bare in lowered:
                errors.append(f"[{case_id}] 出现角色裸名: {bare}")
        count_positions = [index for index, tag in enumerate(tags) if COUNT_RE.match(tag.lower())]
        if count_positions and count_positions[0] != 0:
            errors.append(f"[{case_id}] count tag 未位于首位")
        if base_row.get("NL"):
            nl_baseline += 1
        if row.get("NL"):
            nl_candidate += 1
        if row.get("prompt_ir", row.get("ir")) is not None:
            ir_seen += 1
        if check_ir(row, case_id, errors, warnings):
            ir_valid += 1

    if require_ir:
        minimum = math.ceil(len(baseline) * 0.9)
        if ir_valid < minimum:
            errors.append(f"IR 有效率不足: {ir_valid}/{len(baseline)}，要求至少 {minimum}")

    print(f"baseline={len(baseline)}, candidate={len(candidate)}, candidate OK={ok}")
    print(f"NL: baseline={nl_baseline}, candidate={nl_candidate}")
    print(f"IR: valid={ir_valid}, present={ir_seen}, total={len(candidate)}")
    if errors:
        print("\nFAIL")
        for error in errors:
            print(f"- {error}")
    else:
        print("\nPASS: 结构不变量通过（不做字符串相似度判断）")
    if warnings:
        print("\nWARN")
        for warning in warnings:
            print(f"- {warning}")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--require-ir", action="store_true",
                        help="要求至少 90%% case 具有完整 12 字段 IR")
    args = parser.parse_args()
    baseline_path = args.baseline if args.baseline.is_absolute() else BASE / args.baseline
    candidate_path = args.candidate if args.candidate.is_absolute() else BASE / args.candidate
    if not baseline_path.exists():
        raise SystemExit(f"baseline 不存在: {baseline_path}")
    if not candidate_path.exists():
        raise SystemExit(f"candidate 不存在: {candidate_path}")
    raise SystemExit(compare(load_rows(baseline_path), load_rows(candidate_path), args.require_ir))


if __name__ == "__main__":
    main()
