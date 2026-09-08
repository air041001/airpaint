"""SQLite persistence for AirPaint jobs, dialog sessions, history, and quota.

The database is the source of truth.  ``runtime.JOBS`` and ``runtime.SESSIONS``
are deliberately only hot caches used by the single-process scheduler.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RECOVERABLE_STATUSES = (
    "queued",
    "waiting_for_comfy",
    "dispatching",
    "submitted",
    "running",
    "result_pending",
    "result_ready",
    "reconcile_pending",
)


class QuotaExceeded(RuntimeError):
    """Raised when creating a new generation would exceed today's quota."""


class OwnershipError(RuntimeError):
    """Raised when a parent/session does not belong to the current owner."""


def local_day(timestamp: float | None = None) -> str:
    """Return the server-local calendar day used by the documented daily quota."""
    moment = datetime.fromtimestamp(timestamp if timestamp is not None else time.time()).astimezone()
    return moment.date().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return copy.deepcopy(fallback)
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return copy.deepcopy(fallback)


_SECRET_KEYS = {
    "authorization",
    "api_key",
    "siliconflow_api_key",
    "token",
    "tokens",
}


def redact_secrets(value: Any) -> Any:
    """Deep-copy a request snapshot while dropping authentication material."""
    if isinstance(value, dict):
        return {
            str(key): redact_secrets(item)
            for key, item in value.items()
            if str(key).strip().lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


class OwnerIdentity:
    """Stable HMAC identities keep raw invitation codes out of business records."""

    def __init__(self, key_path: Path):
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) < 32:
                raise RuntimeError(f"身份密钥无效: {self.key_path}")
            self._key = key
        else:
            self._key = secrets.token_bytes(32)
            self.key_path.write_bytes(self._key)

    def for_token(self, token: str) -> str:
        return hmac.new(self._key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_cookie(self, owner_id: str, issued_at: int | None = None) -> str:
        payload = _json({"owner_id": owner_id, "issued_at": issued_at or int(time.time())})
        encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
        signature = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def verify_cookie(
        self,
        value: str,
        *,
        allowed_owner_ids: set[str],
        max_age_seconds: int = 30 * 24 * 60 * 60,
    ) -> str | None:
        try:
            encoded, supplied = value.rsplit(".", 1)
            expected = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                return None
            padded = encoded + "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            owner_id = str(payload["owner_id"])
            issued_at = int(payload["issued_at"])
            if owner_id not in allowed_owner_ids:
                return None
            if issued_at > time.time() + 60 or time.time() - issued_at > max_age_seconds:
                return None
            return owner_id
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            return None


_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        state_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        client_request_id TEXT,
        status TEXT NOT NULL,
        workflow TEXT NOT NULL,
        generation_mode TEXT NOT NULL,
        parent_job_id TEXT REFERENCES jobs(id),
        session_id TEXT REFERENCES sessions(id),
        seed INTEGER NOT NULL,
        width INTEGER,
        height INTEGER,
        registry_revision TEXT,
        source_image_ref TEXT,
        comfy_image_filename TEXT,
        output_image_ref TEXT,
        comfy_prompt_id TEXT,
        created_at REAL NOT NULL,
        queued_at REAL NOT NULL,
        dispatch_started_at REAL,
        submitted_at REAL,
        completed_at REAL,
        failed_at REAL,
        updated_at REAL NOT NULL,
        error_kind TEXT,
        error_message TEXT,
        payload_json TEXT NOT NULL,
        request_snapshot_json TEXT,
        comfy_output_json TEXT,
        UNIQUE(owner_id, client_request_id)
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_owner_created
        ON jobs(owner_id, created_at DESC, id DESC);
    CREATE INDEX IF NOT EXISTS idx_jobs_status_created
        ON jobs(status, created_at, id);
    CREATE INDEX IF NOT EXISTS idx_jobs_prompt_id
        ON jobs(comfy_prompt_id);
    CREATE INDEX IF NOT EXISTS idx_jobs_output
        ON jobs(owner_id, output_image_ref);

    CREATE TABLE IF NOT EXISTS session_turns (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        job_id TEXT NOT NULL REFERENCES jobs(id),
        action TEXT NOT NULL,
        delta TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        PRIMARY KEY(session_id, position),
        UNIQUE(session_id, job_id)
    );

    CREATE INDEX IF NOT EXISTS idx_turns_job ON session_turns(job_id);

    CREATE TABLE IF NOT EXISTS usage_daily (
        owner_id TEXT NOT NULL,
        usage_date TEXT NOT NULL,
        count INTEGER NOT NULL CHECK(count >= 0),
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner_id, usage_date)
    );
    """,
)


_JOB_COLUMN_FIELDS = {
    "status",
    "workflow",
    "generation_mode",
    "parent_job_id",
    "session_id",
    "seed",
    "width",
    "height",
    "registry_revision",
    "source_image_ref",
    "comfy_image_filename",
    "output_image_ref",
    "comfy_prompt_id",
    "created_at",
    "queued_at",
    "dispatch_started_at",
    "submitted_at",
    "completed_at",
    "failed_at",
    "updated_at",
    "error_kind",
    "error_message",
    "request_snapshot",
    "comfy_output",
}


class AirPaintStore:
    """Thread-safe SQLite repository with small, explicit transactions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库版本 {current} 高于当前程序支持的 {SCHEMA_VERSION}"
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                script = _MIGRATIONS[version - 1]
                try:
                    # sqlite3.executescript() commits a transaction opened by
                    # execute() first. Keep BEGIN/COMMIT inside the script so a
                    # partial schema can never be reported as current.
                    self._conn.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + script
                        + "\nINSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                        + f"VALUES ({version}, {time.time()});\n"
                        + f"PRAGMA user_version={version};\nCOMMIT;"
                    )
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def checkpoint(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def backup_to(self, destination: Path) -> None:
        """Create a transactionally consistent backup of a live database."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            with self._lock:
                self._conn.backup(target)
            target.execute("PRAGMA integrity_check")
        finally:
            target.close()

    @staticmethod
    def encode_cursor(created_at: float, job_id: str) -> str:
        raw = _json([created_at, job_id]).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str | None) -> tuple[float, str] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError
            return float(value[0]), str(value[1])
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("无效历史游标")

    def _row_to_job(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        job = _loads(row["payload_json"], {})
        job.update({
            "id": row["id"],
            "owner_id": row["owner_id"],
            "client_request_id": row["client_request_id"],
            "status": row["status"],
            "workflow": row["workflow"],
            "generation_mode": row["generation_mode"],
            "parent_job_id": row["parent_job_id"],
            "session_id": row["session_id"],
            "seed": row["seed"],
            "width": row["width"],
            "height": row["height"],
            "registry_revision": row["registry_revision"],
            "source_image_ref": row["source_image_ref"],
            "comfy_image_filename": row["comfy_image_filename"],
            "output_image_ref": row["output_image_ref"],
            "comfy_prompt_id": row["comfy_prompt_id"],
            "created_at": row["created_at"],
            "queued_at": row["queued_at"],
            "dispatch_started_at": row["dispatch_started_at"],
            "submitted_at": row["submitted_at"],
            "completed_at": row["completed_at"],
            "failed_at": row["failed_at"],
            "updated_at": row["updated_at"],
            "error_kind": row["error_kind"],
            "error_message": row["error_message"],
            "request_snapshot": _loads(row["request_snapshot_json"], None),
            "comfy_output": _loads(row["comfy_output_json"], None),
        })
        if row["output_image_ref"]:
            job["image"] = f"/api/images/{row['output_image_ref']}"
        else:
            job.pop("image", None)
        if row["error_message"]:
            job["error"] = row["error_message"]
        else:
            job.pop("error", None)
        return job

    @staticmethod
    def _payload(job: dict) -> dict:
        excluded = {
            "id", "owner_id", "token", "client_request_id", "status", "workflow",
            "generation_mode", "parent_job_id", "session_id", "seed", "width", "height",
            "registry_revision", "source_image_ref", "comfy_image_filename", "output_image_ref",
            "comfy_prompt_id", "created_at", "queued_at", "dispatch_started_at",
            "submitted_at", "completed_at", "failed_at", "updated_at", "error_kind",
            "error_message", "error", "image", "request_snapshot", "comfy_output",
        }
        return redact_secrets({k: copy.deepcopy(v) for k, v in job.items() if k not in excluded})

    def _job_values(self, job: dict) -> tuple:
        return (
            job["id"], job["owner_id"], job.get("client_request_id"), job["status"],
            job["workflow"], job["generation_mode"], job.get("parent_job_id"),
            job.get("session_id"), job["seed"], job.get("width"), job.get("height"),
            job.get("registry_revision"), job.get("source_image_ref"),
            job.get("comfy_image_filename"), job.get("output_image_ref"),
            job.get("comfy_prompt_id"), job["created_at"], job["queued_at"],
            job.get("dispatch_started_at"), job.get("submitted_at"),
            job.get("completed_at"), job.get("failed_at"), job["updated_at"],
            job.get("error_kind"), job.get("error_message") or job.get("error"),
            _json(self._payload(job)),
            _json(redact_secrets(job.get("request_snapshot")))
            if job.get("request_snapshot") is not None else None,
            _json(redact_secrets(job.get("comfy_output")))
            if job.get("comfy_output") is not None else None,
        )

    def get_job(self, job_id: str, owner_id: str | None = None) -> dict | None:
        with self._lock:
            if owner_id is None:
                row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)
                ).fetchone()
            return self._row_to_job(row)

    def get_job_by_request(self, owner_id: str, client_request_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE owner_id=? AND client_request_id=?",
                (owner_id, client_request_id),
            ).fetchone()
            return self._row_to_job(row)

    def create_generation(
        self,
        job: dict,
        *,
        daily_limit: int,
        usage_date: str | None = None,
        session: dict | None = None,
        turn: dict | None = None,
    ) -> tuple[dict, bool, int]:
        """Atomically create job + optional session turn + one quota charge.

        A duplicate ``client_request_id`` returns the original job and never
        increments usage or appends another turn.
        """
        owner_id = job["owner_id"]
        request_id = job.get("client_request_id")
        usage_date = usage_date or local_day(job.get("created_at"))
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if request_id:
                    existing = self._conn.execute(
                        "SELECT * FROM jobs WHERE owner_id=? AND client_request_id=?",
                        (owner_id, request_id),
                    ).fetchone()
                    if existing:
                        usage = self._usage_count_locked(owner_id, usage_date)
                        self._conn.commit()
                        return self._row_to_job(existing), False, usage

                if job.get("parent_job_id"):
                    parent = self._conn.execute(
                        "SELECT owner_id FROM jobs WHERE id=?", (job["parent_job_id"],)
                    ).fetchone()
                    if not parent or parent["owner_id"] != owner_id:
                        raise OwnershipError("父任务不存在或不属于当前用户")

                if session:
                    existing_session = self._conn.execute(
                        "SELECT owner_id FROM sessions WHERE id=?", (session["id"],)
                    ).fetchone()
                    if existing_session and existing_session["owner_id"] != owner_id:
                        raise OwnershipError("会话不存在或不属于当前用户")
                    if not existing_session:
                        self._conn.execute(
                            "INSERT INTO sessions(id, owner_id, created_at, updated_at, state_json) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (session["id"], owner_id, session["created_at"], now,
                             _json(redact_secrets(session))),
                        )
                elif job.get("session_id"):
                    existing_session = self._conn.execute(
                        "SELECT owner_id FROM sessions WHERE id=?", (job["session_id"],)
                    ).fetchone()
                    if not existing_session or existing_session["owner_id"] != owner_id:
                        raise OwnershipError("会话不存在或不属于当前用户")

                usage = self._usage_count_locked(owner_id, usage_date)
                if usage >= daily_limit:
                    raise QuotaExceeded(f"今日已达 {daily_limit} 张上限")

                self._conn.execute(
                    """
                    INSERT INTO jobs(
                        id, owner_id, client_request_id, status, workflow, generation_mode,
                        parent_job_id, session_id, seed, width, height, registry_revision,
                        source_image_ref, comfy_image_filename, output_image_ref, comfy_prompt_id,
                        created_at, queued_at, dispatch_started_at, submitted_at, completed_at,
                        failed_at, updated_at, error_kind, error_message, payload_json,
                        request_snapshot_json, comfy_output_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    self._job_values(job),
                )
                self._conn.execute(
                    "INSERT INTO usage_daily(owner_id, usage_date, count, updated_at) VALUES(?,?,1,?) "
                    "ON CONFLICT(owner_id, usage_date) DO UPDATE SET "
                    "count=count+1, updated_at=excluded.updated_at",
                    (owner_id, usage_date, now),
                )
                usage += 1

                if turn:
                    session_id = job.get("session_id") or (session or {}).get("id")
                    if not session_id:
                        raise RuntimeError("session turn 缺少 session_id")
                    pos = self._conn.execute(
                        "SELECT COALESCE(MAX(position), -1) + 1 FROM session_turns WHERE session_id=?",
                        (session_id,),
                    ).fetchone()[0]
                    self._conn.execute(
                        "INSERT INTO session_turns(session_id, position, job_id, action, delta, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, pos, job["id"], turn["action"], turn.get("delta", ""), now),
                    )
                    state = copy.deepcopy(session or self._session_state_locked(session_id))
                    state.update(turn.get("session_state") or {})
                    state["id"] = session_id
                    state["owner_id"] = owner_id
                    self._conn.execute(
                        "UPDATE sessions SET updated_at=?, state_json=? WHERE id=?",
                        (now, _json(redact_secrets(state)), session_id),
                    )
                self._conn.commit()
                return copy.deepcopy(job), True, usage
            except Exception:
                self._conn.rollback()
                raise

    def create_session_from_job(
        self,
        session: dict,
        *,
        job_id: str,
        action: str,
        delta: str = "",
    ) -> dict:
        owner_id = session["owner_id"]
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                job = self._conn.execute(
                    "SELECT owner_id FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if not job or job["owner_id"] != owner_id:
                    raise OwnershipError("原图任务不存在或不属于当前用户")
                self._conn.execute(
                    "INSERT INTO sessions(id, owner_id, created_at, updated_at, state_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session["id"], owner_id, session["created_at"], now,
                     _json(redact_secrets(session))),
                )
                self._conn.execute(
                    "INSERT INTO session_turns(session_id, position, job_id, action, delta, created_at) "
                    "VALUES (?, 0, ?, ?, ?, ?)",
                    (session["id"], job_id, action, delta, now),
                )
                self._conn.commit()
                return copy.deepcopy(session)
            except Exception:
                self._conn.rollback()
                raise

    def save_job(self, job: dict) -> dict:
        job = copy.deepcopy(job)
        job["updated_at"] = time.time()
        values = self._job_values(job)
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE jobs SET
                    owner_id=?, client_request_id=?, status=?, workflow=?, generation_mode=?,
                    parent_job_id=?, session_id=?, seed=?, width=?, height=?, registry_revision=?,
                    source_image_ref=?, comfy_image_filename=?, output_image_ref=?, comfy_prompt_id=?,
                    created_at=?, queued_at=?, dispatch_started_at=?, submitted_at=?, completed_at=?,
                    failed_at=?, updated_at=?, error_kind=?, error_message=?, payload_json=?,
                    request_snapshot_json=?, comfy_output_json=?
                WHERE id=?
                """,
                values[1:] + (job["id"],),
            )
            if cur.rowcount != 1:
                raise KeyError(job["id"])
        return job

    def update_job(self, job_id: str, **changes: Any) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            job = self._row_to_job(row)
            if not job:
                raise KeyError(job_id)
            for key, value in changes.items():
                if key == "error":
                    job["error_message"] = value
                else:
                    job[key] = value
            return self.save_job(job)

    def _usage_count_locked(self, owner_id: str, usage_date: str) -> int:
        row = self._conn.execute(
            "SELECT count FROM usage_daily WHERE owner_id=? AND usage_date=?",
            (owner_id, usage_date),
        ).fetchone()
        return int(row[0]) if row else 0

    def usage_count(self, owner_id: str, usage_date: str | None = None) -> int:
        with self._lock:
            return self._usage_count_locked(owner_id, usage_date or local_day())

    def list_jobs(
        self,
        owner_id: str,
        *,
        limit: int = 20,
        cursor: tuple[float, str] | None = None,
    ) -> tuple[list[dict], str | None]:
        limit = max(1, min(int(limit), 50))
        params: list[Any] = [owner_id]
        where = "owner_id=?"
        if cursor:
            where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend([cursor[0], cursor[0], cursor[1]])
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        jobs = [self._row_to_job(row) for row in rows]
        next_cursor = None
        if has_more and jobs:
            last = jobs[-1]
            next_cursor = self.encode_cursor(last["created_at"], last["id"])
        return jobs, next_cursor

    def recoverable_jobs(self) -> list[dict]:
        placeholders = ",".join("?" for _ in RECOVERABLE_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
                "ORDER BY created_at, id",
                RECOVERABLE_STATUSES,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def output_owned_by(self, owner_id: str, filename: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE owner_id=? AND output_image_ref=? LIMIT 1",
                (owner_id, filename),
            ).fetchone()
            return row is not None

    def _session_state_locked(self, session_id: str) -> dict:
        row = self._conn.execute(
            "SELECT state_json FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return _loads(row[0], {}) if row else {}

    def get_session(self, session_id: str, owner_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=? AND owner_id=?", (session_id, owner_id)
            ).fetchone()
            if not row:
                return None
            session = _loads(row["state_json"], {})
            session.update({
                "id": row["id"], "owner_id": row["owner_id"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            })
            turns = self._conn.execute(
                "SELECT job_id, action, delta, created_at FROM session_turns "
                "WHERE session_id=? ORDER BY position",
                (session_id,),
            ).fetchall()
            session["turns"] = [dict(turn) for turn in turns]
            return session

    def session_for_job(self, job_id: str, owner_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM session_turns WHERE job_id=? AND "
                "EXISTS(SELECT 1 FROM sessions s WHERE s.id=session_id AND s.owner_id=?)",
                (job_id, owner_id),
            ).fetchone()
            return row[0] if row else None

    def session_ids_for_job(self, job_id: str, owner_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT t.session_id FROM session_turns t "
                "JOIN sessions s ON s.id=t.session_id "
                "WHERE t.job_id=? AND s.owner_id=? ORDER BY s.updated_at DESC",
                (job_id, owner_id),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("jobs", "sessions", "session_turns", "usage_daily")
            }

    def asset_references(self) -> dict[str, list[str]]:
        """Return the exact input/output filenames referenced by persisted jobs."""
        with self._lock:
            outputs = [
                str(row[0]) for row in self._conn.execute(
                    "SELECT DISTINCT output_image_ref FROM jobs "
                    "WHERE output_image_ref IS NOT NULL ORDER BY output_image_ref"
                ).fetchall()
            ]
            sources = [
                str(row[0]) for row in self._conn.execute(
                    "SELECT DISTINCT source_image_ref FROM jobs "
                    "WHERE source_image_ref IS NOT NULL ORDER BY source_image_ref"
                ).fetchall()
            ]
        return {"images": outputs, "source_images": sources}

    def integrity_check(self) -> str:
        with self._lock:
            return str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])
