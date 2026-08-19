# -*- coding: utf-8 -*-
"""零依赖 Prompt Engine 纯函数回归检查。"""
import asyncio
import sys
import tempfile
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


def test_prompt_ir_meta_is_additive():
    result = asyncio.run(main.translate("甘雨", include_meta=True))
    assert len(result) == 4, result
    prompt_en, breakdown, prompt_ir, meta = result
    assert prompt_en == "1girl, solo, ganyu_(genshin_impact)", prompt_en
    assert breakdown is None and prompt_ir is None
    assert meta["mode"] == "canonical", meta
    assert meta["expansion_applied"] is False, meta


def test_reroll_uses_new_painter_plan_metadata():
    meta = main._prompt_ir_meta("painter_expansion", reroll=True)
    assert meta["expansion_applied"] is True, meta
    assert meta["reroll_strategy"] == "new_painter_plan", meta


def test_painter_prompt_protocol_is_final_prompt():
    output = (
        'IR: {"subject":["1girl"],"appearance":["pink hair"],"clothing":[],"action":["standing"],'
        '"pose":[],"interaction":[],"scene":["cherry blossom tree"],"composition":["medium shot"],'
        '"lighting":["soft afternoon light"],"mood":["gentle"],"style":["anime style"],"constraints":[]}\n'
        "PROMPT: 1girl, pink hair, standing under cherry blossom tree, medium shot, soft afternoon light, anime style\n"
    )
    tags, breakdown, nl, prompt_ir = main._parse_structured_output(output)
    assert tags.startswith("1girl, pink hair"), tags
    assert nl == "", nl
    assert breakdown["scene"] == "cherry blossom tree", breakdown
    assert prompt_ir and prompt_ir["subject"] == ["1girl"], prompt_ir


def test_painter_tag_guard_preserves_woman_and_suppresses_unrequested_silhouette():
    tags = main._prepare_painter_tags(
        ["woman", "silhouette of a woman", "bedroom"],
        {"subject": ["woman"]},
        "女性裸体躺在卧室的床上",
        [],
    )
    assert tags[0] == "1girl", tags
    assert all("silhouette" not in tag for tag in tags), tags


def test_painter_tag_guard_keeps_nsfw_body_framing_and_default_anime_style():
    tags = main._prepare_painter_tags(
        ["woman", "nude", "close-up", "painterly", "soft lighting"],
        {"subject": ["woman"], "appearance": ["nude"]},
        "女性裸体躺在卧室的床上",
        [],
    )
    assert "1girl" in tags, tags
    assert "close-up" not in tags and "painterly" not in tags, tags
    assert "three-quarter view" in tags, tags


def test_painter_tag_guard_preserves_explicit_safety_marker():
    tags = main._prepare_painter_tags(
        ["1girl", "black lace lingerie", "seductive"],
        {"subject": ["1girl"], "constraints": ["nsfw"]},
        "成年女性裸体穿着黑色蕾丝内衣",
        [],
    )
    assert "nude" in tags, tags


def test_character_hint_protocol_parser():
    hints = main._parse_character_hints(
        "CHAR: 帕姆 => pom_pom_(honkai:_star_rail); 未知角色 => unknown_tag"
    )
    assert hints == [
        {"name": "帕姆", "candidate_tag": "pom_pom_(honkai:_star_rail)"},
        {"name": "未知角色", "candidate_tag": "unknown_tag"},
    ], hints


def test_character_hint_ir_fallback():
    hints = main._infer_character_hints_from_ir(
        {"subject": ["hakurei_reimu_(touhou)"], "appearance": []},
        ["博丽灵梦"],
        set(),
    )
    assert hints == [{"name": "博丽灵梦", "candidate_tag": "hakurei_reimu_(touhou)"}], hints
    assert main._infer_character_hints_from_ir(
        {"subject": ["ganyu_(genshin_impact)"]},
        ["站在望舒客栈的阳台上"],
        set(),
        ["ganyu_(genshin_impact)"],
    ) == []
    assert main._infer_character_hints_from_ir(
        {"subject": ["misaka_mikoto"]},
        ["御坂美琴"],
        set(),
    ) == [{"name": "御坂美琴", "candidate_tag": "misaka_mikoto"}]
    assert main._infer_character_hints_from_ir(
        {"subject": ["yukinoshita yukino"]},
        ["雪之下雪乃"],
        set(),
    ) == [{"name": "雪之下雪乃", "candidate_tag": "yukinoshita_yukino"}]


def test_character_bare_name_space_variant_is_removed():
    result = main._strip_char_bare_names(
        ["ai hoshino", "ai_hoshino", "stage"],
        ["ai_hoshino_(oshi_no_ko)"],
    )
    assert result == ["stage"], result
    result = main._strip_char_bare_names(
        ["yukinoshita yukino", "long black hair"],
        ["yukinoshita_yukino"],
    )
    assert result == ["long black hair"], result


