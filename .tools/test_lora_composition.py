# -*- coding: utf-8 -*-
"""LoRA 多选/同文件多 Profile 的纯函数回归。"""
from contextlib import contextmanager
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import main


def profile(name: str, tag: str) -> dict:
    return {
        "name": name,
        "aliases": [name],
        "provides": [f"character {name}"],
        "required_tags": [tag],
        "default_tags": [f"{name} outfit"],
        "optional_tags": {},
    }


def asset(key: str, lora_type: str, filename: str, *, profiles=None,
          allow_multiple=False, strength=1.0) -> dict:
    result = {
        "key": key,
        "type": lora_type,
        "name": key,
        "file": filename,
        "trigger_policy": "profile" if profiles else "none",
        "profiles": profiles or {},
        "selection": {
            "default_profile": next(iter(profiles)) if profiles else None,
            "allow_multiple_profiles": allow_multiple,
        },
        "provides": [f"{key} style"],
        "strength_model": strength,
        "strength_clip": strength,
        "configured": True,
        "legacy_keys": {},
    }
    return result


def registry_fixture() -> dict[str, dict]:
    registry = {
        "cast": asset(
            "cast", "character", "cast.safetensors",
            profiles={"alice": profile("Alice", "alice_tag"),
                      "bob": profile("Bob", "bob_tag"),
                      "cara": profile("Cara", "cara_tag")},
            allow_multiple=True,
        ),
        "single": asset(
            "single", "character", "single.safetensors",
            profiles={"dora": profile("Dora", "dora_tag")},
        ),
        "locked_cast": asset(
            "locked_cast", "character", "locked.safetensors",
            profiles={"one": profile("One", "one_tag"),
                      "two": profile("Two", "two_tag")},
            allow_multiple=False,
        ),
    }
    for index in range(6):
        key = f"style_{index}"
        registry[key] = asset(key, "style", f"{key}.safetensors", strength=0.5 + index * 0.05)
    return registry


@contextmanager
def use_registry(registry: dict[str, dict]):
    previous = main.get_lora_registry
    main.get_lora_registry = lambda: registry
    try:
        yield
    finally:
        main.get_lora_registry = previous


def test_same_file_multi_profile_is_one_binding_and_one_loader_entry():
    registry = registry_fixture()
    with use_registry(registry):
        selections = main.normalize_lora_selections([
            {"key": "cast", "profiles": ["alice", "bob"], "mode": "explicit",
             "strength_model": 0.65, "strength_clip": 0.65},
            {"key": "cast", "profile": "alice", "mode": "explicit"},
        ])
        assert len(selections) == 1, selections
        assert selections[0]["profiles"] == ["alice", "bob"], selections
        bindings, warnings, _ = main.resolve_lora_selections(selections)
        assert not warnings, warnings
        assert len(bindings) == 1, bindings
        assert bindings[0]["profiles"] == ["alice", "bob"], bindings
        assert bindings[0]["profile"] is None, bindings
        assert bindings[0]["injected_tags"] == [
            "alice_tag", "Alice outfit", "bob_tag", "Bob outfit"
        ], bindings
        entries = main._workflow_lora_entries(bindings)
        assert entries == [{
            "name": "cast.safetensors", "strength": 0.65,
            "clipStrength": 0.65, "active": True,
        }], entries
        payload = main.build_prompt(
            "anima", "2girls, garden", 832, 1216, lora_bindings=bindings)
        loader = payload["prompt"][str(main.WORKFLOWS["anima"]["lora_node"])]["inputs"]["loras"]["__value__"]
        assert len(loader) == 1 and loader[0]["name"] == "cast.safetensors", loader


def test_character_limit_counts_profiles_not_files():
    registry = registry_fixture()
    with use_registry(registry):
        allowed = main.normalize_lora_selections([
            {"key": "cast", "profiles": ["alice", "bob"], "mode": "explicit"},
            {"key": "single", "profile": "dora", "mode": "explicit"},
        ])
        assert len(allowed) == 2, allowed
        try:
            main.normalize_lora_selections([
                {"key": "cast", "profiles": ["alice", "bob", "cara"], "mode": "explicit"},
                {"key": "single", "profile": "dora", "mode": "explicit"},
            ])
        except main.HTTPException as exc:
            assert exc.status_code == 400 and "最多选择 3" in str(exc.detail), exc.detail
        else:
            raise AssertionError("four character Profiles must be rejected")


def test_legacy_registry_flag_does_not_block_same_file_multi_profile():
    registry = registry_fixture()
    with use_registry(registry):
        selections = main.normalize_lora_selections([
            {"key": "locked_cast", "profiles": ["one", "two"], "mode": "explicit"}
        ])
        assert selections[0]["profiles"] == ["one", "two"], selections
        bindings, warnings, _ = main.resolve_lora_selections(selections)
        assert not warnings and bindings[0]["profiles"] == ["one", "two"], bindings
        assert len(main._workflow_lora_entries(bindings)) == 1, bindings


def test_style_stack_has_no_product_cap_and_keeps_per_asset_strength():
    registry = registry_fixture()
    with use_registry(registry):
        requested = [
            {"key": f"style_{index}", "mode": "explicit",
             "strength_model": 0.25 + index * 0.1,
             "strength_clip": 0.25 + index * 0.1}
            for index in range(6)
        ]
        bindings, warnings, _ = main.resolve_lora_selections(requested)
        assert not warnings and len(bindings) == 6, bindings
        entries = main._workflow_lora_entries(bindings)
        assert len(entries) == 6, entries
        assert entries[0]["strength"] == 0.25, entries
        assert entries[-1]["strength"] == 0.75, entries


def test_same_physical_file_cannot_hide_conflicting_strengths():
    bindings = [
        {"key": "a", "file": "shared.safetensors", "type": "style",
         "strength_model": 0.5, "strength_clip": 0.5},
        {"key": "b", "file": "shared.safetensors", "type": "style",
         "strength_model": 0.8, "strength_clip": 0.8},
    ]
    try:
        main._workflow_lora_entries(bindings)
    except main.HTTPException as exc:
        assert exc.status_code == 400 and "不同强度重复选择" in str(exc.detail), exc.detail
    else:
        raise AssertionError("same file with conflicting strengths must be rejected")


def test_binding_roundtrip_keeps_profiles_and_strength():
    selections = main._bindings_as_selections([{
        "key": "cast", "profile": None, "profiles": ["alice", "bob"],
        "optional": [], "optional_by_profile": {},
        "strength_model": 0.6, "strength_clip": 0.55,
    }])
    assert selections == [{
        "key": "cast", "profile": None, "profiles": ["alice", "bob"],
        "mode": "explicit", "optional": [],
        "strength_model": 0.6, "strength_clip": 0.55,
    }], selections


def main_test():
    tests = [
        test_same_file_multi_profile_is_one_binding_and_one_loader_entry,
        test_character_limit_counts_profiles_not_files,
        test_legacy_registry_flag_does_not_block_same_file_multi_profile,
        test_style_stack_has_no_product_cap_and_keeps_per_asset_strength,
        test_same_physical_file_cannot_hide_conflicting_strengths,
        test_binding_roundtrip_keeps_profiles_and_strength,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main_test()
