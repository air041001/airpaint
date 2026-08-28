# -*- coding: utf-8 -*-
"""Generate fixed-condition AirPaint style-LoRA comparison previews."""
import argparse
import asyncio
import json
import random
import shutil
import struct
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import main as engine


PROTOCOL_VERSION = "style-preview-character-v1"
DEFAULT_ASSETS = ("blue_archive_style", "light_style", "fymriev6_2")
DEFAULT_BASE_ASSET = "deepseek_maid"
DEFAULT_BASE_PROFILE = "maid"
DEFAULT_BASE_OPTIONAL = ("identity_front", "maid_front_upper")
FIXED_PROMPT = (
    "1girl, solo, looking at viewer, character-focused three-quarter portrait, "
    "head to mid-thigh visible, natural relaxed pose, centered composition, "
    "simple pale studio backdrop, soft diffused daylight, gentle rim light, clean silhouette, "
    "detailed face, detailed eyes, detailed hair, detailed fabric, delicate hands, "
    "high quality anime illustration"
)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        raise ValueError(f"{path.name} 不是有效 PNG")
    return struct.unpack(">II", data[16:24])


def write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def render(output_dir: Path, assets: tuple[str, ...], seed: int,
                 strength: float | None, size: str, include_baseline: bool = True,
                 base_asset: str = DEFAULT_BASE_ASSET,
                 base_profile: str = DEFAULT_BASE_PROFILE,
                 base_optional: tuple[str, ...] = DEFAULT_BASE_OPTIONAL,
                 reference: Path | None = None, denoise: float | None = None) -> int:
    if strength is not None and not 0 <= strength <= 2:
        raise SystemExit("strength 必须在 0~2")
    if reference and (denoise is None or not 0 <= denoise <= 1):
        raise SystemExit("使用 reference 时必须提供 0~1 的 denoise")
    try:
        width, height = (int(value) for value in size.lower().split("x", 1))
    except (TypeError, ValueError):
        raise SystemExit("size 必须使用 WIDTHxHEIGHT")

    output_dir.mkdir(parents=True, exist_ok=True)
    uploaded_reference = None
    if reference:
        if not reference.exists():
            raise SystemExit(f"reference 不存在: {reference}")
        uploaded_reference = await engine.upload_image_to_comfy(reference.read_bytes())
    _, revision = engine.LORA_REGISTRY.snapshot()
    workflow_seed = random.Random(seed).randint(1, 2**31 - 1)
    variants = ([(f'{base_asset}_baseline', None)] if include_baseline else [])
    variants.extend((asset, asset) for asset in assets)
    base_selection = {
        "key": base_asset,
        "mode": "explicit",
        "profile": base_profile,
        "optional": list(base_optional),
    }
    manifest = {
        "protocol": PROTOCOL_VERSION,
        "workflow": "anima",
        "registry_revision": revision,
        "size": size,
        "seed_input": seed,
        "workflow_seed": workflow_seed,
        "style_strength_override": strength,
        "base_selection": base_selection,
        "detailer": False,
        "reference": str(reference.resolve()) if reference else None,
        "denoise": denoise,
        "prompt_en": FIXED_PROMPT,
        "variants": [],
    }
    manifest_path = output_dir / "manifest.json"
    failures = 0

    for index, (label, asset_key) in enumerate(variants):
        row = {"label": label, "asset_key": asset_key, "status": "running"}
        manifest["variants"].append(row)
        write_manifest(manifest_path, manifest)
        selections = [dict(base_selection)]
        if asset_key:
            style_selection = {"key": asset_key, "mode": "explicit"}
            if strength is not None:
                style_selection.update({
                    "strength_model": strength,
                    "strength_clip": strength,
                })
            selections.append(style_selection)
        bindings, warnings, current_revision = engine.resolve_lora_selections(selections)
        if current_revision != revision:
            raise RuntimeError("LoRA Registry 在预览批次中发生变化，请重新生成整组")
        row["warnings"] = warnings
        row["bindings"] = bindings
        row["compiled_prompt"] = engine.compile_lora_bindings(FIXED_PROMPT, bindings)
        started = time.time()
        print(f"[{index + 1}/{len(variants)}] {label} seed={seed} workflow_seed={workflow_seed}", flush=True)
        try:
            random.seed(seed)
            generated_name = await engine.submit_and_wait(
                "anima", FIXED_PROMPT, width, height,
                image_filename=uploaded_reference, denoise=denoise,
                detailer={}, lora_bindings=bindings,
                registry_revision=revision if bindings else None,
            )
            source = engine.IMAGES / generated_name
            target = output_dir / f"{index:02d}_{label}.png"
            shutil.copy2(source, target)
            actual_width, actual_height = png_size(target)
            row.update({
                "status": "ok",
                "file": target.name,
                "source_image": generated_name,
                "actual_size": f"{actual_width}x{actual_height}",
                "elapsed_seconds": round(time.time() - started, 2),
            })
            print(f"  -> {target.name} {actual_width}x{actual_height} {row['elapsed_seconds']}s", flush=True)
        except Exception as exc:
            failures += 1
            row.update({
                "status": "failed",
                "error": str(exc),
                "elapsed_seconds": round(time.time() - started, 2),
            })
            print(f"  !! {exc}", flush=True)
        write_manifest(manifest_path, manifest)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成固定条件风格 LoRA 预览候选")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", nargs="*", default=list(DEFAULT_ASSETS))
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--strength", type=float, help="覆盖全部风格 LoRA 强度；省略则使用各自 Registry 默认值")
    parser.add_argument("--size", default="896x1152")
    parser.add_argument("--base-asset", default=DEFAULT_BASE_ASSET)
    parser.add_argument("--base-profile", default=DEFAULT_BASE_PROFILE)
    parser.add_argument("--base-optional", nargs="*", default=list(DEFAULT_BASE_OPTIONAL))
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--denoise", type=float)
    args = parser.parse_args()
    return asyncio.run(render(
        args.output, tuple(args.assets), args.seed, args.strength, args.size,
        include_baseline=not args.skip_baseline,
        base_asset=args.base_asset, base_profile=args.base_profile,
        base_optional=tuple(args.base_optional),
        reference=args.reference, denoise=args.denoise,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
