"""P2B：用法资料消费的生产函数/接口集成测试（mock 模型，临时 state/Registry）。"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AIRPAINT_STATE_DIR", tempfile.mkdtemp(prefix="airpaint-test-state-"))

from server import lora as lora_module
from server import prompt_engine as prompt
from server.lora_usage import body_hash as _body_hash, version_id as _version_id
from server.prompt_engine import (
    _collect_usage_records,
    _parse_usage_object,
    _validate_usage_object,
    finalize_generation_text,
)

IR = {field: [] for field in
      ("subject", "appearance", "clothing", "action", "pose", "interaction",
       "scene", "composition", "lighting", "mood", "style", "constraints")}


def make_record(asset_key="demo", profile_id="", body="作者模板：{character}\nno watermark", **kw):
    record = {
        "asset_key": asset_key, "profile_id": profile_id, "body": body,
        "source_kind": kw.get("source_kind", "author"), "source_url": "",
        "background": kw.get("background", {"checkpoint": "demo"}),
        "candidate": kw.get("candidate", {"template": "{character}", "negative": ["blurry"]}),
        "advisory": kw.get("advisory", {}), "verified": kw.get("verified", "unverified"),
    }
    record["body_hash"] = _body_hash(body)
    record["usage_id"] = _version_id(record)
    return record


def asset_with_refs(refs, key="demo"):
    return {"key": key, "type": "style", "name": key, "file": key + ".safetensors",
            "trigger_policy": "none", "profiles": {}, "provides": [],
            "strength_model": 1.0, "strength_clip": 1.0, "configured": True,
            "registry_revision": "rev-1", "usage": {"refs": refs}}


def use_registry(registry):
    previous = lora_module.get_lora_registry
    lora_module.get_lora_registry = lambda: registry
    return previous


class UsageObjectSchemaTests(unittest.TestCase):
    def test_bad_schema_never_claims_success(self):
        for bad in ("not json", "[]", '{"applied": "x"}', '{"applied": [1]}',
                    '{"applied": [], "negative": 5}', '{"applied": [], "evidence": 5}'):
            obj, warnings = _parse_usage_object("USAGE: " + bad, True)
            self.assertIsNone(obj, bad)
            self.assertTrue(warnings, bad)

    def test_absent_line_is_not_success_and_disabled_by_default(self):
        self.assertEqual(_parse_usage_object("USAGE: []", False), (None, []))
        obj, warnings = _parse_usage_object("no usage here", True)
        self.assertIsNone(obj)
        self.assertTrue(warnings)

    def test_valid_object_parsed(self):
        line = 'USAGE: {"applied": ["a"], "negative": "", "evidence": "依据", "limits": "限制"}'
        obj, warnings = _parse_usage_object(line, True)
        self.assertEqual(warnings, [])
        self.assertEqual(obj["applied"], ["a"])
        self.assertEqual(obj["negative"], "")
        self.assertEqual(obj["evidence"], "依据")

    def test_unknown_ids_and_unbacked_negative_do_not_apply(self):
        provided = [{"id": "known"}]
        applied, negative, warnings = _validate_usage_object(
            {"applied": ["ghost"], "negative": "NEG"}, provided)
        self.assertEqual(applied, [])
        self.assertTrue(warnings)
        self.assertTrue(negative is prompt.USAGE_NEGATIVE_UNSET)

    def test_negative_three_states(self):
        provided = [{"id": "known"}]
        self.assertIs(_validate_usage_object({"applied": ["known"], "negative": None}, provided)[1],
                      prompt.USAGE_NEGATIVE_UNSET)
        self.assertEqual(_validate_usage_object({"applied": ["known"], "negative": ""}, provided)[1], "")
        self.assertEqual(_validate_usage_object({"applied": ["known"], "negative": "N"}, provided)[1], "N")


class FinalizeNegativeTests(unittest.TestCase):
    def test_baseline_kept_exactly_without_usage(self):
        result = finalize_generation_text(
            prompt_mode="assisted", prompt_state="final", prompt_en="1girl",
            default_negative="worst quality, lowres")
        self.assertEqual(result["final_negative"], "worst quality, lowres")
        self.assertEqual(result["negative_source"], "workflow_default")

    def test_usage_sentinel_keeps_baseline_and_string_replaces(self):
        base = dict(prompt_mode="assisted", prompt_state="final", prompt_en="1girl",
                    default_negative="worst quality, lowres")
        kept = finalize_generation_text(**base, usage_negative=None)
        self.assertEqual(kept["final_negative"], "worst quality, lowres")
        cleared = finalize_generation_text(**base, usage_negative="")
        self.assertEqual(cleared["final_negative"], "")
        self.assertEqual(cleared["negative_source"], "usage_cleared")
        replaced = finalize_generation_text(**base, usage_negative="only this")
        self.assertEqual(replaced["final_negative"], "only this")
        self.assertEqual(replaced["negative_source"], "usage_replaced")

    def test_user_value_wins_over_usage(self):
        result = finalize_generation_text(
            prompt_mode="assisted", prompt_state="final", prompt_en="1girl",
            default_negative="worst quality", negative_prompt="user negative",
            usage_negative="usage negative")
        self.assertEqual(result["final_negative"], "user negative")
        self.assertEqual(result["negative_source"], "user")

    def test_manual_never_adds_prefix_or_negative(self):
        result = finalize_generation_text(
            prompt_mode="manual", prompt_state="final", prompt_en="a lone robot",
            default_negative="worst quality")
        self.assertEqual(result["final_prompt_en"], "a lone robot")
        self.assertEqual(result["final_negative"], "worst quality")


class CollectBudgetTests(unittest.TestCase):
    def tearDown(self):
        lora_module.get_lora_registry = self.previous

    def _collect(self, records, profiles=("white",), selections=None):
        self.previous = use_registry({"demo": asset_with_refs([r["usage_id"] for r in records])})
        fetch = {r["usage_id"]: r for r in records}.get
        return _collect_usage_records(
            selections or [{"key": "demo", "profiles": list(profiles)}], fetch)

    def test_oversize_single_body_rejected_not_truncated(self):
        big = make_record(body="x" * (prompt.MAX_USAGE_BODY_CHARS + 1))
        records, provided, rejected, warnings, _ = self._collect([big])
        self.assertEqual(records, [])
        self.assertEqual(len(rejected), 1)
        self.assertTrue(any("单条预算" in w for w in warnings))

    def test_total_budget_rejects_later_records(self):
        records_in = [make_record(body=("y" * 19000) + str(index), candidate={})
                      for index in range(4)]
        records, provided, rejected, warnings, _ = self._collect(records_in)
        self.assertTrue(rejected)
        self.assertTrue(any("总上下文预算" in w for w in warnings))

    def test_broken_body_hash_rejected(self):
        record = make_record()
        record["body"] = record["body"] + " tampered"
        records, provided, rejected, warnings, _ = self._collect([record])
        self.assertEqual(records, [])
        self.assertTrue(warnings)

    def test_profile_scope_excluded_when_not_selected(self):
        record = make_record(profile_id="sailor")
        records, provided, rejected, warnings, _ = self._collect([record], profiles=("white",))
        self.assertEqual(records, [])
        self.assertTrue(any("未选 Profile" in w for w in warnings))


class TranslateUsageTests(unittest.TestCase):
    def setUp(self):
        self.previous = use_registry({})
        self.saved = prompt.siliconflow_translate
        prompt._TRANSLATE_CACHE.clear()
        self.captured = {}
        self.cfg = unittest.mock.patch.dict(
            prompt.CFG, {"translate": "siliconflow", "siliconflow_api_key": "test-key"},
            clear=False)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        prompt.siliconflow_translate = self.saved
        lora_module.get_lora_registry = self.previous
        prompt._TRANSLATE_CACHE.clear()

    def _fake(self, usage_object=None):
        async def fake(context, reroll=False, prior_state=None, usage_expected=False):
            self.captured["usage_expected"] = usage_expected
            lines = ["CONCEPT: 用户锁定：少女｜模型补全：坐姿",
                     "IR: " + json.dumps(IR, ensure_ascii=False),
                     "CHAR: none"]
            if usage_object is not None:
                lines.append("USAGE: " + json.dumps(usage_object, ensure_ascii=False))
            lines.append("PROMPT: 1girl, sitting")
            return ("1girl, sitting", None, "", dict(IR), [], {}, "用户锁定：少女｜模型补全：坐姿",
                    False, [], usage_object)
        return fake

    def _translate(self, selections):
        return asyncio.run(prompt.translate("少女坐着", lora_selections=selections,
                                            include_meta=True))

    def test_ordinary_path_makes_no_usage_and_flags_expected_false(self):
        lora_module.get_lora_registry = lambda: {"plain": {
            "key": "plain", "type": "style", "name": "plain", "file": "plain.safetensors",
            "trigger_policy": "none", "profiles": {}, "provides": [], "strength_model": 1.0,
            "strength_clip": 1.0, "configured": True, "registry_revision": "rev-1"}}
        prompt.siliconflow_translate = self._fake(None)
        _, _, _, meta = self._translate([{"key": "plain", "mode": "explicit"}])
        self.assertFalse(self.captured["usage_expected"])
        self.assertEqual(meta["usage_provided"], [])
        self.assertIsNone(meta["usage_negative"])

    def test_usage_object_applies_ids_and_negative(self):
        record = make_record()
        lora_module.get_lora_registry = lambda: {"demo": asset_with_refs([record["usage_id"]])}
        prompt.siliconflow_translate = self._fake(
            {"applied": [record["usage_id"]], "negative": "NEG-NEW",
             "evidence": "按作者模板", "limits": "单幅"})
        with unittest.mock.patch.object(prompt, "_default_usage_fetch",
                                        lambda uid: record if uid == record["usage_id"] else None):
            _, _, _, meta = self._translate([{"key": "demo", "mode": "explicit"}])
        self.assertTrue(self.captured["usage_expected"])
        self.assertEqual(meta["usage_applied"], [record["usage_id"]])
        self.assertEqual(meta["usage_negative"], "NEG-NEW")
        self.assertEqual(meta["usage_evidence_model"], "按作者模板")

    def test_unknown_declared_id_does_not_apply_negative(self):
        record = make_record()
        lora_module.get_lora_registry = lambda: {"demo": asset_with_refs([record["usage_id"]])}
        prompt.siliconflow_translate = self._fake(
            {"applied": ["ghost"], "negative": "NEG", "evidence": "", "limits": ""})
        with unittest.mock.patch.object(prompt, "_default_usage_fetch",
                                        lambda uid: record if uid == record["usage_id"] else None):
            _, _, _, meta = self._translate([{"key": "demo", "mode": "explicit"}])
        self.assertEqual(meta["usage_applied"], [])
        self.assertIsNone(meta["usage_negative"])
        self.assertTrue(meta["usage_warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
