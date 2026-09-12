"""P1.1 请求层集成：manual+LoRA 实际 workflow 参数、final 文本落盘/读取一致性、负面继承。

不调用真实模型/GPU/ComfyUI；使用临时数据库与受控 Registry 替身。
"""
import asyncio
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import api
from server import lora as lora_module
from server.lora import resolve_lora_selections
from server.persistence import AirPaintStore
from server.settings import WORKFLOWS
from server.workflow_engine import build_prompt

OWNER = "owner-p11"
PREFIX = WORKFLOWS["anima"]["quality_prefix"]
PROMPT_NODE = str(WORKFLOWS["anima"]["prompt_node"])
NEG_NODE = str(WORKFLOWS["anima"]["negative_node"])
LORA_NODE = str(WORKFLOWS["anima"]["lora_node"])


def _style_asset():
    return {
        "key": "demo_style", "type": "style", "name": "Demo Style",
        "file": "demo_style.safetensors", "trigger_policy": "none",
        "profiles": {}, "selection": {"default_profile": None},
        "provides": ["demo style"], "required_tags": ["demo_style_trigger"],
        "strength_model": 0.7, "strength_clip": 0.7,
        "configured": True, "legacy_keys": {},
    }


@contextmanager
def use_registry(registry):
    previous = lora_module.get_lora_registry
    lora_module.get_lora_registry = lambda: registry
    try:
        yield
    finally:
        lora_module.get_lora_registry = previous


class ManualLoRARequestTests(unittest.TestCase):
    def test_manual_lora_request_keeps_literal_text_and_physical_binding(self):
        with use_registry({"demo_style": _style_asset()}):
            bindings, _, _ = resolve_lora_selections([{"key": "demo_style", "mode": "explicit"}])
            payload = build_prompt("anima", "a lone robot", 832, 1216,
                                   lora_bindings=bindings,
                                   final_prompt_en="a lone robot", final_negative="blurry")
            # 物理加载仍按服务端 binding（manual 只跳过文本编译）
            loader = payload["prompt"][LORA_NODE]["inputs"]["loras"]["__value__"]
        wf = payload["prompt"]
        self.assertEqual(wf[PROMPT_NODE]["inputs"]["text"], "a lone robot")
        self.assertNotIn("demo_style_trigger", wf[PROMPT_NODE]["inputs"]["text"])
        self.assertEqual(loader, [{"name": "demo_style.safetensors", "strength": 0.7,
                                   "clipStrength": 0.7, "active": True}])
        self.assertEqual(wf[NEG_NODE]["inputs"]["text"], "blurry")

    def test_final_negative_is_literal_not_workflow_default(self):
        # 已确认的 final 负面是字面写入：即使服务端默认随后变成别的内容也不受影响。
        payload = build_prompt("anima", "x", 832, 1216,
                               final_prompt_en="x", final_negative="NEG-A")
        self.assertEqual(payload["prompt"][NEG_NODE]["inputs"]["text"], "NEG-A")


class FinalRequestIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = AirPaintStore(root / "airpaint.db")
        self.images = root / "images"
        self.sources = root / "sources"
        self.images.mkdir()
        self.sources.mkdir()
        self.patches = [patch.object(api, name, value) for name, value in {
            "STORE": self.store,
            "IMAGES": self.images,
            "SOURCE_IMAGES": self.sources,
            "JOBS": {},
            "SESSIONS": {},
            "USAGE": {},
            "QUEUE": asyncio.Queue(),
        }.items()]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in self.patches:
            item.stop()
        self.store.close()
        self.temp.cleanup()

    async def _enqueue(self, **kwargs):
        with use_registry({"demo_style": _style_asset()}):
            return await api._enqueue(
                OWNER, "anima", kwargs["prompt_en"], kwargs.get("prompt_raw", ""),
                "832x1216", [{"key": "demo_style", "mode": "explicit"}], None, None,
                prompt_mode=kwargs.get("prompt_mode", "assisted"),
                prompt_state=kwargs.get("prompt_state"),
                negative_prompt=kwargs.get("negative_prompt"))

    async def test_final_path_persists_and_worker_request_matches(self):
        job_id = await self._enqueue(prompt_en="1girl, sitting", prompt_raw="少女坐下",
                                     prompt_state="final", negative_prompt="NEG-A")
        job = self.store.get_job(job_id, OWNER)
        self.assertEqual(job["prompt_mode"], "assisted")
        self.assertEqual(job["prompt_state"], "final")
        self.assertEqual(job["prompt_en"], "1girl, sitting")   # final 提交值未被加前缀
        self.assertEqual(job["final_prompt_en"], "1girl, sitting")
        self.assertNotIn("masterpiece", job["prompt_en"])
        self.assertEqual(job["final_negative"], "NEG-A")
        self.assertEqual(job["negative_source"], "user")
        # 落盘 → 读取 → worker 请求一致
        with use_registry({"demo_style": _style_asset()}):
            request = api._job_request(job, None)
        self.assertEqual(request["prompt"][PROMPT_NODE]["inputs"]["text"], "1girl, sitting")
        self.assertEqual(request["prompt"][NEG_NODE]["inputs"]["text"], "NEG-A")
        loader = request["prompt"][LORA_NODE]["inputs"]["loras"]["__value__"]
        self.assertTrue(loader and loader[0]["active"])
        self.assertEqual(loader[0]["name"], "demo_style.safetensors")

    async def test_body_path_adds_prefix_once(self):
        job_id = await self._enqueue(prompt_en="1girl, beach", prompt_state="body",
                                     negative_prompt="NEG-A")
        job = self.store.get_job(job_id, OWNER)
        self.assertTrue(job["prompt_en"].startswith(PREFIX))
        self.assertEqual(job["prompt_en"].count("masterpiece"), 1)
        self.assertIn("demo_style_trigger", job["prompt_en"])   # body 路径注入 trigger
        self.assertEqual(job["final_negative"], "NEG-A")

    async def test_recompiled_branch_inherits_negative(self):
        # 有 delta 重编译：正向回到 body（需组装），负面仍继承源任务已确认值。
        job_id = await self._enqueue(prompt_en="1girl, new pose", prompt_state="body",
                                     negative_prompt="NEG-S")
        job = self.store.get_job(job_id, OWNER)
        self.assertTrue(job["prompt_en"].startswith(PREFIX))
        self.assertEqual(job["final_negative"], "NEG-S")

    async def test_legacy_path_keeps_wildcard_negative(self):
        job_id = await self._enqueue(prompt_en="1girl", prompt_state=None)
        job = self.store.get_job(job_id, OWNER)
        self.assertIsNone(job["final_negative"])
        self.assertEqual(job["negative_source"], "workflow_wildcard")
        with use_registry({"demo_style": _style_asset()}):
            request = api._job_request(job, None)
        self.assertIsInstance(request["prompt"][NEG_NODE]["inputs"]["text"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
