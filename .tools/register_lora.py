# -*- coding: utf-8 -*-
"""AirPaint LoRA Registry onboarding tool.

它只把人确认过的语义/Profile 写入 versioned lora_registry.yaml；Civitai/metadata
只作为候选展示，不自动 promote 为正式 trigger knowledge。
"""
import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

import httpx
import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import main


REGISTRY_PATH = main.LORA_REGISTRY_PATH
LORA_DIR = main.LORA_DIR


def load_registry() -> dict:
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) if REGISTRY_PATH.exists() else None
    raw = raw or {"schema_version": 1, "loras": {}}
    main.HotLoraRegistry.validate(raw)
    return raw


def atomic_write_registry(raw: dict) -> None:
    main.HotLoraRegistry.validate(raw)
    text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120)
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=REGISTRY_PATH.parent,
            prefix="lora_registry.", suffix=".tmp", delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(REGISTRY_PATH)


def split_tags(value: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in re.split(r"[,，]\s*", value) if x.strip()))


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def ask_choice(label: str, choices: list[str], default: str) -> str:
    while True:
        value = ask(f"{label} ({'/'.join(choices)})", default)
        if value in choices:
            return value
        print(f"请输入: {', '.join(choices)}")


def fetch_civitai_candidate(url: str) -> dict:
    match = re.search(r"/models/(\d+)", url)
    if not match:
        print("Civitai URL 未包含 model id，跳过联网候选。")
        return {}


def show_local_civitai_candidate(filename: str) -> dict:
    """展示同名 .civitai.info 的作者说明/示例，不自动写 Registry。"""
    path = (LORA_DIR / filename).with_suffix(".civitai.info")
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        model = raw.get("model") or {}
        description = model.get("description") or raw.get("description") or ""
        code_blocks = []
        for block in re.findall(r"<pre><code>(.*?)</code></pre>", description, flags=re.I | re.S):
            clean = html.unescape(re.sub(r"<[^>]+>", " ", block))
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean:
                code_blocks.append(clean)
        examples = []
        strengths = set()
        for image in raw.get("images") or []:
            meta = image.get("meta") or {}
            if meta.get("prompt"):
                examples.append(meta["prompt"])
            for resource in meta.get("additionalResources") or []:
                if resource.get("type") == "lora" and resource.get("strength") is not None:
                    strengths.add((resource.get("strength"), resource.get("strengthClip")))
        candidate = {
            "source": str(path),
            "name": model.get("name") or raw.get("name"),
            "baseModel": raw.get("baseModel"),
            "trainedWords": raw.get("trainedWords") or [],
            "authorCodeBlocks": code_blocks,
            "exampleStrengths": sorted(strengths, key=str),
            "examplePrompts": examples,
        }
        print("\n--- local .civitai.info candidate（只供人阅读，不自动写 trigger）---")
        print(json.dumps(candidate, ensure_ascii=False, indent=2)[:20000])
        return candidate
    except Exception as exc:
        print(f"本地 .civitai.info 读取失败，继续手工录入: {exc}")
        return {}
    try:
        response = httpx.get(f"https://civitai.com/api/v1/models/{match.group(1)}", timeout=20)
        response.raise_for_status()
        data = response.json()
        versions = data.get("modelVersions") or []
        candidate = {
            "name": data.get("name", ""),
            "type": str(data.get("type", "")).lower(),
            "description": re.sub(r"<[^>]+>", " ", data.get("description") or ""),
            "versions": [
                {"name": v.get("name"), "baseModel": v.get("baseModel"),
                 "trainedWords": v.get("trainedWords") or []}
                for v in versions
            ],
        }
        print("\n--- Civitai candidate（只供人阅读，不自动写 trigger）---")
        print(json.dumps(candidate, ensure_ascii=False, indent=2)[:8000])
        return candidate
    except Exception as exc:
        print(f"Civitai 获取失败，降级为纯手工录入: {exc}")
        return {}


def list_inventory(raw: dict) -> None:
    registered = {asset.get("file") for asset in raw["loras"].values()}
    print("\n已注册：")
    for key, asset in raw["loras"].items():
        profiles = ", ".join((asset.get("profiles") or {}).keys()) or "-"
        print(f"  {key:24} {asset.get('type'):10} {asset.get('file')}  profiles={profiles}")
    print("\n未注册本地文件：")
    missing = [path.name for path in sorted(LORA_DIR.glob("*.safetensors")) if path.name not in registered]
    if missing:
        for name in missing:
            metadata = main._read_lora_metadata(LORA_DIR / name)
            base = metadata.get("base_model") or metadata.get("baseModel") or "?"
            print(f"  {name}  base={base}")
    else:
        print("  无")


def collect_optional(existing: dict | None = None) -> dict:
    optional = {}
    existing = existing or {}
    while ask_choice("继续录入 optional concept?", ["y", "n"], "n") == "y":
        option_id = ask("optional id（英文稳定 ID）")
        old = existing.get(option_id) or {}
        optional[option_id] = {
            "name": ask("显示名称", old.get("name", option_id)),
            "aliases": split_tags(ask("匹配别名（逗号分隔）", ", ".join(old.get("aliases", [])))),
            "provides": split_tags(ask("它提供的语义（逗号分隔）", ", ".join(old.get("provides", [])))),
            "tags": split_tags(ask("exact tags（逗号分隔）", ", ".join(old.get("tags", [])))),
        }
    return optional or existing


