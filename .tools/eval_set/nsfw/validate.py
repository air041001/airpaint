# -*- coding: utf-8 -*-
"""验证 NSFW 结构 candidate：成人声明、IR 完整和 explicit workflow safety。"""
import argparse
import json
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from server import main as engine


IR_FIELDS = {
    "subject", "appearance", "clothing", "action", "pose", "interaction",
    "scene", "composition", "lighting", "mood", "style", "constraints",
}


def load(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    path = args.candidate if args.candidate.is_absolute() else ROOT / args.candidate
    rows = load(path)
    errors = []
    if len(rows) != 8:
        errors.append(f"case 数量 {len(rows)} != 8")
    for row in rows:
        case_id = row.get("id", "?")
        if row.get("status") != "OK":
            errors.append(f"{case_id}: translate status={row.get('status')}")
        if "成年" not in str(row.get("input", "")):
            errors.append(f"{case_id}: input 缺少明确成年声明")
        ir = row.get("prompt_ir")
        if not isinstance(ir, dict) or set(ir) != IR_FIELDS:
            errors.append(f"{case_id}: IR 字段不完整")
        prompt_en = str(row.get("prompt_en") or "")
        if not prompt_en:
            errors.append(f"{case_id}: prompt_en 为空")
            continue
        payload = engine.build_prompt("anima", prompt_en, 832, 1216)
        text = payload["prompt"]["54"]["inputs"]["text"].lower()
        if not text.startswith("masterpiece") or "explicit" not in text:
            errors.append(f"{case_id}: workflow safety 未得到 explicit")
    if errors:
        print("FAIL")
        for error in errors:
            print("-", error)
        return 1
    print(json.dumps({"cases": len(rows), "status": "PASS", "safety": "explicit"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
