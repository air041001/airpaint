# -*- coding: utf-8 -*-
"""LoRA onboarding Agent 的确定性测试；不调用外部模型。"""
import contextlib
import importlib.util
import io
from pathlib import Path


TOOL_PATH = Path(__file__).with_name("register_lora.py")
SPEC = importlib.util.spec_from_file_location("register_lora_tool", TOOL_PATH)
tool = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(tool)


def test_json_fence_parser():
    parsed = tool._extract_json_object('```json\n{"asset_id":"demo","asset":{}}\n```')
    assert parsed["asset_id"] == "demo"


def test_remi_profiles_are_preserved_and_truth_fields_are_forced():
    candidate = {
        "asset_id": "Remielle Dan",
        "asset": {
            "name": "Remielle Dan",
            "type": "character",
            "file": "invented.safetensors",
            "trigger_policy": "profile",
            "default_strength": {"model": 0.7, "clip": 0.7},
            "selection": {"default_profile": "base"},
            "profiles": {
                "base": {
                    "name": "Remielle Dan",
                    "aliases": ["Remielle Dan"],
                    "provides": ["Remielle Dan character identity"],
                    "required_tags": ["remielle_dan"],
                    "default_tags": [],
                    "optional_tags": {},
                },
                "white": {
                    "name": "Remielle Dan（白）",
                    "aliases": ["白 Remielle"],
                    "provides": ["white form identity", "white halter top"],
                    "required_tags": [r"remielle_dan \(white\)"],
                    "default_tags": ["a white halter top with tassels", "white headband, stars hair ornament"],
                    "optional_tags": {},
                    "source": "model invented",
                    "verified": "verified",
                },
                "black": {
                    "name": "Remielle Dan（黑）",
                    "aliases": ["黑 Remielle"],
                    "provides": ["black form identity", "dark high-collared top"],
                    "required_tags": [r"remielle_dan \(black\)"],
                    "default_tags": ["star hair ornament", "x_hair_ornament"],
                    "optional_tags": {},
                },
                "swim": {
                    "name": "Remielle Dan（泳装）",
                    "aliases": ["Remielle 泳装"],
                    "provides": ["swimsuit form identity", "shell headband"],
                    "required_tags": [r"remielle_dan \(swim\)"],
                    "default_tags": ["a white sleeveless top with purple ruffles", "shell headband"],
                    "optional_tags": {},
                },
            },
        },
        "evidence": {
            "strength_mode": "single", "strength_value": 0.7,
            "strength": "建议 0.7", "uncertainties": []},
    }
    asset_id, asset, evidence = tool.normalize_agent_candidate(candidate, "remi_lora-000003.safetensors")
    assert asset_id == "remielle_dan"
    assert asset["file"] == "remi_lora-000003.safetensors"
    assert asset["default_strength"] == {"model": 0.7, "clip": 0.7}
    assert set(asset["profiles"]) == {"base", "white", "black", "swim"}
    assert asset["selection"]["default_profile"] == "base"
    assert asset["profiles"]["white"]["default_tags"] == [
        "a white halter top with tassels", "white headband", "stars hair ornament"]
    assert asset["profiles"]["white"]["source"] == "user-provided author description"
    assert asset["profiles"]["white"]["verified"] == "candidate"
    assert evidence["strength"] == "建议 0.7"


def test_single_author_strength_overrides_mismatched_model_output():
    candidate = {
        "asset_id": "demo_style",
        "asset": {
            "name": "Demo Style",
            "type": "style",
            "trigger_policy": "none",
            "default_strength": {"model": 0.7, "clip": 1.0},
            "provides": ["Demo illustration style"],
        },
        "evidence": {
            "strength_mode": "single", "strength_value": 0.7,
            "strength": "搭配 LoRA 建议 0.7", "uncertainties": []},
    }
    _, asset, _ = tool.normalize_agent_candidate(candidate, "demo.safetensors")
    assert asset["default_strength"] == {"model": 0.7, "clip": 0.7}