def collect_profile(profile_id: str, existing: dict | None = None) -> dict:
    existing = existing or {}
    return {
        "name": ask("Profile 显示名称", existing.get("name", profile_id)),
        "aliases": split_tags(ask("别名（逗号分隔）", ", ".join(existing.get("aliases", [])))),
        "provides": split_tags(ask("LoRA 已提供的语义（逗号分隔）", ", ".join(existing.get("provides", [])))),
        "required_tags": split_tags(ask("required exact tags（逗号分隔）", ", ".join(existing.get("required_tags", [])))),
        "default_tags": split_tags(ask("minimal default tags（逗号分隔）", ", ".join(existing.get("default_tags", [])))),
        "optional_tags": collect_optional(existing.get("optional_tags")),
        "source": ask("来源", existing.get("source", "author description")),
        "verified": ask_choice("验证状态", ["candidate", "curated", "verified"], existing.get("verified", "candidate")),
        "notes": ask("备注", existing.get("notes", "")),
    }


def collect_asset(filename: str, asset_id: str | None, existing: dict | None = None) -> tuple[str, dict]:
    existing = existing or {}
    default_id = asset_id or re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower()).strip("_")
    asset_id = ask("Asset ID（英文稳定 ID）", default_id)
    lora_type = ask_choice("类型", ["character", "style", "action", "expression", "unknown"],
                           existing.get("type", "character"))
    policy = ask_choice("Trigger policy", ["profile", "required", "none"],
                        existing.get("trigger_policy", "profile" if lora_type == "character" else "required"))
    asset = {
        "name": ask("显示名称", existing.get("name", Path(filename).stem)),
        "type": lora_type,
        "file": filename,
        "trigger_policy": policy,
        "default_strength": {
            "model": float(ask("默认 model strength", str((existing.get("default_strength") or {}).get("model", 1.0)))),
            "clip": float(ask("默认 clip strength", str((existing.get("default_strength") or {}).get("clip", 1.0)))),
        },
    }
    if policy == "profile":
        profiles = {}
        old_profiles = existing.get("profiles") or {}
        while True:
            profile_id = ask("Profile ID（留空结束）")
            if not profile_id:
                break
            profiles[profile_id] = collect_profile(profile_id, old_profiles.get(profile_id))
        if not profiles:
            profiles = old_profiles
        if not profiles:
            raise ValueError("profile policy 至少需要一个 Profile")
        asset["selection"] = {
            "default_profile": ask("默认 Profile ID（无法自动匹配时使用，可留空）",
                                   (existing.get("selection") or {}).get("default_profile", "")) or None,
            "allow_multiple_profiles": False,
        }
        asset["profiles"] = profiles
        asset["legacy_keys"] = existing.get("legacy_keys") or {}
    else:
        asset["required_tags"] = [] if policy == "none" else split_tags(
            ask("required exact tags（逗号分隔）", ", ".join(existing.get("required_tags", []))))
        asset["provides"] = split_tags(ask("LoRA 已提供的语义（逗号分隔）",
                                           ", ".join(existing.get("provides", []))))
        asset["source"] = ask("来源", existing.get("source", "author description"))
        asset["verified"] = ask_choice("验证状态", ["candidate", "curated", "verified"],
                                        existing.get("verified", "candidate"))
        asset["legacy_keys"] = existing.get("legacy_keys") or []
    return asset_id, asset


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="注册/编辑 AirPaint LoRA semantic profiles")
    parser.add_argument("filename", nargs="?", help="models/loras 下的 safetensors 文件名")
    parser.add_argument("--list", action="store_true", help="列出 registry 与未注册文件")
    parser.add_argument("--validate", action="store_true", help="只校验 registry")
    parser.add_argument("--inspect", metavar="FILENAME", help="只展示同名本地 .civitai.info 候选")
    parser.add_argument("--edit", metavar="ASSET_ID", help="编辑已有 Asset")
    parser.add_argument("--civitai", metavar="URL", help="展示 Civitai 候选描述（不自动解析）")
    args = parser.parse_args()
    raw = load_registry()
    if args.inspect:
        candidate = show_local_civitai_candidate(args.inspect)
        return 0 if candidate else 1
    if args.validate:
        print(f"registry valid: {len(raw['loras'])} assets")
        return 0
    if args.list or (not args.filename and not args.edit):
        list_inventory(raw)
        if not args.edit and not args.filename:
            return 0
    if args.civitai:
        fetch_civitai_candidate(args.civitai)
    existing = None
    asset_id = args.edit
    filename = args.filename
    if args.edit:
        existing = raw["loras"].get(args.edit)
        if not existing:
            raise SystemExit(f"未知 Asset: {args.edit}")
        filename = existing["file"]
    if not filename:
        raise SystemExit("请提供 filename 或 --edit ASSET_ID")
    show_local_civitai_candidate(filename)
    if not (LORA_DIR / filename).exists():
        answer = ask_choice(f"本地未找到 {filename}，仍写入 registry?", ["y", "n"], "n")
        if answer != "y":
            return 1
    new_id, asset = collect_asset(filename, asset_id, existing)
    candidate = {"schema_version": 1, "loras": dict(raw["loras"])}
    candidate["loras"][new_id] = asset
    main.HotLoraRegistry.validate(candidate)
    print("\n--- 将写入的 Asset ---")
    print(yaml.safe_dump({new_id: asset}, allow_unicode=True, sort_keys=False, width=120))
    if ask_choice("确认原子写入 lora_registry.yaml?", ["y", "n"], "n") != "y":
        print("已取消")
        return 1
    atomic_write_registry(candidate)
    print(f"已写入 {REGISTRY_PATH}；后端下次访问会热更新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
