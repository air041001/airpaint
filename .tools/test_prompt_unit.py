# -*- coding: utf-8 -*-
"""零依赖 Prompt Engine 纯函数回归检查。"""
import asyncio
import json
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


def test_build_prompt_routes_txt2img_and_img2img_explicitly():
    txt2img = main.build_prompt("anima", "1girl, beach", 1024, 1536)["prompt"]
    assert txt2img["42"]["inputs"]["select"] == 1
    assert txt2img["56"]["inputs"]["width"] == 1024
    assert txt2img["56"]["inputs"]["height"] == 1536

    img2img = main.build_prompt(
        "anima", "1girl, beach", 1024, 1536,
        image_filename="uploaded.png", denoise=0.35,
    )["prompt"]
    assert img2img["42"]["inputs"]["select"] == 2
    assert img2img["0"]["inputs"]["image"] == "uploaded.png"
    assert img2img["6"]["inputs"]["denoise"] == 0.35


def test_build_prompt_keeps_rating_tags_manual():
    prompt_node = str(main.WORKFLOWS["anima"]["prompt_node"])
    plain = main.build_prompt("anima", "1girl, sitting", 832, 1216)["prompt"]
    plain_text = plain[prompt_node]["inputs"]["text"]
    assert "safe" not in plain_text and "explicit" not in plain_text, plain_text

    adult = main.build_prompt("anima", "1girl, nude", 832, 1216)["prompt"]
    adult_text = adult[prompt_node]["inputs"]["text"]
    assert "explicit" not in adult_text, adult_text

    manual = main.build_prompt("anima", "safe, 1girl, sitting", 832, 1216)["prompt"]
    manual_text = manual[prompt_node]["inputs"]["text"]
    assert manual_text.count("safe") == 1, manual_text


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


def test_painter_tag_guard_preserves_explicit_user_intent():
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


def test_lora_registry_preserves_nested_profiles():
    registry = main.get_lora_registry()
    denia = registry["denia"]
    assert denia["profiles"]["white"]["required_tags"] == ["denia \\(wuthering waves\\)"]
    assert denia["profiles"]["black"]["optional_tags"]["arm_tattoo"]["tags"] == ["arm tattoo"]
    assert denia["registry_revision"] == main.LORA_REGISTRY.snapshot()[1]
    deepseek = registry["deepseek_maid"]["profiles"]["maid"]
    assert deepseek["required_tags"] == ["deepseek_whale_girl", "deepseek_maid_outfit"]
    assert deepseek["default_tags"] == []
    assert "very long hair" in deepseek["optional_tags"]["identity_front"]["tags"]
    assert "black mary janes" in deepseek["optional_tags"]["maid_front_full"]["tags"]
    assert deepseek["verified"] == "candidate"
    assert registry["deepseek_maid"]["strength_model"] == 0.85


def test_bright_afternoon_uses_daylight_not_golden_hour():
    tags, remaining = main.match_dict_words("明亮午后光线")
    assert "bright afternoon" in tags, tags
    assert "high sun" in tags, tags
    assert "golden" not in tags and "golden hour" not in tags, tags
    assert remaining == "光线", remaining


def test_hot_lora_registry_keeps_last_good_snapshot():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "registry.yaml"
        path.write_text(
            "schema_version: 1\nloras:\n  test:\n    name: Test\n    type: style\n"
            "    file: test.safetensors\n    trigger_policy: none\n",
            encoding="utf-8",
        )
        hot = main.HotLoraRegistry(path)
        before, revision = hot.snapshot()
        assert before["loras"]["test"]["name"] == "Test"
        path.write_text("schema_version: [broken", encoding="utf-8")
        after, after_revision = hot.snapshot()
        assert after == before
        assert after_revision == revision


def test_lora_legacy_key_resolves_explicit_profile():
    bindings, warnings, revision = main.resolve_lora_selections(["denia_black"])
    assert not warnings, warnings
    assert revision == main.LORA_REGISTRY.snapshot()[1]
    assert bindings[0]["key"] == "denia"
    assert bindings[0]["profile"] == "black"
    assert bindings[0]["resolved_by"] == "explicit"
    assert bindings[0]["injected_tags"][0] == "blackdenia \\(wuthering waves\\)"