def test_author_text_hard_fact_overrides_overcautious_agent():
    candidate = {
        "asset_id": "demo_style",
        "asset": {
            "name": "Demo Style", "type": "style", "trigger_policy": "none",
            "default_strength": {"model": 1.0, "clip": 1.0},
            "provides": ["Demo illustration style"],
        },
        "evidence": {"strength_mode": "default", "strength_value": 1.0, "strength": ""},
    }
    _, asset, evidence = tool.normalize_agent_candidate(candidate, "demo.safetensors")
    asset, evidence = tool.apply_author_hard_facts(
        asset, evidence, "搭配 LoRA 时建议 0.7，追求细节可以提升权重。")
    assert asset["default_strength"] == {"model": 0.7, "clip": 0.7}
    assert evidence["strength_mode"] == "single"


def test_strength_range_is_not_silently_collapsed():
    assert tool.extract_explicit_single_strength("推荐权重 0.7 到 0.9") is None
    assert tool.extract_explicit_single_strength("LoRA version 1.0") is None
    assert tool.extract_explicit_single_strength("UNET strength 0.7, CLIP 0.5") is None


def test_author_exact_trigger_escape_is_restored_and_generic_color_alias_is_removed():
    candidate = {
        "asset_id": "demo_character",
        "asset": {
            "name": "Demo Character", "type": "character", "trigger_policy": "profile",
            "default_strength": {"model": 1.0, "clip": 1.0},
            "selection": {"default_profile": "white"},
            "profiles": {
                "white": {
                    "name": "White", "aliases": ["white", "Demo white"],
                    "provides": ["Demo white form"],
                    "required_tags": ["demo_character (white)"],
                    "default_tags": [], "optional_tags": {},
                }
            },
        },
    }
    _, asset, evidence = tool.normalize_agent_candidate(candidate, "demo.safetensors")
    asset, evidence = tool.apply_author_hard_facts(
        asset, evidence, r"基础 demo_character；白：demo_character \(white\)")
    assert asset["profiles"]["white"]["required_tags"] == [r"demo_character \(white\)"]
    assert asset["profiles"]["white"]["aliases"] == ["Demo white"]
    assert evidence["exact_tags_restored"]


def test_no_trigger_style_cannot_keep_invented_trigger():
    candidate = {
        "asset_id": "light_style",
        "asset": {
            "name": "Light Style",
            "type": "style",
            "trigger_policy": "none",
            "default_strength": {"model": 1.0, "clip": 1.0},
            "required_tags": ["invented_trigger"],
            "provides": ["Light illustration style"],
        },
    }
    _, asset, _ = tool.normalize_agent_candidate(candidate, "light_lora-000006.safetensors")
    assert asset["required_tags"] == []
    assert asset["verified"] == "candidate"


def test_required_style_needs_exact_trigger():
    candidate = {
        "asset": {
            "name": "Dolphro-kun Style",
            "type": "style",
            "trigger_policy": "required",
            "default_strength": {"model": 1.0, "clip": 1.0},
            "required_tags": [],
            "provides": ["Dolphro-kun illustration style"],
        },
    }
    try:
        tool.normalize_agent_candidate(candidate, "dolphro-kun_v1_step400.safetensors")
    except ValueError as exc:
        assert "required_tags" in str(exc)
    else:
        raise AssertionError("required policy 不应接受空 trigger")


def test_civitai_url_branch_is_reachable():
    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "name": "Demo LoRA",
                "type": "LORA",
                "description": "<p>Demo description</p>",
                "modelVersions": [{"name": "v1", "baseModel": "Anima", "trainedWords": ["demo"]}],
            }

    old_get = tool.httpx.get
    try:
        tool.httpx.get = lambda *args, **kwargs: FakeResponse()
        with contextlib.redirect_stdout(io.StringIO()):
            result = tool.fetch_civitai_candidate("https://civitai.com/models/12345/demo")
    finally:
        tool.httpx.get = old_get
    assert result["name"] == "Demo LoRA"
    assert result["versions"][0]["trainedWords"] == ["demo"]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} lora onboarding agent tests passed")
