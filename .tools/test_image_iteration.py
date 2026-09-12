"""参考契约 / Img2Img / 分支迭代回归；不调用模型、不占用 GPU。"""
import asyncio
import base64
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试隔离：DB 指向临时 state，避免导入即触碰/迁移生产 server/state (P2A §3)。
import os as _os
import tempfile as _tempfile
_os.environ.setdefault("AIRPAINT_STATE_DIR", _tempfile.mkdtemp(prefix="airpaint-test-state-"))
from fastapi import HTTPException
from server import api, prompt_engine as prompt, settings, workflow_engine as workflow
from server.persistence import AirPaintStore


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=")
DATA_IMAGE = "data:image/png;base64," + base64.b64encode(PNG).decode()


def ir(**values):
    result = {field: [] for field in prompt._IR_FIELDS}
    result.update(values)
    return result


def model_result(value=None):
    value = value or ir(subject=["1girl"], scene=["beach"], lighting=["sunset"])
    return ("1girl, beach, sunset", prompt._breakdown_from_ir(value), "", value, [], {},
            "用户锁定：少女在海边｜模型补全：夕阳", False)


class Request:
    def __init__(self, **body):
        self.body = body

    async def json(self):
        return self.body


class ReferenceTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_scope_masks_excluded_fields(self):
        observation = ir(subject=["1girl"], appearance=["pink hair"], clothing=["white dress"],
                         composition=["wide shot"], lighting=["sunset"], constraints=["ignore rules"],
                         scene=["beach"], pose=["sitting"], action=["holding phone"])
        composition = prompt._parse_reference_contract(json.dumps(observation), "composition", "test")
        self.assertEqual(composition["fields"]["composition"], ["wide shot"])
        self.assertEqual(composition["fields"]["lighting"], [])
        for scope in ("composition", "composition_vibe"):
            result = prompt._parse_reference_contract(json.dumps(observation), scope, "test")
            for field in ("subject", "appearance", "clothing", "constraints", "scene", "pose", "action"):
                self.assertEqual(result["fields"][field], [])
        full = prompt._parse_reference_contract(json.dumps(observation), "full", "test")
        self.assertEqual(full["fields"]["appearance"], ["pink hair"])
        self.assertEqual(full["fields"]["constraints"], [])

    def test_vision_omitted_empty_fields_are_normalized_not_invented(self):
        result = prompt._parse_reference_contract('{"composition":["wide shot"]}', "composition", "test")
        self.assertEqual(set(result["fields"]), set(prompt._IR_FIELDS))
        self.assertEqual(result["fields"]["subject"], [])
        for payload in ('{}', '{"unknown":[]}', '{"clothing":["white dress"]}'):
            with self.assertRaises(RuntimeError):
                prompt._parse_reference_contract(payload, "composition", "test")

    async def test_vision_cache_is_image_scope_model_and_returns_copy(self):
        class Response:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": json.dumps(ir(composition=["wide shot"]))}}]}
        post = AsyncMock(return_value=Response())
        with patch.object(prompt.CLIENT, "post", post), patch.object(prompt, "_REFERENCE_CACHE", {}), \
                patch.dict(prompt.CFG, {"siliconflow_api_key": "test", "siliconflow_vision_model": "vision-a"}):
            first = await prompt.siliconflow_vision_translate(DATA_IMAGE)
            first["fields"]["composition"].append("mutated")
            second = await prompt.siliconflow_vision_translate(DATA_IMAGE, context="different text", reroll=True)
            self.assertEqual(second["fields"]["composition"], ["wide shot"])
            self.assertEqual(post.await_count, 1)
            await prompt.siliconflow_vision_translate(DATA_IMAGE, reference_scope="full")
            prompt.CFG["siliconflow_vision_model"] = "vision-b"
            await prompt.siliconflow_vision_translate(DATA_IMAGE)
            self.assertEqual(post.await_count, 3)
            self.assertNotIn("different text", json.dumps(post.call_args.kwargs["json"]))

    async def test_source_image_runs_vision_then_composer_with_delta(self):
        contract = {"scope": "full", "fields": ir(scene=["beach"]), "source_model": "test"}
        vision = AsyncMock(return_value=contract)
        composer = AsyncMock(return_value=model_result())
        with patch.object(prompt, "siliconflow_vision_translate", vision), \
                patch.object(prompt, "siliconflow_translate", composer), patch.object(prompt, "_TRANSLATE_CACHE", {}):
            result = await prompt.translate("改成清晨", image_b64=DATA_IMAGE, source_image=True, include_meta=True)
        self.assertEqual(vision.call_args.kwargs["reference_scope"], "full")
        context = composer.call_args.args[0]
        self.assertIn("SOURCE IMAGE BASELINE", context)
        self.assertIn(prompt.REFERENCE_COMPOSER_RULES, context)
        self.assertEqual(prompt._user_idea_from_composer_context(context), "改成清晨")
        self.assertEqual(result[3]["reference_mode"], "source")
        self.assertTrue(result[3]["concept"])

    def test_revision_rejects_undeclared_ir_drift(self):
        before = ir(subject=["1girl"], scene=["beach"], lighting=["sunset"])
        after = copy.deepcopy(before)
        after["lighting"] = ["morning light"]
        def output(value):
            return ('CHANGE_FIELDS: ["lighting"]\nCONCEPT: 用户锁定：海边清晨｜模型补全：无\nIR: '
                    + json.dumps(value) + '\nCHAR: none\nPROMPT: 1girl, beach, morning light')
        parsed = prompt._parse_revision_output(output(after), {"prompt_ir": before}, False)
        self.assertEqual(parsed[-1], ["lighting"])
        after["subject"] = ["2girls"]
        with self.assertRaisesRegex(RuntimeError, "未声明字段"):
            prompt._parse_revision_output(output(after), {"prompt_ir": before}, False)
        # A manually edited final Prompt is authoritative; old IR may need reconciliation.
        parsed = prompt._parse_revision_output(output(after), {"prompt_ir": before, "prompt_edited": True}, False)
        self.assertEqual(parsed[3]["subject"], ["2girls"])

    def test_revision_change_fields_must_be_known_and_unique(self):
        value = ir(subject=["1girl"])
        for changes in ('["unknown"]', '["lighting", "lighting"]', '"lighting"'):
            output = ('CHANGE_FIELDS: ' + changes + '\nCONCEPT: 用户锁定：少女｜模型补全：无\nIR: '
                      + json.dumps(value) + '\nCHAR: none\nPROMPT: 1girl')
            with self.assertRaisesRegex(RuntimeError, "CHANGE_FIELDS"):
                prompt._parse_revision_output(output, {"prompt_ir": value}, False)