def test_danbooru_character_classification():
    likely_supported = main._classify_danbooru_rows(
        [{"name": "known_char", "category": 4, "post_count": 100,
          "is_deprecated": False}],
        "known_char",
    )
    weak = main._classify_danbooru_rows(
        [{"name": "rare_char", "category": 4, "post_count": 2,
          "is_deprecated": False}],
        "rare_char",
    )
    artist = main._classify_danbooru_rows(
        [{"name": "artist_name", "category": 1, "post_count": 10000,
          "is_deprecated": False}],
        "artist_name",
    )
    assert likely_supported["status"] == "likely_supported", likely_supported
    assert weak["status"] == "weak", weak
    assert artist["status"] == "absent", artist


def test_auto_character_cache_write_and_match():
    with tempfile.TemporaryDirectory() as temp:
        old_dir, old_path, old_auto = main.KNOWLEDGE_CACHE_DIR, main.CHAR_AUTO_PATH, main.CHAR_AUTO
        try:
            cache_dir = Path(temp)
            main.KNOWLEDGE_CACHE_DIR = cache_dir
            main.CHAR_AUTO_PATH = cache_dir / "characters_auto.yaml"
            main.CHAR_AUTO = main.HotDict(main.CHAR_AUTO_PATH, key_fn=lambda s: s)
            main._record_auto_character("测试角色", "test_character_(series)")
            tags, remaining = main.match_characters("测试角色站在街上")
            assert tags == ["test_character_(series)"], tags
            assert remaining == "站在街上", repr(remaining)
        finally:
            main.KNOWLEDGE_CACHE_DIR, main.CHAR_AUTO_PATH, main.CHAR_AUTO = old_dir, old_path, old_auto


def test_unavailable_lookup_is_retryable():
    class FakeResponse:
        status_code = 503
        text = "temporary"

        def json(self):
            return {}

    calls = 0

    async def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    old_get = main.CLIENT.get
    old_cache, old_loaded = main._CHAR_LOOKUP_CACHE, main._CHAR_LOOKUP_CACHE_LOADED
    try:
        main.CLIENT.get = fake_get
        main._CHAR_LOOKUP_CACHE = {}
        main._CHAR_LOOKUP_CACHE_LOADED = True
        first = asyncio.run(main.lookup_character("临时角色", "temporary_character"))
        second = asyncio.run(main.lookup_character("临时角色", "temporary_character"))
        assert first["status"] == second["status"] == "unavailable"
        assert calls == 2, calls
        assert "临时角色|temporary_character" not in main._CHAR_LOOKUP_CACHE
    finally:
        main.CLIENT.get = old_get
        main._CHAR_LOOKUP_CACHE, main._CHAR_LOOKUP_CACHE_LOADED = old_cache, old_loaded


def test_unknown_character_fallback_on_unavailable():
    old_translate = main.siliconflow_translate
    old_lookup = main.lookup_character
    old_dict = main.match_dict_words
    old_cache = dict(main._TRANSLATE_CACHE)

    async def fake_translate(context, reroll=False):
        out = (
            'IR: {"subject":["yukinoshita yukino"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["classroom"],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
            "PROMPT: yukinoshita yukino, classroom\n"
        )
        return main._parse_structured_output(out) + (main._parse_character_hints(out),)

    async def fake_lookup(name, candidate):
        return {"name": name, "candidate_tag": candidate, "canonical_tag": "",
                "post_count": 0, "status": "unavailable", "source": "danbooru", "error": "mock down"}

    def fake_dict(text):
        return [], text

    main.siliconflow_translate = fake_translate
    main.lookup_character = fake_lookup
    main.match_dict_words = fake_dict
    main._TRANSLATE_CACHE = {}
    try:
        prompt, breakdown, ir, meta = asyncio.run(
            main.translate("雪之下雪乃坐在教室里", include_meta=True)
        )
        assert "yukinoshita_yukino" in prompt, prompt
        assert "yukinoshita yukino" not in prompt, prompt
        assert meta["character_lookup"][0]["status"] == "unavailable", meta
    finally:
        main.siliconflow_translate = old_translate
        main.lookup_character = old_lookup
        main.match_dict_words = old_dict
        main._TRANSLATE_CACHE = old_cache


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
        test_prompt_ir_meta_is_additive,
        test_reroll_uses_new_painter_plan_metadata,
        test_painter_prompt_protocol_is_final_prompt,
        test_painter_tag_guard_preserves_woman_and_suppresses_unrequested_silhouette,
        test_painter_tag_guard_keeps_nsfw_body_framing_and_default_anime_style,
        test_painter_tag_guard_preserves_explicit_safety_marker,
        test_character_hint_protocol_parser,
        test_character_hint_ir_fallback,
        test_character_bare_name_space_variant_is_removed,
        test_danbooru_character_classification,
        test_auto_character_cache_write_and_match,
        test_unavailable_lookup_is_retryable,
        test_unknown_character_fallback_on_unavailable,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} prompt unit tests passed")


if __name__ == "__main__":
    main_test()