def test_lora_auto_profile_and_optional_ids_are_whitelisted():
    bindings, warnings, _ = main.resolve_lora_selections(
        [{"key": "denia", "mode": "auto"}],
        {"denia": {"profile": "white", "optional": ["white_dress", "made_up"]}},
    )
    assert bindings[0]["profile"] == "white"
    assert bindings[0]["optional"] == ["white_dress"]
    assert "white dress" in bindings[0]["injected_tags"]
    assert any("made_up" in warning for warning in warnings), warnings


def test_lora_explicit_profile_still_accepts_semantic_optional_choice():
    bindings, warnings, _ = main.resolve_lora_selections(
        [{"key": "denia", "profile": "white", "mode": "explicit"}],
        {"denia": {"profile": "black", "optional": ["white_dress"]}},
    )
    assert not warnings, warnings
    assert bindings[0]["profile"] == "white"
    assert bindings[0]["resolved_by"] == "explicit"
    assert bindings[0]["optional"] == ["white_dress"]
    assert "white dress" in bindings[0]["injected_tags"]


def test_lora_intent_alias_locks_profile_and_optional_ids():
    selections = main.apply_lora_intent_hints(
        "黑达妮娅露出手臂纹身", [{"key": "denia", "mode": "auto"}]
    )
    assert selections == [{
        "key": "denia", "profile": "black", "mode": "auto", "optional": ["arm_tattoo"]
    }], selections
    bindings, warnings, _ = main.resolve_lora_selections(selections)
    assert not warnings, warnings
    assert bindings[0]["resolved_by"] == "intent_alias"
    assert "arm tattoo" in bindings[0]["injected_tags"]


def test_deepseek_view_recipes_are_conditional():
    base, warnings, _ = main.resolve_lora_selections([
        {"key": "deepseek_maid", "profile": "maid", "mode": "explicit"}
    ])
    assert not warnings, warnings
    assert base[0]["injected_tags"] == ["deepseek_whale_girl", "deepseek_maid_outfit"]

    selections = main.apply_lora_intent_hints(
        "deepseek正面全身站立",
        [{"key": "deepseek_maid", "profile": "maid", "mode": "explicit"}],
    )
    assert selections[0]["optional"] == ["identity_front", "maid_front_full"], selections
    detailed, warnings, _ = main.resolve_lora_selections(selections)
    assert not warnings, warnings
    assert "very long hair" in detailed[0]["injected_tags"]
    assert "black mary janes" in detailed[0]["injected_tags"]


def test_lora_registry_revision_rejects_stale_binding():
    try:
        main.resolve_lora_selections(["denia_white"], expected_revision="stale-revision")
    except main.HTTPException as exc:
        assert exc.status_code == 409, exc
        assert "LoRA Registry" in str(exc.detail), exc.detail
    else:
        raise AssertionError("stale LoRA registry revision must be rejected")


def test_scan_force_skips_known_wan_without_hashing():
    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        wan = temp_dir / "wan_lightx2v_high_noise_model.safetensors"
        wan.write_bytes(b"not-a-real-model")
        old_dir = main.LORA_DIR
        old_cache_file = main.LORA_CACHE_FILE
        old_auto = main._lora_auto
        old_loaded = main._lora_auto_loaded
        old_read_sha = main._read_sha256

        def fail_if_hashed(_):
            raise AssertionError("known Wan file must be excluded before SHA256")

        try:
            main.LORA_DIR = temp_dir
            main.LORA_CACHE_FILE = temp_dir / "cache.json"
            main._lora_auto = {
                wan.stem: {
                    "baseModel": "Wan Video 14B",
                    "status": "excluded",
                    "fingerprint": "old",
                }
            }
            main._lora_auto_loaded = True
            main._read_sha256 = fail_if_hashed
            result = asyncio.run(main.scan_loras(force=True))
            assert result["excluded"] == 1, result
            assert main._lora_auto[wan.stem]["status"] == "excluded"
        finally:
            main.LORA_DIR = old_dir
            main.LORA_CACHE_FILE = old_cache_file
            main._lora_auto = old_auto
            main._lora_auto_loaded = old_loaded
            main._read_sha256 = old_read_sha


