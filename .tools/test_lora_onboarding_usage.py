"""P2A 用法登记接入 onboarding 的真实向导/CLI 行为测试（mock 输入与外部服务）。"""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(tempfile.mkdtemp(prefix="airpaint-onb-usage-"))
REGISTRY = TEMP / "lora_registry.yaml"
EMPTY_REGISTRY = "schema_version: 1\nloras: {}\n"
REGISTRY.write_text(EMPTY_REGISTRY, encoding="utf-8")  # settings 校验要求它先存在
os.environ["AIRPAINT_STATE_DIR"] = str(TEMP / "state")
os.environ["AIRPAINT_LORA_REGISTRY"] = str(REGISTRY)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".tools"))
SPEC = importlib.util.spec_from_file_location(
    "register_lora_onb", ROOT / ".tools" / "register_lora.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

from server.lora import resolve_lora_usage
from server.persistence import AirPaintStore

SINGLE_ASSET = {"name": "Demo", "type": "style", "file": "demo.safetensors",
                "trigger_policy": "none", "required_tags": [], "provides": [],
                "source": "author description", "verified": "candidate"}
SINGLE_ASSET_REGISTRY = ("schema_version: 1\nloras:\n  demo:\n"
                         "    name: Demo\n    type: style\n    file: demo.safetensors\n"
                         "    trigger_policy: none\n")

ASSET = {"name": "Demo", "type": "style", "file": "demo.safetensors",
         "trigger_policy": "none", "required_tags": [], "provides": [],
         "source": "author description", "verified": "candidate", "x_custom": {"keep": 1}}
USAGE_BODY = "作者模板：{character} 站在 {scene}\nno watermark, no text"


class Scripted:
    """脚本化交互输入：choices 供 ask_choice，texts 供 ask / read_multiline。"""

    def __init__(self, choices, texts=()):
        self.choices = list(choices)
        self.texts = list(texts)

    def ask_choice(self, label, options, default):
        return self.choices.pop(0) if self.choices else default

    def ask(self, label, default=""):
        value = self.texts.pop(0) if self.texts else ""
        return value or default

    def read_multiline(self, label):
        return self.texts.pop(0) if self.texts else ""


class OnboardingUsageTests(unittest.TestCase):
    def setUp(self):
        REGISTRY.write_text(EMPTY_REGISTRY, encoding="utf-8")
        (TEMP / "demo.safetensors").write_bytes(b"stub")
        self.db = TEMP / f"db-{self.id().split('.')[-1]}.db"

    def _agent(self, scripted, db=None):
        """跑真实 run_agent_onboarding，只 mock 外部服务与交互。"""
        with patch.object(tool, "choose_unregistered_file", lambda raw: "demo.safetensors"), \
                patch.object(tool, "LORA_DIR", TEMP), \
                patch.object(tool, "ensure_lora_manager_index", lambda filename: True), \
                patch.object(tool, "show_local_civitai_candidate", lambda filename: {}), \
                patch.object(tool, "call_onboard_agent",
                             lambda *a, **k: ("demo", dict(ASSET), {})), \
                patch.object(tool, "run_style_preview_flow", lambda *a, **k: "later"), \
                patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(tool, "read_multiline", scripted.read_multiline):
            with redirect_stdout(io.StringIO()):
                return tool.run_agent_onboarding(
                    tool.load_registry(), None, None, False,
                    str(db or self.db), None)

    def _registry_text(self):
        return REGISTRY.read_text(encoding="utf-8")

    def test_new_registration_with_usage_links_record_and_resolves(self):
        scripted = Scripted(choices=["write", "y", "paste", "community", "n", "y"],
                            texts=["作者说明", "demo", USAGE_BODY, "https://example.invalid/x", ""])
        self.assertEqual(self._agent(scripted), 0)
        text = self._registry_text()
        self.assertIn("usage:", text)
        store = AirPaintStore(self.db)
        try:
            records = store.list_lora_usage("demo")
            default_db = tool.default_state_db_path()
            self.assertEqual(len(records), 1,
                             f"db={self.db} exists={self.db.exists()} "
                             f"default={default_db} default_exists={default_db.exists()}")
            self.assertEqual(records[0]["body"], USAGE_BODY)
            self.assertEqual(records[0]["source_kind"], "community")
            asset = tool.load_registry()["loras"]["demo"]
            view = resolve_lora_usage(asset, [], store.get_lora_usage)
        finally:
            store.close()
        self.assertEqual(view["status"], "ok")
        self.assertEqual([r["usage_id"] for r in view["shared"]], [records[0]["usage_id"]])

    def test_skipping_usage_keeps_old_behaviour_and_creates_no_db(self):
        scripted = Scripted(choices=["write", "n", "y"], texts=["作者说明", "demo"])
        self.assertEqual(self._agent(scripted), 0)
        self.assertNotIn("usage:", self._registry_text())
        self.assertFalse(self.db.exists())

    def test_cancelling_at_final_confirmation_writes_nothing(self):
        before = self._registry_text()
        scripted = Scripted(choices=["write", "n", "n"], texts=["作者说明", "demo"])
        self.assertEqual(self._agent(scripted), 1)
        self.assertEqual(self._registry_text(), before)
        self.assertFalse(self.db.exists())

    def test_attach_usage_to_registered_asset_keeps_other_fields(self):
        REGISTRY.write_text(
            "schema_version: 1\nloras:\n  demo:\n" + "".join(
                f"    {line}\n" for line in (
                    'name: Demo', 'type: style', 'file: demo.safetensors',
                    "trigger_policy: none", "required_tags: []", "provides: []",
                    'source: author description', 'verified: candidate',
                    "x_custom: {keep: 1}")), encoding="utf-8")
        body_file = TEMP / "usage.md"
        body_file.write_text(USAGE_BODY, encoding="utf-8")
        scripted = Scripted(choices=["y"])
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(tool, "read_multiline", scripted.read_multiline):
            with redirect_stdout(io.StringIO()):
                code = tool.run_attach_usage(tool.load_registry(), "demo",
                                             str(body_file), str(self.db))
        self.assertEqual(code, 0)
        asset = tool.load_registry()["loras"]["demo"]
        self.assertEqual(asset["x_custom"], {"keep": 1})
        self.assertEqual(asset["verified"], "candidate")
        self.assertEqual(len((asset.get("usage") or {}).get("refs") or []), 1)

    def test_repeat_attach_is_idempotent(self):
        REGISTRY.write_text("schema_version: 1\nloras:\n  demo:\n"
                            "    name: Demo\n    type: style\n    file: demo.safetensors\n"
                            "    trigger_policy: none\n", encoding="utf-8")
        body_file = TEMP / "usage2.md"
        body_file.write_text(USAGE_BODY, encoding="utf-8")
        for _ in range(2):
            scripted = Scripted(choices=["y"])
            with patch.object(tool, "ask_choice", scripted.ask_choice), \
                    patch.object(tool, "ask", scripted.ask), \
                    patch.object(tool, "read_multiline", scripted.read_multiline):
                with redirect_stdout(io.StringIO()):
                    tool.run_attach_usage(tool.load_registry(), "demo", str(body_file), str(self.db))
        asset = tool.load_registry()["loras"]["demo"]
        refs = (asset.get("usage") or {}).get("refs") or []
        self.assertEqual(len(refs), 1)
        store = AirPaintStore(self.db)
        try:
            self.assertEqual(len(store.list_lora_usage("demo")), 1)
        finally:
            store.close()

    def test_unknown_profile_scope_is_rejected(self):
        asset = dict(ASSET)
        scripted = Scripted(choices=["y", "paste", "user", "n"], texts=[USAGE_BODY, "", "ghost"])
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(tool, "read_multiline", scripted.read_multiline):
            with self.assertRaises(SystemExit):
                tool.collect_usage_entries("demo", asset)

    def test_concurrent_registry_change_is_refused(self):
        entries = [{"asset_key": "demo", "profile_id": "", "body": USAGE_BODY,
                    "source_kind": "user", "source_url": ""}]
        candidate = {"schema_version": 1, "loras": {"demo": dict(ASSET)}}
        with self.assertRaises(SystemExit):
            tool.persist_usage_and_registry("demo", candidate, entries,
                                            db_path=self.db, previous_raw={"stale": True})
        self.assertEqual(self._registry_text(), EMPTY_REGISTRY)

    def test_registry_write_failure_keeps_records_reusable(self):
        entries = [{"asset_key": "demo", "profile_id": "", "body": USAGE_BODY,
                    "source_kind": "user", "source_url": ""}]
        candidate = {"schema_version": 1, "loras": {"demo": dict(ASSET)}}
        previous = tool.load_registry()
        with patch.object(tool, "atomic_write_registry",
                          side_effect=OSError("disk full")):
            with self.assertRaises(SystemExit):
                tool.persist_usage_and_registry("demo", candidate, entries,
                                                db_path=self.db, previous_raw=previous)
        self.assertEqual(self._registry_text(), EMPTY_REGISTRY)
        store = AirPaintStore(self.db)
        try:
            self.assertEqual(len(store.list_lora_usage("demo")), 1)
        finally:
            store.close()

    def test_db_failure_leaves_registry_untouched(self):
        entries = [{"asset_key": "demo", "profile_id": "", "body": USAGE_BODY,
                    "source_kind": "user", "source_url": ""}]
        candidate = {"schema_version": 1, "loras": {"demo": dict(ASSET)}}
        previous = tool.load_registry()
        with patch.object(AirPaintStore, "save_lora_usage",
                          side_effect=RuntimeError("db down")):
            with self.assertRaises(SystemExit):
                tool.persist_usage_and_registry("demo", candidate, entries,
                                                db_path=self.db, previous_raw=previous)
        self.assertEqual(self._registry_text(), EMPTY_REGISTRY)

    def test_help_creates_no_state_database(self):
        state = TEMP / "help-state"
        env = dict(os.environ)
        env["AIRPAINT_STATE_DIR"] = str(state)
        env["AIRPAINT_LORA_REGISTRY"] = str(REGISTRY)
        result = subprocess.run(
            [sys.executable, str(ROOT / ".tools" / "register_lora.py"), "--help"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((state / "airpaint.db").exists())


class ProductionStateIsolationTests(unittest.TestCase):
    def test_repo_state_database_is_not_modified_by_help(self):
        production = ROOT / "server" / "state" / "airpaint.db"
        before = production.stat().st_mtime_ns if production.exists() else None
        subprocess.run([sys.executable, str(ROOT / ".tools" / "register_lora.py"), "--help"],
                       cwd=str(ROOT), env=dict(os.environ), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        after = production.stat().st_mtime_ns if production.exists() else None
        self.assertEqual(before, after)


class AgentMenuTests(unittest.TestCase):
    """--agent 无 filename 的真实菜单：attach 走补用法路径，不跑候选/Manager/预览。"""

    def setUp(self):
        REGISTRY.write_text(SINGLE_ASSET_REGISTRY, encoding="utf-8")
        self.db = TEMP / f"menu-{self.id().split('.')[-1]}.db"

    def test_main_cli_agent_attach_updates_db_and_registry(self):
        body_file = TEMP / "menu-usage.md"
        body_file.write_text(USAGE_BODY, encoding="utf-8")
        scripted = Scripted(choices=["attach", "y"], texts=["1"])
        argv = [str(ROOT / ".tools" / "register_lora.py"), "--agent",
                "--usage-file", str(body_file), "--db", str(self.db)]
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(tool, "read_multiline", scripted.read_multiline), \
                patch.object(sys, "argv", argv), \
                patch.object(tool, "call_onboard_agent",
                             side_effect=AssertionError("attach 不应调用候选 LLM")), \
                patch.object(tool, "ensure_lora_manager_index",
                             side_effect=AssertionError("attach 不应刷新 Manager")), \
                patch.object(tool, "run_style_preview_flow",
                             side_effect=AssertionError("attach 不应生成预览")):
            with redirect_stdout(io.StringIO()):
                code = tool.main_cli()
        self.assertEqual(code, 0)
        asset = tool.load_registry()["loras"]["demo"]
        refs = (asset.get("usage") or {}).get("refs") or []
        self.assertEqual(len(refs), 1)
        store = AirPaintStore(self.db)
        try:
            self.assertEqual(len(store.list_lora_usage("demo")), 1)
        finally:
            store.close()

    def test_agent_menu_cancel_writes_nothing(self):
        scripted = Scripted(choices=["cancel"], texts=[""])
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask):
            with redirect_stdout(io.StringIO()):
                code = tool.run_agent_menu(tool.load_registry(), None, None, False,
                                           str(self.db), None)
        self.assertEqual(code, 0)
        self.assertFalse(self.db.exists())
        self.assertEqual(REGISTRY.read_text(encoding="utf-8"), SINGLE_ASSET_REGISTRY)


class PersistFailureTests(unittest.TestCase):
    def setUp(self):
        REGISTRY.write_text(SINGLE_ASSET_REGISTRY, encoding="utf-8")
        self.db = TEMP / f"fail-{self.id().split('.')[-1]}.db"

    def test_second_invalid_entry_creates_no_database(self):
        entries = [{"asset_key": "demo", "profile_id": "", "body": "ok",
                    "source_kind": "user", "source_url": ""},
                   {"asset_key": "demo", "profile_id": "ghost", "body": "x",
                    "source_kind": "user", "source_url": ""}]
        candidate = {"schema_version": 1, "loras": {"demo": dict(SINGLE_ASSET)}}
        with self.assertRaises(SystemExit):
            tool.persist_usage_and_registry("demo", candidate, entries,
                                            db_path=self.db,
                                            previous_raw=tool.load_registry())
        self.assertFalse(self.db.exists())
        self.assertEqual(REGISTRY.read_text(encoding="utf-8"), SINGLE_ASSET_REGISTRY)

    def test_db_open_failure_returns_non_zero_without_traceback(self):
        body_file = TEMP / "fail-usage.md"
        body_file.write_text(USAGE_BODY, encoding="utf-8")
        scripted = Scripted(choices=["y"], texts=[""])
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(AirPaintStore, "__init__", side_effect=OSError("disk full")):
            with redirect_stdout(io.StringIO()):
                code = tool.run_attach_usage(tool.load_registry(), "demo",
                                             str(body_file), str(self.db))
        self.assertEqual(code, 1)
        self.assertEqual(REGISTRY.read_text(encoding="utf-8"), SINGLE_ASSET_REGISTRY)

    def test_atomic_write_failure_returns_non_zero_and_keeps_records(self):
        body_file = TEMP / "fail-atomic.md"
        body_file.write_text(USAGE_BODY, encoding="utf-8")
        scripted = Scripted(choices=["y"], texts=[""])
        with patch.object(tool, "ask_choice", scripted.ask_choice), \
                patch.object(tool, "ask", scripted.ask), \
                patch.object(tool, "atomic_write_registry", side_effect=OSError("lock")):
            with redirect_stdout(io.StringIO()):
                code = tool.run_attach_usage(tool.load_registry(), "demo",
                                             str(body_file), str(self.db))
        self.assertEqual(code, 1)
        self.assertEqual(REGISTRY.read_text(encoding="utf-8"), SINGLE_ASSET_REGISTRY)
        store = AirPaintStore(self.db)
        try:
            self.assertEqual(len(store.list_lora_usage("demo")), 1)
        finally:
            store.close()


class RegistryConcurrencyTests(unittest.TestCase):
    """持久化期间他人改写 Registry：保留他人版本、返回非零、资料已保存可复用。"""

    def setUp(self):
        REGISTRY.write_text(SINGLE_ASSET_REGISTRY, encoding="utf-8")
        self.db = TEMP / f"conc-{self.id().split('.')[-1]}.db"

    def _entries(self):
        return [{"asset_key": "demo", "profile_id": "", "body": USAGE_BODY,
                 "source_kind": "user", "source_url": ""}]

    def test_registry_changed_while_writing_db_keeps_others_change(self):
        candidate = {"schema_version": 1, "loras": {"demo": dict(SINGLE_ASSET)}}
        previous = tool.load_registry()
        others = SINGLE_ASSET_REGISTRY + (
            "  other:\n    name: Other\n    type: style\n"
            "    file: other.safetensors\n    trigger_policy: none\n")
        real_save = AirPaintStore.save_lora_usage

        def save_then_modify(store_self, record):
            result = real_save(store_self, record)
            REGISTRY.write_text(others, encoding="utf-8")   # 模拟他人并发写入
            return result

        with patch.object(AirPaintStore, "save_lora_usage", save_then_modify):
            with self.assertRaises(SystemExit):
                tool.persist_usage_and_registry("demo", candidate, self._entries(),
                                                db_path=self.db, previous_raw=previous)
        self.assertEqual(REGISTRY.read_text(encoding="utf-8"), others)
        store = AirPaintStore(self.db)
        try:
            self.assertEqual(len(store.list_lora_usage("demo")), 1)
        finally:
            store.close()

    def test_existing_db_record_first_reference_still_counts_as_added(self):
        pre = AirPaintStore(self.db)
        try:
            pre.save_lora_usage({"asset_key": "demo", "body": USAGE_BODY,
                                 "source_kind": "user"})
        finally:
            pre.close()
        candidate = {"schema_version": 1, "loras": {"demo": dict(SINGLE_ASSET)}}
        result = tool.persist_usage_and_registry("demo", candidate, self._entries(),
                                                 db_path=self.db,
                                                 previous_raw=tool.load_registry())
        self.assertEqual(result["refs_added"], 1)          # 首次关联仍算新增引用
        self.assertEqual(len(result["reused"]), 1)         # 记录本身是复用的
        asset = tool.load_registry()["loras"]["demo"]
        self.assertEqual(len((asset.get("usage") or {}).get("refs") or []), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
