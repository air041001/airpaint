# -*- coding: utf-8 -*-
"""零依赖 Prompt Engine 纯函数回归检查。"""
import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import main


def test_character_match():
    tags, remaining = main.match_characters("甘雨")
    assert tags == ["ganyu_(genshin_impact)"], tags
    assert remaining == "", repr(remaining)


def test_bare_character_fast_path():
    prompt_en, breakdown, prompt_ir = asyncio.run(main.translate("甘雨"))
    assert prompt_en == "1girl, solo, ganyu_(genshin_impact)", prompt_en
    assert breakdown is None, breakdown
    assert prompt_ir is None, prompt_ir


def test_ir_protocol_and_breakdown_derivation():
    output = (
        'IR: {"subject":["1girl"],"appearance":["pink hair"],"clothing":[],"action":["standing"],'
        '"pose":[],"interaction":[],"scene":["beach"],"composition":["full body"],'
        '"lighting":["bright sunlight"],"mood":["carefree"],"style":["anime style"],"constraints":[]}\n'
        "TAGS: 1girl, pink hair, beach\n"
        "NL:\n"
    )
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags == "1girl, pink hair, beach", tags
    assert breakdown == {
        "scene": "beach",
        "composition": "full body",
        "mood": "carefree",
        "lighting": "bright sunlight",
        "style": "anime style",
    }, breakdown
    assert nl == "", nl
    assert prompt_ir and prompt_ir["subject"] == ["1girl"], prompt_ir


def test_legacy_protocol_fallback():
    output = "scene: bedroom\ncomposition: close-up\nTAGS: 1girl, bedroom\nNL:\n"
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags == "1girl, bedroom", tags
    assert breakdown == {"scene": "bedroom", "composition": "close-up"}, breakdown
    assert nl == "", nl
    assert prompt_ir is None, prompt_ir


def test_tag_order_and_deduplication():
    result = main.normalize_tag_order(
        ["ganyu_(genshin_impact)"],
        ["blue eyes", "1girl", "solo", "blue eyes"],
    )
    assert result == "1girl, solo, ganyu_(genshin_impact), blue eyes", result


def test_character_bare_name_is_removed():
    result = main._strip_char_bare_names(
        ["ganyu", "ganyu_(genshin_impact)", "blue eyes"],
        ["ganyu_(genshin_impact)"],
    )
    assert result == ["ganyu_(genshin_impact)", "blue eyes"], result


def test_compile_prompt_merges_tags_and_nl():
    result = main.compile_prompt(
        ["ganyu_(genshin_impact)"],
        ["ganyu", "1girl", "solo", "blue eyes", "blue eyes"],
        "She stands by the window.",
    )
    assert result == "1girl, solo, ganyu_(genshin_impact), blue eyes. She stands by the window.", result


def test_experiment_negative_override_is_opt_in():
    workflow = main.WORKFLOWS["anima"]
    previous = workflow.pop("negative_text_node", None)
    workflow["negative_text_node"] = "4"
    try:
        payload = main.build_prompt("anima", "1girl, sitting", 832, 1216,
                                    negative_text="standing, walking")
        negative = payload["prompt"]["4"]["inputs"]
        assert negative["wildcard_text"].endswith("standing, walking"), negative
        assert negative["populated_text"].endswith("standing, walking"), negative
    finally:
        if previous is None:
            workflow.pop("negative_text_node", None)
        else:
            workflow["negative_text_node"] = previous


def test_render_profile_inference():
    simple = {"subject": ["1girl"], "action": ["standing"], "pose": [], "interaction": []}
    nsfw_simple = {"subject": ["1girl"], "appearance": ["adult", "nude"],
                   "action": ["standing"], "pose": ["legs apart"], "interaction": []}
    complex_pose = {"subject": ["1girl"], "action": ["running", "looking back"],
                    "pose": ["one leg raised"], "interaction": []}
    two_people = {"subject": ["2boys"], "action": ["fighting"], "pose": [],
                  "interaction": ["facing each other"]}
    assert main.infer_render_profile(simple) == "relation_hybrid"
    assert main.infer_render_profile(nsfw_simple) == "tag_first"
    assert main.infer_render_profile(complex_pose) == "relation_hybrid"
    assert main.infer_render_profile(two_people) == "relation_hybrid"


def test_tag_first_profile_drops_nl():
    result = main.compile_prompt([], ["1girl", "coffee"], "She holds one cup.", "tag_first")
    assert result == "1girl, coffee", result
    result = main.compile_prompt([], ["2boys", "sword"], "They face each other.", "relation_hybrid")
    assert result.endswith(". They face each other."), result


def main_test():
    tests = [
        test_character_match,
        test_bare_character_fast_path,
        test_ir_protocol_and_breakdown_derivation,
        test_legacy_protocol_fallback,
        test_tag_order_and_deduplication,
        test_character_bare_name_is_removed,
        test_compile_prompt_merges_tags_and_nl,
        test_experiment_negative_override_is_opt_in,
        test_render_profile_inference,
        test_tag_first_profile_drops_nl,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} prompt unit tests passed")


if __name__ == "__main__":
    main_test()