def test_scan_uses_local_civitai_info_before_hash_or_network():
    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        lora = temp_dir / "sidecar_character.safetensors"
        lora.write_bytes(b"not-a-real-model")
        (temp_dir / "sidecar_character.civitai.info").write_text(json.dumps({
            "name": "v1", "baseModel": "Illustrious",
            "trainedWords": ["sidecar_character, blue hair"],
            "model": {"name": "Sidecar Character", "tags": ["character", "anime"]},
            "files": [{"name": lora.name, "hashes": {"SHA256": "ABC123"}}],
        }), encoding="utf-8")
        old_dir = main.LORA_DIR
        old_cache_file = main.LORA_CACHE_FILE
        old_auto = main._lora_auto
        old_loaded = main._lora_auto_loaded
        old_read_sha = main._read_sha256
        old_lookup = main._civitai_lookup

        def fail_if_hashed(_):
            raise AssertionError("valid .civitai.info must avoid SHA256")

        async def fail_if_online(_):
            raise AssertionError("valid .civitai.info must avoid network lookup")

        try:
            main.LORA_DIR = temp_dir
            main.LORA_CACHE_FILE = temp_dir / "cache.json"
            main._lora_auto = {}
            main._lora_auto_loaded = True
            main._read_sha256 = fail_if_hashed
            main._civitai_lookup = fail_if_online
            result = asyncio.run(main.scan_loras(force=True))
            cached = main._lora_auto[lora.stem]
            assert result["new"] == 1, result
            assert cached["status"] == "resolved", cached
            assert cached["type"] == "character", cached
            assert cached["sha256"] == "abc123", cached
            assert cached["metadataSource"] == "civitai.info", cached
        finally:
            main.LORA_DIR = old_dir
            main.LORA_CACHE_FILE = old_cache_file
            main._lora_auto = old_auto
            main._lora_auto_loaded = old_loaded
            main._read_sha256 = old_read_sha
            main._civitai_lookup = old_lookup


def test_lora_binding_compiler_is_exact_and_idempotent():
    bindings, _, _ = main.resolve_lora_selections(["denia_white"])
    first = main.compile_lora_bindings("1girl, solo, denia wuthering waves, beach, sunset", bindings)
    second = main.compile_lora_bindings(first, bindings)
    assert first == second
    assert first.count("denia \\(wuthering waves\\)") == 1, first
    assert "denia wuthering waves" not in first, first
    assert first.startswith("1girl, solo, denia \\(wuthering waves\\)"), first


def test_lora_choice_protocol_parser():
    output = (
        'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":[],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
        'LORA: {"denia":{"profile":"white","optional":["white_dress"]}}\n'
        'PROMPT: 1girl, solo, beach\n'
    )
    choices = main._parse_lora_choices(output)
    assert choices == {"denia": {"profile": "white", "optional": ["white_dress"]}}, choices


def test_active_lora_forces_painter_and_compiles_binding():
    old_translate = main.siliconflow_translate
    old_cache = dict(main._TRANSLATE_CACHE)
    calls = []

    async def fake_translate(context, reroll=False):
        calls.append(context)
        out = (
            'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":["standing"],"pose":[],"interaction":[],"scene":["beach"],"composition":["full body"],"lighting":["sunset"],"mood":["calm"],"style":[],"constraints":[]}\n'
            'LORA: {"denia":{"profile":"white","optional":[]}}\n'
            'PROMPT: 1girl, solo, standing, beach, full body, sunset\n'
        )
        return main._parse_structured_output(out) + ([], main._parse_lora_choices(out))

    main.siliconflow_translate = fake_translate
    main._TRANSLATE_CACHE = {}
    try:
        prompt, _, _, meta = asyncio.run(main.translate(
            "站在海边", lora_selections=[{"key": "denia", "mode": "auto"}], include_meta=True
        ))
        assert calls and "ACTIVE LORA CONTEXT" in calls[0], calls
        assert "Select exactly one profile ID" in calls[0], calls[0]
        assert "denia \\(wuthering waves\\)" in prompt, prompt
        assert "beach" in prompt and "sunset" in prompt, prompt
        assert meta["lora_aware"] is True
        assert meta["lora_bindings"][0]["profile"] == "white"
    finally:
        main.siliconflow_translate = old_translate
        main._TRANSLATE_CACHE = old_cache


