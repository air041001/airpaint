"""Persistence, idempotency, recovery, quota, and media-ownership checks.

No model or GPU service is contacted.  ComfyUI states are simulated at the
boundary used by the production worker.
"""
from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import api, workflow_engine as workflow
from server.maintenance import create_backup, restore_backup, verify_backup
from server.persistence import AirPaintStore, OwnerIdentity
from server.workflow_engine import ComfyResultUnavailable, ComfySubmissionUncertain


class Request:
    def __init__(self, **body):
        self.body = body
        self.headers = {}

    async def json(self):
        return self.body


class PersistenceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AirPaintStore(self.root / "airpaint.db")
        self.images = self.root / "images"
        self.sources = self.root / "sources"
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
            "_queued_for_worker": set(),
            "DAILY_LIMIT": 2,
        }.items()]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.store.close()
        self.temp.cleanup()

    async def enqueue(
        self, request_id: str, *, owner: str = "owner-a",
        source: bytes | None = None, fingerprint: str | None = None,
    ):
        return await api._enqueue(
            owner, "anima", "1girl, beach", "海边少女", "832x1216",
            [], None, None, seed=123, source_image_bytes=source,
            source_dimensions=(1, 1) if source else None,
            client_request_id=request_id,
            request_fingerprint=fingerprint,
        )

    async def test_idempotency_and_quota_share_one_transaction(self):
        first = await self.enqueue("request-0001")
        replay = await self.enqueue("request-0001")
        self.assertEqual(replay, first)
        self.assertTrue(api.JOBS[first]["idempotent_replay"])
        self.assertEqual(self.store.usage_count("owner-a"), 1)
        second = await self.enqueue("request-0002")
        self.assertNotEqual(second, first)
        self.assertEqual(self.store.usage_count("owner-a"), 2)
        with self.assertRaises(HTTPException) as error:
            await self.enqueue("request-0003")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(self.store.counts()["jobs"], 2)

    async def test_owner_cookie_survives_restart_without_storing_invite(self):
        key_path = self.root / "identity-cookie.key"
        first = OwnerIdentity(key_path)
        owner_id = first.for_token("friend-super-secret")
        cookie = first.issue_cookie(owner_id, issued_at=int(time.time()))
        second = OwnerIdentity(key_path)
        self.assertEqual(second.verify_cookie(cookie, allowed_owner_ids={owner_id}), owner_id)
        self.assertNotIn("friend-super-secret", cookie)

    async def test_idempotency_key_cannot_be_reused_for_different_request(self):
        await self.enqueue("request-conflict", fingerprint="fingerprint-a")
        with self.assertRaises(HTTPException) as error:
            api._idempotent_existing("owner-a", "request-conflict", "fingerprint-b")
        self.assertEqual(error.exception.status_code, 409)

    async def test_queued_restart_restores_once(self):
        job_id = await self.enqueue("request-queued")
        api.QUEUE = asyncio.Queue()
        api._queued_for_worker = set()
        api.JOBS = {}
        self.assertEqual(await api._restore_scheduler(), 1)
        self.assertEqual(await api._restore_scheduler(), 1)
        self.assertEqual(api.QUEUE.qsize(), 1)
        self.assertEqual(await api.QUEUE.get(), job_id)

    async def test_ambiguous_submission_is_never_blindly_resent(self):
        job_id = await self.enqueue("request-uncertain")
        job = self.store.get_job(job_id)
        prompt_id = "11111111-1111-4111-8111-111111111111"
        request = {
            "prompt": {"1": {"class_type": "Test", "inputs": {}}},
            "prompt_id": prompt_id,
            "extra_data": {"airpaint_job_id": job_id, "token": "must-not-persist"},
        }
        with patch.object(api, "_job_request", return_value=request), \
                patch.object(api, "submit_to_comfy", AsyncMock(
                    side_effect=ComfySubmissionUncertain("response lost")
                )) as submit:
            await api._submit_queued(job)
        submit.assert_awaited_once()
        saved = self.store.get_job(job_id)
        self.assertEqual(saved["status"], "reconcile_pending")
        self.assertEqual(saved["comfy_prompt_id"], prompt_id)
        self.assertNotIn("token", str(saved["request_snapshot"]))

        with patch.object(api, "inspect_comfy_prompt", AsyncMock(return_value={"state": "missing"})), \
                patch.dict(api.CFG, {"timeout_seconds": 0}), \
                patch.object(api, "submit_to_comfy", AsyncMock()) as no_resubmit:
            await api._monitor_submitted(saved)
        no_resubmit.assert_not_awaited()
        self.assertEqual(self.store.get_job(job_id)["status"], "reconcile_pending")

    async def test_running_restart_recovers_existing_result(self):
        job_id = await self.enqueue("request-running")
        prompt_id = "22222222-2222-4222-8222-222222222222"
        job = self.store.update_job(
            job_id, status="running", comfy_prompt_id=prompt_id, submitted_at=time.time()
        )

        async def retrieve(_prompt_id, destination):
            (self.images / destination).write_bytes(b"\x89PNG\r\n\x1a\nresult")
            return destination, {"filename": "comfy.png", "type": "output", "subfolder": ""}

        with patch.object(api, "inspect_comfy_prompt", AsyncMock(
                    return_value={"state": "result_ready", "entry": {"outputs": {}}}
                )), patch.object(api, "retrieve_comfy_result", AsyncMock(side_effect=retrieve)):
            await api._monitor_submitted(job)
        recovered = self.store.get_job(job_id)
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(recovered["comfy_prompt_id"], prompt_id)
        self.assertTrue((self.images / recovered["output_image_ref"]).is_file())

    async def test_result_download_retry_does_not_charge_again(self):
        job_id = await self.enqueue("request-result")
        prompt_id = "33333333-3333-4333-8333-333333333333"
        job = self.store.update_job(
            job_id, status="result_ready", comfy_prompt_id=prompt_id,
            error_kind="result_download_failed", error_message="network",
        )
        used = self.store.usage_count("owner-a")

        async def retrieve(_prompt_id, destination):
            (self.images / destination).write_bytes(b"\x89PNG\r\n\x1a\nresult")
            return destination, {"filename": "comfy.png", "type": "output", "subfolder": ""}

        with patch.object(api, "retrieve_comfy_result", AsyncMock(side_effect=retrieve)):
            response = await api.recover_job_result(job_id, "owner-a")
        self.assertEqual(response["status"], "done")
        self.assertEqual(self.store.usage_count("owner-a"), used)

    async def test_history_session_and_image_ownership_survive_empty_caches(self):
        job_id = await self.enqueue("request-history")
        (self.images / f"{job_id}.png").write_bytes(b"\x89PNG\r\n\x1a\nresult")
        done = self.store.update_job(
            job_id, status="done", output_image_ref=f"{job_id}.png", completed_at=time.time()
        )
        api.JOBS[job_id] = done
        opened = await api.dialog_turn(Request(action="start-image", job_id=job_id), "owner-a")
        session_id = opened["session_id"]
        api.JOBS = {}
        api.SESSIONS = {}
        history = await api.history(owner_id="owner-a")
        dialog = await api.dialog_get(session_id, "owner-a")
        self.assertEqual(history["items"][0]["id"], job_id)
        self.assertEqual(dialog["turns"][0]["job_id"], job_id)
        response = await api.protected_image(f"{job_id}.png", "owner-a")
        self.assertEqual(Path(response.path), self.images / f"{job_id}.png")
        with self.assertRaises(HTTPException) as error:
            await api.protected_image(f"{job_id}.png", "owner-b")
        self.assertEqual(error.exception.status_code, 404)

    async def test_live_database_backup_is_consistent(self):
        await self.enqueue("request-backup")
        backup = self.root / "backup.db"
        self.store.backup_to(backup)
        restored = AirPaintStore(backup)
        try:
            self.assertEqual(restored.integrity_check(), "ok")
            self.assertEqual(restored.counts(), self.store.counts())
            self.assertEqual(restored.get_job_by_request("owner-a", "request-backup")["seed"], 123)
        finally:
            restored.close()

    async def test_archive_restore_pairs_database_identity_and_images(self):
        job_id = await self.enqueue("request-archive", source=b"\x89PNG\r\n\x1a\nsource")
        output_name = f"{job_id}.png"
        (self.images / output_name).write_bytes(b"\x89PNG\r\n\x1a\noutput")
        self.store.update_job(
            job_id, status="done", output_image_ref=output_name, completed_at=time.time()
        )
        identity = self.root / "identity.key"
        identity.write_bytes(b"i" * 32)
        archive = self.root / "backup.zip"
        manifest = create_backup(
            archive,
            database_path=self.store.path,
            identity_key_path=identity,
            images_dir=self.images,
            source_images_dir=self.sources,
        )
        self.assertEqual(verify_backup(archive)["archive_sha256"], manifest["archive_sha256"])

        restored_root = self.root / "restored"
        result = restore_backup(
            archive,
            database_path=restored_root / "state" / "airpaint.db",
            identity_key_path=restored_root / "state" / "identity.key",
            images_dir=restored_root / "images",
            source_images_dir=restored_root / "sources",
        )
        self.assertEqual(result["restored_counts"], self.store.counts())
        self.assertEqual((restored_root / "state" / "identity.key").read_bytes(), b"i" * 32)
        self.assertEqual((restored_root / "images" / output_name).read_bytes(), b"\x89PNG\r\n\x1a\noutput")
        source_name = self.store.get_job(job_id)["source_image_ref"]
        self.assertTrue((restored_root / "sources" / source_name).is_file())

    async def test_replace_restore_removes_stale_sqlite_sidecars(self):
        await self.enqueue("request-sidecars")
        identity = self.root / "identity-sidecars.key"
        identity.write_bytes(b"k" * 32)
        archive = self.root / "sidecars.zip"
        create_backup(
            archive, database_path=self.store.path, identity_key_path=identity,
            images_dir=self.images, source_images_dir=self.sources,
        )

        target = self.root / "replace" / "airpaint.db"
        target.parent.mkdir()
        replacement = AirPaintStore(target)
        replacement.close()
        wal = target.with_name(target.name + "-wal")
        shm = target.with_name(target.name + "-shm")
        wal.write_bytes(b"stale-wal")
        shm.write_bytes(b"stale-shm")
        restore_backup(
            archive,
            database_path=target,
            identity_key_path=identity,
            images_dir=self.root / "replace-images",
            source_images_dir=self.root / "replace-sources",
            replace=True,
            rollback_output=self.root / "rollback.zip",
        )
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

    async def test_direct_static_image_bypass_is_not_registered(self):
        paths = {getattr(route, "path", "") for route in api.app.routes}
        self.assertIn("/api/images/{filename}", paths)
        self.assertNotIn("/images", paths)


class FakeComfyContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_preassigned_id_and_extra_data_are_reconcilable(self):
        prompt_id = "44444444-4444-4444-8444-444444444444"
        seen = {}

        def handler(request: httpx.Request):
            if request.url.path == "/prompt":
                body = json.loads(request.content)
                seen.update(body)
                return httpx.Response(200, json={"prompt_id": prompt_id})
            if request.url.path == f"/history/{prompt_id}":
                return httpx.Response(200, json={})
            if request.url.path == "/queue":
                return httpx.Response(200, json={
                    "queue_running": [[0, prompt_id, {}, {"airpaint_job_id": "job-1"}, []]],
                    "queue_pending": [],
                })
            raise AssertionError(request.url)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy")
        try:
            payload = workflow.submission_payload(
                {"prompt": {"1": {"class_type": "Test", "inputs": {}}}, "client_id": "client"},
                prompt_id,
                "job-1",
            )
            with patch.object(workflow, "CLIENT", client), patch.object(workflow, "COMFY", "http://comfy"):
                self.assertEqual(await workflow.submit_to_comfy(payload), prompt_id)
                self.assertEqual((await workflow.inspect_comfy_prompt(prompt_id))["state"], "running")
            self.assertEqual(seen["prompt_id"], prompt_id)
            self.assertEqual(seen["extra_data"]["airpaint_job_id"], "job-1")
        finally:
            await client.aclose()

    async def test_response_timeout_is_ambiguous_but_connect_failure_is_safe(self):
        prompt_id = "55555555-5555-4555-8555-555555555555"
        payload = {"prompt": {}, "prompt_id": prompt_id}

        def response_lost(request: httpx.Request):
            raise httpx.ReadTimeout("lost", request=request)

        lost_client = httpx.AsyncClient(transport=httpx.MockTransport(response_lost))
        try:
            with patch.object(workflow, "CLIENT", lost_client):
                with self.assertRaises(ComfySubmissionUncertain):
                    await workflow.submit_to_comfy(payload)
        finally:
            await lost_client.aclose()

        def connect_failed(request: httpx.Request):
            raise httpx.ConnectError("offline", request=request)

        offline_client = httpx.AsyncClient(transport=httpx.MockTransport(connect_failed))
        try:
            with patch.object(workflow, "CLIENT", offline_client):
                with self.assertRaises(workflow.ComfyUnavailable):
                    await workflow.submit_to_comfy(payload)
        finally:
            await offline_client.aclose()

    async def test_existing_history_result_can_be_downloaded_without_submit(self):
        prompt_id = "66666666-6666-4666-8666-666666666666"

        def handler(request: httpx.Request):
            if request.url.path == f"/history/{prompt_id}":
                return httpx.Response(200, json={prompt_id: {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"100": {"images": [{
                        "filename": "done.png", "subfolder": "", "type": "output",
                    }]}},
                }})
            if request.url.path == "/view":
                return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nresult")
            raise AssertionError(request.url)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfy")
        with tempfile.TemporaryDirectory() as name:
            try:
                with patch.object(workflow, "CLIENT", client), \
                        patch.object(workflow, "COMFY", "http://comfy"), \
                        patch.object(workflow, "IMAGES", Path(name)):
                    saved, output = await workflow.retrieve_comfy_result(prompt_id, "job.png")
                self.assertEqual(saved, "job.png")
                self.assertEqual(output["node_id"], "100")
                self.assertTrue((Path(name) / "job.png").is_file())
            finally:
                await client.aclose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
