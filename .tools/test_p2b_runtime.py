"""P2B 运行时：严格校验、system/user 协议载荷、API 集成、dialog 规划（不调用真实模型）。"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AIRPAINT_STATE_DIR", tempfile.mkdtemp(prefix="airpaint-test-state-"))

from server import api
from server import lora as lora_module
from server import prompt_engine as prompt
from server.lora_usage import body_hash as _body_hash, version_id as _version_id
from server.persistence import AirPaintStore
from server.settings import WORKFLOWS

OWNER = "owner-p2b"
PROMPT_NODE = str(WORKFLOWS["anima"]["prompt_node"])
NEG_NODE = str(WORKFLOWS["anima"]["negative_node"])
IR = {field: [] for field in
      ("subject", "appearance", "clothing", "action", "pose", "interaction",
       "scene", "composition", "lighting", "mood", "style", "constraints")}


def make_record(**kw):
    body = kw.get("body", "作者模板：{character}\nno watermark")
    record = {
        "asset_key": kw.get("asset_key", "demo"), "profile_id": kw.get("profile_id", ""),
        "body": body, "source_kind": "author", "source_url": "",
        "background": kw.get("background", {"checkpoint": "demo"}),
        "candidate": kw.get("candidate", {"template": "{character}", "negative": ["blurry"]}),
        "advisory": {}, "verified": "unverified",
    }
    record["body_hash"] = _body_hash(body)
    record["usage_id"] = _version_id(record)
    return record


def asset(key, refs=None):
    result = {"key": key, "type": "style", "name": key, "file": key + ".safetensors",
              "trigger_policy": "none", "profiles": {}, "provides": [],
              "strength_model": 1.0, "strength_clip": 1.0, "configured": True,
              "registry_revision": "rev-1"}
    if refs:
        result["usage"] = {"refs": refs}
    return result


class Request:
    def __init__(self, **body):
        self.body = body
        self.headers = {}

    async def json(self):
        return self.body


class StrictValidationTests(unittest.TestCase):
    def test_unknown_id_disables_whole_declaration(self):
        provided = [{"id": "known"}]
        for applied, negative in ((["ghost"], None), (["ghost"], ""), (["known", "ghost"], "N"),
                                  ([], ""), ([], "N")):
            applied_out, negative_out, warnings = prompt._validate_usage_object(
                {"applied": applied, "negative": negative}, provided)
            self.assertEqual(applied_out, [], (applied, negative))
            self.assertIs(negative_out, prompt.USAGE_NEGATIVE_UNSET, (applied, negative))

    def test_not_applied_never_clears_or_replaces_negative(self):
        provided = [{"id": "known"}]
        for negative in ("", "NEG"):
            _, negative_out, warnings = prompt._validate_usage_object(
                {"applied": [], "negative": negative}, provided)
            self.assertIs(negative_out, prompt.USAGE_NEGATIVE_UNSET)
            self.assertTrue(warnings)

    def test_valid_applied_allows_clear_and_replace(self):
        provided = [{"id": "known"}]
        self.assertEqual(
            prompt._validate_usage_object({"applied": ["known"], "negative": ""}, provided)[1], "")
        self.assertEqual(
            prompt._validate_usage_object({"applied": ["known"], "negative": "N"}, provided)[1], "N")
        self.assertIs(
            prompt._validate_usage_object({"applied": ["known"], "negative": None}, provided)[1],
            prompt.USAGE_NEGATIVE_UNSET)

    def test_duplicate_usage_lines_use_first_valid(self):
        out = ('USAGE: {"applied": ["known"], "negative": null}\n'
               'USAGE: {"applied": ["ghost"], "negative": "X"}')
        obj, warnings = prompt._parse_usage_object(out, True)
        self.assertEqual(obj["applied"], ["known"])
        self.assertEqual(warnings, [])


class ProtocolPayloadTests(unittest.TestCase):
    def setUp(self):
        self.saved_post = prompt.CLIENT.post
        self.previous = lora_module.get_lora_registry
        self.calls = []
        prompt._TRANSLATE_CACHE.clear()

    def tearDown(self):
        prompt.CLIENT.post = self.saved_post
        lora_module.get_lora_registry = self.previous
        prompt._TRANSLATE_CACHE.clear()

    def _fake_post(self, usage_line=None, lora_key="demo"):
        async def fake_post(url, headers=None, timeout=None, **kwargs):
            self.calls.append(kwargs.get("json"))
            lines = ["CONCEPT: 用户锁定：少女｜模型补全：坐姿",
                     "IR: " + json.dumps(IR, ensure_ascii=False), "CHAR: none",
                     "LORA: " + json.dumps({lora_key: {"profile": None, "optional": []}})]
            if usage_line:
                lines.append(usage_line)
            lines.append("PROMPT: 1girl, sitting")

            class Response:
                status_code = 200

                def json(self):
                    return {"choices": [{"message": {"content": "\n".join(lines)}}]}
            return Response()
        return fake_post

    def _run(self, selections, usage_line=None):
        prompt.CLIENT.post = self._fake_post(usage_line, selections[0]["key"])
        with patch.dict(prompt.CFG, {"translate": "siliconflow",
                                     "siliconflow_api_key": "test-key"}, clear=False):
            return asyncio.run(prompt.translate(
                "少女坐着", lora_selections=selections, include_meta=True))

    def test_usage_expected_extends_system_and_injects_context(self):
        record = make_record()
        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        line = json.dumps({"applied": [record["usage_id"]], "negative": "",
                           "evidence": "依据", "limits": "限制"}, ensure_ascii=False)
        with patch.object(prompt, "_default_usage_fetch",
                          lambda uid: record if uid == record["usage_id"] else None):
            result = self._run([{"key": "demo", "mode": "explicit"}], "USAGE: " + line)
        system = self.calls[0]["messages"][0]["content"]
        user = self.calls[0]["messages"][1]["content"]
        self.assertIn("USAGE PROTOCOL OVERRIDE", system)
        self.assertIn("USAGE CONTEXT", user)
        self.assertIn(record["body"], user)
        meta = result[3]
        self.assertEqual(meta["usage_applied"], [record["usage_id"]])
        self.assertEqual(meta["usage_negative"], "")
        self.assertEqual(meta["usage_evidence_model"], "依据")

    def test_ordinary_path_payload_unchanged(self):
        lora_module.get_lora_registry = lambda: {"plain": asset("plain")}
        result = self._run([{"key": "plain", "mode": "explicit"}])
        system = self.calls[0]["messages"][0]["content"]
        user = self.calls[0]["messages"][1]["content"]
        self.assertNotIn("USAGE PROTOCOL OVERRIDE", system)
        self.assertNotIn("USAGE CONTEXT", user)
        self.assertEqual(result[3]["usage_applied"], [])
        self.assertIsNone(result[3]["usage_negative"])

    def test_bad_schema_warnings_reach_meta(self):
        record = make_record()
        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        with patch.object(prompt, "_default_usage_fetch",
                          lambda uid: record if uid == record["usage_id"] else None):
            result = self._run([{"key": "demo", "mode": "explicit"}], 'USAGE: {"applied": "bad"}')
        meta = result[3]
        self.assertEqual(meta["usage_applied"], [])
        self.assertIsNone(meta["usage_negative"])
        self.assertTrue(any("USAGE" in warning for warning in meta["usage_warnings"]))

    def test_ordinary_path_has_no_usage_warnings(self):
        lora_module.get_lora_registry = lambda: {"plain": asset("plain")}
        result = self._run([{"key": "plain", "mode": "explicit"}])
        self.assertEqual(result[3]["usage_warnings"], [])


class BudgetTailTests(unittest.TestCase):
    def test_10203_char_body_with_tail_marker_is_fully_provided(self):
        body = "字" * 10203 + "ENDMARK"
        record = make_record(body=body)
        previous = lora_module.get_lora_registry
        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        try:
            records, provided, rejected, warnings, _ = prompt._collect_usage_records(
                [{"key": "demo", "profiles": []}],
                lambda uid: record if uid == record["usage_id"] else None)
        finally:
            lora_module.get_lora_registry = previous
        self.assertEqual(len(records), 1)
        self.assertEqual(rejected, [])
        context = prompt._format_usage_context(records, rejected=[], baseline_negative="")
        self.assertIn(body, context)
        self.assertIn("ENDMARK", context)

    def test_total_budget_rejects_whole_records(self):
        records_in = [make_record(body=("y" * 19000) + str(index)) for index in range(4)]
        previous = lora_module.get_lora_registry
        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [r["usage_id"] for r in records_in])}
        try:
            records, provided, rejected, warnings, _ = prompt._collect_usage_records(
                [{"key": "demo", "profiles": []}],
                {r["usage_id"]: r for r in records_in}.get)
        finally:
            lora_module.get_lora_registry = previous
        self.assertTrue(rejected)
        self.assertTrue(any("总上下文预算" in warning for warning in warnings))


class DialogRefsTests(unittest.TestCase):
    def test_start_uses_planned_refs(self):
        self.assertEqual(api._dialog_usage_refs(None, "start", "", {}, {"usage_applied": ["a"]}), ["a"])

    def test_no_delta_inherits_source_refs(self):
        self.assertEqual(
            api._dialog_usage_refs({"usage_refs": ["s"]}, "redo", "", {}, {"usage_applied": ["a"]}), ["s"])

    def test_recompiled_uses_meta_applied(self):
        self.assertEqual(
            api._dialog_usage_refs({"usage_refs": ["s"]}, "vibe", "", {}, {"usage_applied": ["a"]}), ["a"])
        self.assertEqual(
            api._dialog_usage_refs({"usage_refs": ["s"]}, "tweak", "改光照", {}, {"usage_applied": ["a"]}), ["a"])

    def test_explicit_body_refs_win(self):
        self.assertEqual(
            api._dialog_usage_refs({"usage_refs": ["s"]}, "redo", "", {"usage_refs": ["x"]}, {}), ["x"])


class ApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = AirPaintStore(root / "airpaint.db")
        self.images = root / "images"
        self.sources = root / "sources"
        self.images.mkdir()
        self.sources.mkdir()
        self.patches = [patch.object(api, name, value) for name, value in {
            "STORE": self.store, "IMAGES": self.images, "SOURCE_IMAGES": self.sources,
            "JOBS": {}, "SESSIONS": {}, "USAGE": {}, "QUEUE": asyncio.Queue(),
        }.items()]
        self.patches.append(patch.dict(
            prompt.CFG, {"translate": "siliconflow", "siliconflow_api_key": "test-key"},
            clear=False))
        for item in self.patches:
            item.start()
        self.previous = lora_module.get_lora_registry
        prompt._TRANSLATE_CACHE.clear()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        lora_module.get_lora_registry = self.previous
        prompt._TRANSLATE_CACHE.clear()
        self.store.close()

    async def test_translate_then_job_keeps_literal_final_and_refs(self):
        record = make_record()

        async def fake_translate(context, reroll=False, prior_state=None, usage_expected=False):
            return ("1girl, sitting", None, "", dict(IR), [], {},
                    "用户锁定：少女｜模型补全：坐姿", False, [],
                    {"applied": [record["usage_id"]], "negative": "NEG-NEW",
                     "evidence": "依据", "limits": "限制"})

        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        with patch.object(prompt, "siliconflow_translate", fake_translate), \
                patch.object(prompt, "_default_usage_fetch",
                             lambda uid: record if uid == record["usage_id"] else None):
            preview = await api.translate_prompt(
                Request(prompt="少女坐着", workflow="anima",
                        lora_selections=[{"key": "demo", "mode": "explicit"}]), token="t")
            job = await api.create_job(
                Request(workflow="anima", prompt_en=preview["final_prompt_en"], prompt="少女坐着",
                        prompt_mode="assisted", prompt_state="final",
                        negative_prompt=preview["final_negative"],
                        usage_refs=preview["usage_applied"],
                        lora_selections=[{"key": "demo", "mode": "explicit"}]), owner_id=OWNER)
        self.assertEqual(preview["usage_applied"], [record["usage_id"]])
        self.assertEqual(preview["final_negative"], "NEG-NEW")
        self.assertTrue(preview["final_prompt_en"].startswith(WORKFLOWS["anima"]["quality_prefix"]))
        stored = self.store.get_job(job["id"], OWNER)
        self.assertEqual(stored["usage_refs"], [record["usage_id"]])
        self.assertEqual(stored["prompt_en"], preview["final_prompt_en"])
        request = api._job_request(stored, None)
        self.assertEqual(request["prompt"][PROMPT_NODE]["inputs"]["text"], preview["final_prompt_en"])
        self.assertEqual(request["prompt"][NEG_NODE]["inputs"]["text"], "NEG-NEW")

    async def test_dialog_start_is_always_assisted_body(self):
        record = make_record()

        async def fake_translate(context, reroll=False, prior_state=None, usage_expected=False):
            return ("1girl, sitting", None, "", dict(IR), [], {},
                    "用户锁定：少女｜模型补全：坐姿", False, [],
                    {"applied": [record["usage_id"]], "negative": "NEG-NEW",
                     "evidence": "依据", "limits": ""})

        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        with patch.object(prompt, "siliconflow_translate", fake_translate), \
                patch.object(prompt, "_default_usage_fetch",
                             lambda uid: record if uid == record["usage_id"] else None):
            prefix = WORKFLOWS["anima"]["quality_prefix"]
            for extra in ({}, {"prompt_state": "final"},
                          {"prompt_mode": "manual", "prompt_state": "final"}):
                body = dict(action="start", prompt="少女坐着", workflow="anima",
                            lora_selections=[{"key": "demo", "mode": "explicit"}])
                body.update(extra)
                result = await api.dialog_turn(Request(**body), owner_id=OWNER)
                stored = self.store.get_job(result["job_id"], OWNER)
                self.assertTrue(stored["prompt_en"].startswith(prefix), extra)
                self.assertEqual(stored["prompt_en"].count(prefix.strip()), 1, extra)
                self.assertEqual(stored["final_negative"], "NEG-NEW", extra)
                self.assertEqual(stored["usage_refs"], [record["usage_id"]], extra)
        return
        self.assertEqual(True, False)


class DialogBranchBaselineTests(unittest.IsolatedAsyncioTestCase):
    """B：源任务合法空负面必须保持为空；refs 按分支继承或重编译。"""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = AirPaintStore(root / "airpaint.db")
        self.images = root / "images"
        self.sources = root / "sources"
        self.images.mkdir()
        self.sources.mkdir()
        self.patches = [patch.object(api, name, value) for name, value in {
            "STORE": self.store, "IMAGES": self.images, "SOURCE_IMAGES": self.sources,
            "JOBS": {}, "SESSIONS": {}, "USAGE": {}, "QUEUE": asyncio.Queue(),
        }.items()]
        self.patches.append(patch.dict(
            prompt.CFG, {"translate": "siliconflow", "siliconflow_api_key": "test-key"},
            clear=False))
        for item in self.patches:
            item.start()
        self.previous = lora_module.get_lora_registry
        prompt._TRANSLATE_CACHE.clear()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        lora_module.get_lora_registry = self.previous
        prompt._TRANSLATE_CACHE.clear()
        self.store.close()

    async def _source_job(self, record):
        record, _ = self.store.save_lora_usage(record)

        async def fake_translate(context, reroll=False, prior_state=None, usage_expected=False):
            return ("1girl, sitting", None, "", dict(IR), [], {},
                    "用户锁定：少女｜模型补全：坐姿", False, [],
                    {"applied": [record["usage_id"]], "negative": None, "evidence": "", "limits": ""})

        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [record["usage_id"]])}
        with patch.object(prompt, "siliconflow_translate", fake_translate), \
                patch.object(prompt, "_default_usage_fetch", self.store.get_lora_usage):
            started = await api.dialog_turn(
                Request(action="start", prompt="少女坐着", workflow="anima",
                        lora_selections=[{"key": "demo", "mode": "explicit"}]), owner_id=OWNER)
        source_id = started["job_id"]
        source = self.store.get_job(source_id, OWNER)
        (self.images / f"{source_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        source.update({"status": "done", "output_image_ref": f"{source_id}.png",
                       "image": f"/api/images/{source_id}.png", "final_negative": ""})
        self.store.save_job(source)
        return started["session_id"], self.store.get_job(source_id, OWNER)

    async def test_redo_without_delta_inherits_empty_negative_and_refs(self):
        record = make_record()
        session_id, source = await self._source_job(record)
        with patch.object(prompt, "_default_usage_fetch", self.store.get_lora_usage):
            redone = await api.dialog_turn(
                Request(action="redo", session_id=session_id, source_job_id=source["id"]),
                owner_id=OWNER)
        stored = self.store.get_job(redone["job_id"], OWNER)
        self.assertEqual(stored["final_negative"], "")
        self.assertEqual(stored["usage_refs"], source.get("usage_refs") or [])

    async def test_vibe_and_delta_pass_empty_source_negative_as_baseline(self):
        record = make_record()
        for action in ("vibe", "tweak"):
            session_id, source = await self._source_job(record)
            captured = {}

            async def capture(context, *args, **kwargs):
                captured.update(kwargs)
                meta = {"usage_applied": [], "usage_negative": None, "usage_warnings": [],
                        "lora_bindings": source.get("lora_bindings") or [],
                        "lora_warnings": source.get("lora_warnings") or [],
                        "registry_revision": source.get("registry_revision"),
                        "change_fields": [], "reference_contract": None, "reference_scope": None}
                return ("1girl, sitting", None, dict(IR), meta)

            with patch.object(api, "translate", capture), \
                    patch.object(prompt, "_default_usage_fetch", self.store.get_lora_usage):
                body = {"action": action, "session_id": session_id, "source_job_id": source["id"]}
                if action == "tweak":
                    body.update({"delta": "改成傍晚", "denoise": 0.55})
                else:
                    body.update({"delta": "换个氛围"})
                redone = await api.dialog_turn(Request(**body), owner_id=OWNER)
            self.assertEqual(captured.get("usage_baseline_negative"), "", action)
            stored = self.store.get_job(redone["job_id"], OWNER)
            self.assertEqual(stored["final_negative"], "", action)


class TranslateCacheTests(unittest.TestCase):
    """C：同输入命中缓存只调一次；workflow/负面基线变化重新调用且 meta 恢复。"""

    def setUp(self):
        self.saved_post = prompt.CLIENT.post
        self.previous = lora_module.get_lora_registry
        self.record = make_record()
        lora_module.get_lora_registry = lambda: {"demo": asset("demo", [self.record["usage_id"]])}
        prompt._TRANSLATE_CACHE.clear()
        self.calls = []
        self.cfg = patch.dict(prompt.CFG, {"translate": "siliconflow",
                                           "siliconflow_api_key": "test-key"}, clear=False)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        prompt.CLIENT.post = self.saved_post
        lora_module.get_lora_registry = self.previous
        prompt._TRANSLATE_CACHE.clear()

    def _post(self):
        async def fake_post(url, headers=None, timeout=None, **kwargs):
            self.calls.append(kwargs.get("json"))
            obj = {"applied": [self.record["usage_id"]], "negative": None,
                   "evidence": "依据", "limits": ""}
            lines = ["CONCEPT: 用户锁定：少女｜模型补全：坐姿",
                     "IR: " + json.dumps(IR, ensure_ascii=False), "CHAR: none",
                     "LORA: " + json.dumps({"demo": {"profile": None, "optional": []}}),
                     "USAGE: " + json.dumps(obj, ensure_ascii=False), "PROMPT: 1girl, sitting"]

            class Response:
                status_code = 200

                def json(self):
                    return {"choices": [{"message": {"content": "\n".join(lines)}}]}
            return Response()
        return fake_post

    def _translate(self, workflow="anima", baseline="BASE"):
        prompt.CLIENT.post = self._post()
        with patch.object(prompt, "_default_usage_fetch",
                          lambda uid: self.record if uid == self.record["usage_id"] else None):
            return asyncio.run(prompt.translate(
                "少女坐着", lora_selections=[{"key": "demo", "mode": "explicit"}],
                include_meta=True, usage_baseline_negative=baseline, usage_workflow=workflow))

    def test_same_input_hits_cache_once_and_restores_meta(self):
        first = self._translate()
        calls_after_first = len(self.calls)
        second = self._translate()
        self.assertEqual(len(self.calls), calls_after_first)
        self.assertEqual(second[3]["usage_applied"], first[3]["usage_applied"])
        self.assertEqual(second[3]["usage_warnings"], first[3]["usage_warnings"])
        self.assertIsNone(second[3]["usage_negative"])

    def test_workflow_and_baseline_changes_reissue(self):
        self._translate(workflow="anima")
        calls = len(self.calls)
        self._translate(workflow="other")
        self.assertEqual(len(self.calls), calls + 1)
        calls = len(self.calls)
        self._translate(workflow="other", baseline="OTHER")
        self.assertEqual(len(self.calls), calls + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