def test_active_lora_is_present_in_vision_path():
    old_vision = main.siliconflow_vision_translate
    calls = []

    async def fake_vision(image_b64, context, reroll=False, mode="reference"):
        calls.append((context, reroll, mode))
        return (
            "1girl, beach, sunset", {"scene": "beach", "lighting": "sunset"}, "", None,
            {"denia": {"profile": "white", "optional": []}},
        )

    main.siliconflow_vision_translate = fake_vision
    try:
        prompt, _, _, meta = asyncio.run(main.translate(
            "站在海边", image_b64="mock-image",
            lora_selections=[{"key": "denia", "mode": "auto"}], include_meta=True,
        ))
        assert calls and "ACTIVE LORA CONTEXT" in calls[0][0], calls
        assert "denia \\(wuthering waves\\)" in prompt, prompt
        assert meta["lora_bindings"][0]["profile"] == "white"
    finally:
        main.siliconflow_vision_translate = old_vision


def test_lora_cache_isolated_by_profile():
    old_translate = main.siliconflow_translate
    old_cache = dict(main._TRANSLATE_CACHE)
    calls = 0

    async def fake_translate(context, reroll=False):
        nonlocal calls
        calls += 1
        profile = "black" if "Locked profile: black" in context else "white"
        out = (
            'IR: {"subject":["1girl"],"appearance":[],"clothing":[],"action":[],"pose":[],"interaction":[],"scene":["street"],"composition":[],"lighting":[],"mood":[],"style":[],"constraints":[]}\n'
            f'LORA: {{"denia":{{"profile":"{profile}","optional":[]}}}}\n'
            'PROMPT: 1girl, solo, street\n'
        )
        return main._parse_structured_output(out) + ([], main._parse_lora_choices(out))

    main.siliconflow_translate = fake_translate
    main._TRANSLATE_CACHE = {}
    try:
        white = asyncio.run(main.translate("街道", lora_selections=["denia_white"]))[0]
        black = asyncio.run(main.translate("街道", lora_selections=["denia_black"]))[0]
        assert calls == 2, calls
        assert "denia \\(wuthering waves\\)" in white
        assert "blackdenia \\(wuthering waves\\)" in black
    finally:
        main.siliconflow_translate = old_translate
        main._TRANSLATE_CACHE = old_cache


def test_build_prompt_re_resolves_binding_and_deduplicates_trigger():
    bindings, _, revision = main.resolve_lora_selections(["denia_white"])
    payload = main.build_prompt(
        "anima", "1girl, solo, denia \\(wuthering waves\\), beach", 832, 1216,
        lora_bindings=bindings, registry_revision=revision,
    )
    prompt = payload["prompt"][str(main.WORKFLOWS["anima"]["prompt_node"])]["inputs"]["text"]
    loras = payload["prompt"][str(main.WORKFLOWS["anima"]["lora_node"])]["inputs"]["loras"]["__value__"]
    assert prompt.count("denia \\(wuthering waves\\)") == 1, prompt
    assert loras[0]["name"] == "denia_lorav4-000005.safetensors", loras


def test_enqueue_rebuilds_client_binding_from_registry_snapshot():
    old_jobs, old_queue, old_usage = main.JOBS, main.QUEUE, main.USAGE
    _, revision = main.LORA_REGISTRY.snapshot()
    try:
        main.JOBS = {}
        main.QUEUE = asyncio.Queue()
        main.USAGE = {"test-token": ["2099-01-01", 0]}
        job_id = asyncio.run(main._enqueue(
            "test-token", "anima", "1girl, beach", "测试", "832x1216",
            [], None, None,
            lora_bindings=[{
                "key": "denia", "profile": "white", "optional": [],
                "file": "evil.safetensors", "injected_tags": ["evil trigger"],
            }],
            registry_revision=revision,
        ))
        job = main.JOBS[job_id]
        assert "evil trigger" not in job["prompt_en"], job
        assert "denia \\(wuthering waves\\)" in job["prompt_en"], job
        assert job["lora_bindings"][0]["file"] == "denia_lorav4-000005.safetensors"
        assert asyncio.run(main.QUEUE.get()) == job_id
    finally:
        main.JOBS, main.QUEUE, main.USAGE = old_jobs, old_queue, old_usage