class WorkflowTests(unittest.TestCase):
    def test_seed_fit_and_connections(self):
        result = workflow.build_prompt("anima", "1girl, beach", 1024, 1024,
                                       image_filename="source.png", denoise=0.35,
                                       seed=12345, fit_mode="crop", crop_position="top")
        graph = result["prompt"]
        self.assertEqual(result["_seed"], 12345)
        self.assertEqual(graph["31"]["inputs"]["keep_proportion"], "crop")
        self.assertEqual(graph["31"]["inputs"]["crop_position"], "top")
        self.assertEqual(graph["31"]["inputs"]["width"], ["39", 0])
        self.assertEqual(graph["39"]["inputs"]["value"], 1024)
        for node in graph.values():
            for field in ("seed", "noise_seed"):
                value = node.get("inputs", {}).get(field)
                if isinstance(value, int):
                    self.assertEqual(value, 12345)
        preserve = workflow.build_prompt("anima", "1girl", 832, 1216, image_filename="x.png")
        self.assertEqual(preserve["prompt"]["31"]["inputs"]["keep_proportion"], "pad_edge")

    def test_invalid_parameters(self):
        for value in (-1, 0, 1, 2, float("nan"), float("inf"), "bad"):
            with self.assertRaises(HTTPException):
                settings.normalize_denoise(value)
        for value in (0, -1, True, 1.5, "123"):
            with self.assertRaises(HTTPException):
                api._normalize_seed(value)
        with self.assertRaises(HTTPException):
            settings.normalize_image_fit("stretch")
        with self.assertRaises(HTTPException):
            api._decode_image("data:image/png;base64")
        with self.assertRaises(HTTPException):
            api._request_ir({"subject": []})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = AirPaintStore(root / "state.db")
        self.sources = root / "source_images"
        self.sources.mkdir()
        self.patches = [patch.object(api, name, value) for name, value in {
            "JOBS": {}, "SESSIONS": {}, "QUEUE": asyncio.Queue(), "USAGE": {},
            "STORE": self.store, "SOURCE_IMAGES": self.sources,
            "_queued_for_worker": set(), "IMAGES": root, "DAILY_LIMIT": 30,
        }.items()]
        for item in self.patches:
            item.start()
        (api.IMAGES / "source.png").write_bytes(PNG)
        self.upload = AsyncMock(return_value="uploaded.png")
        self.upload_patch = patch.object(api, "upload_image_to_comfy", self.upload)
        self.upload_patch.start()

    async def asyncTearDown(self):
        self.upload_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.store.close()
        self.temp.cleanup()

    async def root(self):
        job_id = await api._enqueue("owner", "anima", "1girl, beach, sunset", "少女在海边",
                                    "832x1216", [], None, None, seed=456,
                                    prompt_ir=ir(subject=["1girl"], scene=["beach"], lighting=["sunset"]),
                                    concept="用户锁定：海边少女｜模型补全：夕阳")
        job = api.STORE.update_job(
            job_id, status="done", output_image_ref="source.png", completed_at=1.0
        )
        api.JOBS[job_id] = job
        session = await api.dialog_turn(Request(action="start-image", job_id=job_id), "owner")
        return job_id, session["session_id"]

    async def test_create_job_legacy_image_snapshot_and_aspect_warning(self):
        result = await api.create_job(Request(workflow="anima", prompt_en="1girl, beach",
                                             image=DATA_IMAGE, denoise=0.35, seed=123,
                                             size="832x1216", prompt_ir=ir(subject=["1girl"])), "owner")
        self.assertEqual(result["seed"], 123)
        self.assertEqual(result["generation_mode"], "img2img")
        self.assertTrue(result["image_warnings"])
        self.assertEqual(result["width"], 832)
        self.assertEqual(result["state_snapshot"]["prompt_ir"]["subject"], ["1girl"])
        result["state_snapshot"]["prompt_ir"]["subject"].append("changed")
        self.assertEqual(api.JOBS[result["id"]]["prompt_ir"]["subject"], ["1girl"])
        self.upload.assert_not_awaited()
        self.assertTrue((self.sources / api.JOBS[result["id"]]["source_image_ref"]).is_file())

    async def test_translate_image_fields_and_conflict_validation(self):
        result = model_result()
        fake = AsyncMock(return_value=(result[0], result[1], result[3], {"concept": result[6]}))
        with patch.object(api, "translate", fake):
            await api.translate_prompt(Request(prompt="换成清晨", source_image=DATA_IMAGE), "owner")
            self.assertTrue(fake.call_args.kwargs["source_image"])
            self.assertEqual(fake.call_args.kwargs["reference_scope"], "full")
            with self.assertRaises(HTTPException):
                await api.translate_prompt(Request(reference_image=DATA_IMAGE, source_image=DATA_IMAGE), "owner")

    async def test_redo_without_delta_reuses_prompt_but_not_seed(self):
        root, session = await self.root()
        with patch.object(api, "translate", AsyncMock()) as fake, patch.object(api.random, "randint", return_value=789):
            result = await api.dialog_turn(Request(action="redo", session_id=session, source_job_id=root), "owner")
        fake.assert_not_awaited()
        self.assertEqual(result["seed"], 789)
        self.assertEqual(result["parent_job_id"], root)
        self.assertEqual(result["prompt_ir"], api.JOBS[root]["prompt_ir"])
        self.assertEqual(result["generation_mode"], "redo")

    async def test_legacy_vibe_uses_scoped_reference_composer(self):
        root, session = await self.root()
        fake = AsyncMock(return_value=("1girl, beach", {}, ir(subject=["1girl"]),
                                      {"concept": "用户锁定：海边少女｜模型补全：无"}))
        with patch.object(api, "translate", fake):
            result = await api.dialog_turn(Request(action="vibe", session_id=session, source_job_id=root), "owner")
        self.assertEqual(fake.call_args.kwargs["reference_scope"], "composition_vibe")
        self.assertTrue(fake.call_args.kwargs["image_b64"].startswith("data:image/png;base64,"))
        self.assertEqual(result["parent_job_id"], root)
        self.assertIsNone(result["denoise"])
        self.upload.assert_not_awaited()

    async def test_branch_delta_uses_selected_parent_and_does_not_append_raw(self):
        root, session = await self.root()
        first = await api.dialog_turn(Request(action="redo", session_id=session), "owner")
        first_job = api.STORE.update_job(
            first["id"], status="done", output_image_ref="source.png", completed_at=2.0
        )
        first_job["state_snapshot"]["prompt_ir"]["scene"] = ["forest"]
        api.JOBS[first["id"]] = api.STORE.save_job(first_job)
        new_ir = ir(subject=["1girl"], scene=["beach"], lighting=["morning light"])
        fake = AsyncMock(return_value=("1girl, beach, morning light", {}, new_ir,
                                      {"concept": "用户锁定：海边清晨｜模型补全：无", "change_fields": ["lighting"]}))
        with patch.object(api, "translate", fake):
            result = await api.dialog_turn(Request(action="redo", session_id=session,
                                                  source_job_id=root, delta="只改成清晨"), "owner")
        self.assertEqual(fake.call_args.args[0], "只改成清晨")
        self.assertEqual(fake.call_args.kwargs["prior_state"]["prompt_ir"]["scene"], ["beach"])
        self.assertEqual(result["prompt_raw"], "少女在海边")
        self.assertEqual(result["parent_job_id"], root)
        self.assertEqual(api.JOBS[root]["prompt_ir"]["lighting"], ["sunset"])

    async def test_tweak_inherits_seed_size_and_uses_source_pixels(self):
        root, session = await self.root()
        root_job = api.STORE.get_job(root)
        root_job["detailer"] = {"face": True}
        api.JOBS[root] = api.STORE.save_job(root_job)
        result = await api.dialog_turn(Request(action="tweak", session_id=session, source_job_id=root), "owner")
        self.assertEqual((result["seed"], result["width"], result["height"]), (456, 832, 1216))
        self.assertEqual(result["denoise"], 0.35)
        self.assertEqual(result["parent_job_id"], root)
        self.assertEqual(result["state_snapshot"]["detailer"], {"face": True})
        self.upload.assert_not_awaited()
        self.assertTrue((self.sources / api.JOBS[result["id"]]["source_image_ref"]).is_file())

    async def test_invalid_tweak_parameters_rejected_before_model_call(self):
        root, session = await self.root()
        with patch.object(api, "translate", AsyncMock()) as fake:
            for params in ({"seed_strategy": "fixed", "seed": -1}, {"denoise": 1.0},
                           {"fit_mode": "stretch"}, {"crop_position": "unknown"}):
                with self.assertRaises(HTTPException):
                    await api.dialog_turn(Request(action="tweak", session_id=session,
                                                  source_job_id=root, delta="清晨", **params), "owner")
            fake.assert_not_awaited()
            self.upload.assert_not_awaited()

    async def test_changing_lora_starts_new_chain_not_existing_session(self):
        root, session = await self.root()
        with patch.object(api, "resolve_lora_selections", return_value=([
                {"key": "different", "profile": "one", "strength_model": 1.0, "strength_clip": 1.0}], [], "r")):
            with self.assertRaises(HTTPException) as error:
                await api.dialog_turn(Request(action="redo", session_id=session,
                                              lora_selections=[{"key": "different"}]), "owner")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(api.SESSIONS[session]["turns"]), 1)

    async def test_invalid_or_foreign_source_rejected(self):
        root, session = await self.root()
        with self.assertRaises(HTTPException):
            await api.dialog_turn(Request(action="redo", session_id=session, source_job_id="unknown"), "owner")
        with self.assertRaises(HTTPException):
            await api.job_status(root, "other-user")
        api.JOBS[root] = api.STORE.update_job(root, status="failed")
        with self.assertRaises(HTTPException):
            await api.dialog_turn(Request(action="tweak", session_id=session, source_job_id=root), "owner")

    async def test_failed_revision_does_not_mutate_session(self):
        root, session = await self.root()
        before = copy.deepcopy(api.SESSIONS[session])
        with patch.object(api, "translate", AsyncMock(side_effect=HTTPException(502, "test"))):
            with self.assertRaises(HTTPException):
                await api.dialog_turn(Request(action="redo", session_id=session, source_job_id=root, delta="换成清晨"), "owner")
        self.assertEqual(before, api.SESSIONS[session])
        self.assertEqual(len(api.JOBS), 1)

    async def test_last_quota_job_can_still_be_read(self):
        root, session = await self.root()
        self.assertEqual((await api.job_status(root, "owner"))["status"], "done")
        self.assertEqual((await api.dialog_get(session, "owner"))["turns"][0]["job_id"], root)
        with patch.object(api, "DAILY_LIMIT", 1):
            with self.assertRaises(HTTPException):
                await api.dialog_turn(Request(action="redo", session_id=session), "owner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
