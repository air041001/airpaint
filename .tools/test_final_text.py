"""P1 最终正负文本控制的关键行为断言（不调用真实模型/GPU/ComfyUI）。

覆盖：最终化三态与状态区分、默认负面的真实文字来源、build_prompt 的
final 文本字面写入与旧路径兼容、幂等指纹含负面空串。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from server.api import _normalize_negative_prompt, _normalize_prompt_mode, _normalize_prompt_state, _request_fingerprint
from server.prompt_engine import finalize_generation_text
from server.settings import BASE, WORKFLOWS
from server.workflow_engine import build_prompt, default_negative_text

PREFIX = WORKFLOWS["anima"]["quality_prefix"]
PROMPT_NODE = WORKFLOWS["anima"]["prompt_node"]
NEGATIVE_NODE = WORKFLOWS["anima"]["negative_node"]
DEFAULT_NEG = "worst quality, lowres"


def _node_text(payload, node_id):
    return payload["prompt"][str(node_id)]["inputs"].get("text")


class FinalizeTests(unittest.TestCase):
    def test_assisted_body_adds_prefix_once_and_preview_equals_commit(self):
        preview = finalize_generation_text(
            prompt_mode="assisted", prompt_state="body", prompt_en="1girl, sitting",
            bindings=None, quality_prefix=PREFIX, default_negative=DEFAULT_NEG)
        self.assertEqual(preview["final_prompt_en"], PREFIX + "1girl, sitting")
        # 新前端提交已确认的 final：不再被二次加前缀 (P1 §8.A)
        committed = finalize_generation_text(
            prompt_mode="assisted", prompt_state="final",
            prompt_en=preview["final_prompt_en"], bindings=None, quality_prefix=PREFIX,
            default_negative=DEFAULT_NEG)
        self.assertEqual(committed["final_prompt_en"], preview["final_prompt_en"])
        self.assertEqual(committed["final_prompt_en"].count("masterpiece"), 1)

    def test_final_state_is_literal(self):
        result = finalize_generation_text(
            prompt_mode="assisted", prompt_state="final", prompt_en="1girl, beach",
            bindings=None, quality_prefix=PREFIX, default_negative=DEFAULT_NEG)
        self.assertEqual(result["final_prompt_en"], "1girl, beach")

    def test_manual_never_adds_prefix(self):
        result = finalize_generation_text(
            prompt_mode="manual", prompt_state="final", prompt_en="a lone robot",
            bindings=None, quality_prefix=PREFIX, default_negative=DEFAULT_NEG)
        self.assertEqual(result["final_prompt_en"], "a lone robot")
        self.assertEqual(result["prompt_mode"], "manual")

    def test_legacy_path_still_finalizes_prompt_but_keeps_wildcard_negative(self):
        result = finalize_generation_text(
            prompt_mode="assisted", prompt_state=None, prompt_en="1girl",
            bindings=None, quality_prefix=PREFIX)
        self.assertEqual(result["final_prompt_en"], PREFIX + "1girl")
        self.assertIsNone(result["final_negative"])
        self.assertEqual(result["negative_source"], "workflow_wildcard")

    def test_negative_three_states(self):
        default = "worst quality, lowres"
        # 缺省：新路径用默认真实文字
        self.assertEqual(
            finalize_generation_text(prompt_mode="assisted", prompt_state="final",
                                     prompt_en="x", default_negative=default,
                                     negative_prompt=None)["final_negative"], default)
        # 显式空串：确实清空
        cleared = finalize_generation_text(prompt_mode="assisted", prompt_state="final",
                                           prompt_en="x", default_negative=default,
                                           negative_prompt="")
        self.assertEqual(cleared["final_negative"], "")
        self.assertEqual(cleared["negative_source"], "user")
        # 非空：完整覆盖（不追加默认）
        override = finalize_generation_text(prompt_mode="assisted", prompt_state="final",
                                            prompt_en="x", default_negative=default,
                                            negative_prompt="blurry")
        self.assertEqual(override["final_negative"], "blurry")

    def test_dynamic_default_is_refused_on_new_path(self):
        with self.assertRaises(HTTPException):
            finalize_generation_text(prompt_mode="assisted", prompt_state="final",
                                     prompt_en="x", default_negative="a {b|c}",
                                     default_negative_dynamic=True, negative_prompt=None)

    def test_invalid_state_and_type_are_rejected(self):
        with self.assertRaises(HTTPException):
            finalize_generation_text(prompt_mode="assisted", prompt_state="weird", prompt_en="x")
        with self.assertRaises(HTTPException):
            finalize_generation_text(prompt_mode="assisted", prompt_state="final",
                                     prompt_en="x", default_negative="d", negative_prompt=5)

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(HTTPException):
            finalize_generation_text(prompt_mode="assisted", prompt_state="final", prompt_en="  ")


class NormalizerTests(unittest.TestCase):
    def test_prompt_mode_and_state(self):
        self.assertEqual(_normalize_prompt_mode(None), "assisted")
        self.assertEqual(_normalize_prompt_mode("MANUAL"), "manual")
        self.assertIsNone(_normalize_prompt_state(None))
        self.assertEqual(_normalize_prompt_state("final"), "final")
        with self.assertRaises(HTTPException):
            _normalize_prompt_mode("auto")
        with self.assertRaises(HTTPException):
            _normalize_prompt_state("done")

    def test_negative_prompt_type(self):
        self.assertIsNone(_normalize_negative_prompt(None))
        self.assertEqual(_normalize_negative_prompt(""), "")
        with self.assertRaises(HTTPException):
            _normalize_negative_prompt(1)


class DefaultNegativeTests(unittest.TestCase):
    def test_matches_workflow_wildcard_text_not_populated(self):
        text, static = default_negative_text("anima")
        self.assertTrue(static)
        wf = json.loads((BASE / WORKFLOWS["anima"]["file"]).read_text(encoding="utf-8"))
        raw = wf[str(NEGATIVE_NODE)]["inputs"]["text"]
        upstream = wf[str(raw[0])]["inputs"]
        self.assertEqual(text, upstream["wildcard_text"].strip().strip(",").strip())
        self.assertNotEqual(upstream["wildcard_text"], upstream["populated_text"])


class BuildPromptFinalTextTests(unittest.TestCase):
    def test_final_text_is_literal_and_negative_is_written(self):
        payload = build_prompt("anima", "ignored-body", 832, 1216,
                               final_prompt_en="X, 1girl, sitting", final_negative="")
        self.assertEqual(_node_text(payload, PROMPT_NODE), "X, 1girl, sitting")
        self.assertEqual(payload["prompt"][str(NEGATIVE_NODE)]["inputs"]["text"], "")

    def test_final_negative_is_written_verbatim(self):
        payload = build_prompt("anima", "ignored", 832, 1216,
                               final_prompt_en="1girl", final_negative="blurry, watermark")
        self.assertEqual(payload["prompt"][str(NEGATIVE_NODE)]["inputs"]["text"],
                         "blurry, watermark")

    def test_legacy_path_adds_prefix_and_keeps_wildcard_negative(self):
        payload = build_prompt("anima", "1girl, sitting", 832, 1216)
        self.assertEqual(_node_text(payload, PROMPT_NODE), PREFIX + "1girl, sitting")
        # 旧路径不覆盖负面节点：其 text 仍是上游连接 (P1 §8.B)
        self.assertIsInstance(payload["prompt"][str(NEGATIVE_NODE)]["inputs"]["text"], list)


class FingerprintTests(unittest.TestCase):
    def test_empty_negative_and_mode_change_the_fingerprint(self):
        base = {"workflow": "anima", "prompt_en": "1girl", "prompt_mode": "assisted",
                "prompt_state": "final"}
        with_clear = dict(base, negative_prompt="")
        with_value = dict(base, negative_prompt="blurry")
        manual = dict(base, prompt_mode="manual")
        self.assertNotEqual(_request_fingerprint(base), _request_fingerprint(with_clear))
        self.assertNotEqual(_request_fingerprint(with_clear), _request_fingerprint(with_value))
        self.assertNotEqual(_request_fingerprint(base), _request_fingerprint(manual))


if __name__ == "__main__":
    unittest.main(verbosity=2)