def test_dialog_start_carries_binding_snapshot_into_job():
    old_translate = main.translate
    old_jobs, old_sessions = main.JOBS, main.SESSIONS
    old_queue, old_usage = main.QUEUE, main.USAGE
    bindings, _, revision = main.resolve_lora_selections(["denia_white"])

    class FakeRequest:
        async def json(self):
            return {
                "action": "start", "prompt": "达妮娅在海边", "workflow": "anima",
                "size": "832x1216", "lora_selections": ["denia_white"],
            }

    async def fake_translate(*args, **kwargs):
        meta = {
            "lora_bindings": bindings, "lora_warnings": [],
            "registry_revision": revision,
        }
        return "1girl, denia \\(wuthering waves\\), beach", None, None, meta

    try:
        main.translate = fake_translate
        main.JOBS = {}
        main.SESSIONS = {}
        main.QUEUE = asyncio.Queue()
        main.USAGE = {"dialog-token": ["2099-01-01", 0]}
        response = asyncio.run(main.dialog_turn(FakeRequest(), token="dialog-token"))
        session = main.SESSIONS[response["session_id"]]
        job = main.JOBS[response["job_id"]]
        assert session["registry_revision"] == revision
        assert session["lora_bindings"] == bindings
        assert job["registry_revision"] == revision
        assert job["lora_bindings"][0]["profile"] == "white"
    finally:
        main.translate = old_translate
        main.JOBS, main.SESSIONS = old_jobs, old_sessions
        main.QUEUE, main.USAGE = old_queue, old_usage


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
        test_build_prompt_routes_txt2img_and_img2img_explicitly,
        test_build_prompt_keeps_rating_tags_manual,
        test_render_profile_inference,
        test_tag_first_profile_drops_nl,
        test_prompt_ir_meta_is_additive,
        test_reroll_uses_new_painter_plan_metadata,
        test_painter_prompt_protocol_is_final_prompt,
        test_painter_tag_guard_preserves_woman_and_suppresses_unrequested_silhouette,
        test_painter_tag_guard_keeps_nsfw_body_framing_and_default_anime_style,
        test_painter_tag_guard_preserves_explicit_user_intent,
        test_character_hint_protocol_parser,
        test_character_hint_ir_fallback,
        test_character_bare_name_space_variant_is_removed,
        test_danbooru_character_classification,
        test_auto_character_cache_write_and_match,
        test_unavailable_lookup_is_retryable,
        test_unknown_character_fallback_on_unavailable,
        test_lora_registry_preserves_nested_profiles,
        test_bright_afternoon_uses_daylight_not_golden_hour,
        test_hot_lora_registry_keeps_last_good_snapshot,
        test_lora_legacy_key_resolves_explicit_profile,
        test_lora_auto_profile_and_optional_ids_are_whitelisted,
        test_lora_explicit_profile_still_accepts_semantic_optional_choice,
        test_lora_intent_alias_locks_profile_and_optional_ids,
        test_deepseek_view_recipes_are_conditional,
        test_lora_registry_revision_rejects_stale_binding,
        test_scan_force_skips_known_wan_without_hashing,
        test_scan_uses_local_civitai_info_before_hash_or_network,
        test_lora_binding_compiler_is_exact_and_idempotent,
        test_lora_choice_protocol_parser,
        test_active_lora_forces_painter_and_compiles_binding,
        test_active_lora_is_present_in_vision_path,
        test_lora_cache_isolated_by_profile,
        test_build_prompt_re_resolves_binding_and_deduplicates_trigger,
        test_enqueue_rebuilds_client_binding_from_registry_snapshot,
        test_dialog_start_carries_binding_snapshot_into_job,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} prompt unit tests passed")


if __name__ == "__main__":
    main_test()
