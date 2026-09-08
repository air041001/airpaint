"""AirPaint state backup, verification, and offline restore utilities."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from server.persistence import AirPaintStore, SCHEMA_VERSION
from server.settings import (
    BASE,
    CFG,
    DATABASE_PATH,
    IDENTITY_KEY_PATH,
    SOURCE_IMAGES,
)


IMAGES = BASE / "images"


BACKUP_FORMAT = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_asset(directory: Path, filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise RuntimeError(f"数据库包含不安全的图片引用: {filename!r}")
    return directory / filename


def create_backup(
    output: Path,
    *,
    database_path: Path = DATABASE_PATH,
    identity_key_path: Path = IDENTITY_KEY_PATH,
    images_dir: Path = IMAGES,
    source_images_dir: Path = SOURCE_IMAGES,
) -> dict:
    """Create one consistent SQLite + referenced-image zip archive."""
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not Path(identity_key_path).is_file():
        raise RuntimeError(f"缺少身份密钥: {identity_key_path}")

    with tempfile.TemporaryDirectory(prefix="airpaint-backup-") as temp_name:
        stage = Path(temp_name)
        state_dir = stage / "state"
        stage_images = stage / "images"
        stage_sources = stage / "source_images"
        state_dir.mkdir()
        stage_images.mkdir()
        stage_sources.mkdir()

        store = AirPaintStore(Path(database_path))
        try:
            store.backup_to(state_dir / "airpaint.db")
            references = store.asset_references()
            counts = store.counts()
            schema_version = store.schema_version
        finally:
            store.close()
        shutil.copy2(identity_key_path, state_dir / "identity.key")

        missing: list[str] = []
        for category, source, target in (
            ("images", Path(images_dir), stage_images),
            ("source_images", Path(source_images_dir), stage_sources),
        ):
            for filename in references[category]:
                candidate = _safe_asset(source, filename)
                if not candidate.is_file():
                    missing.append(f"{category}/{filename}")
                    continue
                shutil.copy2(candidate, target / filename)
        if missing:
            raise RuntimeError("备份中止，数据库引用的文件缺失: " + ", ".join(missing[:10]))

        files: dict[str, dict] = {}
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            relative = path.relative_to(stage).as_posix()
            files[relative] = {"sha256": _sha256(path), "size": path.stat().st_size}
        manifest = {
            "format_version": BACKUP_FORMAT,
            "schema_version": schema_version,
            "created_at": time.time(),
            "created_local": datetime.now().astimezone().isoformat(timespec="seconds"),
            "counts": counts,
            "references": references,
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                archive.write(path, path.relative_to(stage).as_posix())
        os.replace(temporary, output)
    return manifest | {"archive": str(output), "archive_sha256": _sha256(output)}


def verify_backup(archive_path: Path) -> dict:
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file():
        raise RuntimeError(f"备份不存在: {archive_path}")
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        for name in names:
            candidate = Path(name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise RuntimeError(f"备份包含不安全路径: {name}")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("备份 manifest 无效") from exc
        if manifest.get("format_version") != BACKUP_FORMAT:
            raise RuntimeError(f"不支持的备份格式: {manifest.get('format_version')}")
        if int(manifest.get("schema_version", -1)) > SCHEMA_VERSION:
            raise RuntimeError("备份数据库版本高于当前程序支持版本")
        for name, expected in (manifest.get("files") or {}).items():
            try:
                data = archive.read(name)
            except KeyError as exc:
                raise RuntimeError(f"备份缺少文件: {name}") from exc
            if len(data) != int(expected["size"]):
                raise RuntimeError(f"备份文件大小不符: {name}")
            if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise RuntimeError(f"备份文件校验失败: {name}")

        with tempfile.TemporaryDirectory(prefix="airpaint-verify-") as temp_name:
            db_path = Path(temp_name) / "airpaint.db"
            db_path.write_bytes(archive.read("state/airpaint.db"))
            connection = sqlite3.connect(db_path)
            try:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()
            if integrity != "ok":
                raise RuntimeError(f"备份数据库损坏: {integrity}")
            if version != int(manifest["schema_version"]):
                raise RuntimeError("manifest 与数据库 schema 版本不一致")
    return manifest | {"archive": str(archive_path), "archive_sha256": _sha256(archive_path)}


def restore_backup(
    archive_path: Path,
    *,
    database_path: Path = DATABASE_PATH,
    identity_key_path: Path = IDENTITY_KEY_PATH,
    images_dir: Path = IMAGES,
    source_images_dir: Path = SOURCE_IMAGES,
    replace: bool = False,
    rollback_output: Path | None = None,
) -> dict:
    """Restore a verified archive. The caller must ensure AirPaint is stopped."""
    manifest = verify_backup(archive_path)
    database_path = Path(database_path)
    identity_key_path = Path(identity_key_path)
    images_dir = Path(images_dir)
    source_images_dir = Path(source_images_dir)
    if database_path.exists() and not replace:
        raise RuntimeError("目标数据库已存在；停止 AirPaint 后使用 --replace，并保留自动回滚备份")

    if database_path.exists() and replace:
        rollback_output = rollback_output or (
            database_path.parent.parent / "backups" /
            f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        create_backup(
            rollback_output,
            database_path=database_path,
            identity_key_path=identity_key_path,
            images_dir=images_dir,
            source_images_dir=source_images_dir,
        )

    with tempfile.TemporaryDirectory(prefix="airpaint-restore-") as temp_name:
        stage = Path(temp_name)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for name in archive.namelist():
                candidate = Path(name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise RuntimeError(f"备份包含不安全路径: {name}")
                if name.endswith("/"):
                    continue
                target = stage / candidate
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))

        database_path.parent.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)
        source_images_dir.mkdir(parents=True, exist_ok=True)
        db_temp = database_path.with_name(f".{database_path.name}.restore.tmp")
        key_temp = identity_key_path.with_name(f".{identity_key_path.name}.restore.tmp")
        shutil.copy2(stage / "state" / "airpaint.db", db_temp)
        shutil.copy2(stage / "state" / "identity.key", key_temp)
        # The service is required to be stopped. Remove only SQLite's exact
        # sidecars before replacing the main file, otherwise a stale WAL from
        # the previous database could be replayed into the restored database.
        for suffix in ("-wal", "-shm"):
            database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)
        os.replace(db_temp, database_path)
        os.replace(key_temp, identity_key_path)
        for category, target_dir in (("images", images_dir), ("source_images", source_images_dir)):
            source_dir = stage / category
            for filename in manifest["references"][category]:
                shutil.copy2(_safe_asset(source_dir, filename), target_dir / filename)

    restored = AirPaintStore(database_path)
    try:
        if restored.integrity_check() != "ok":
            raise RuntimeError("恢复后的数据库完整性检查失败")
        counts = restored.counts()
    finally:
        restored.close()
    if counts != manifest["counts"]:
        raise RuntimeError("恢复后的数据库记录数量与备份不一致")
    return manifest | {"restored_counts": counts, "rollback": str(rollback_output or "")}


def _server_is_running() -> bool:
    host = str(CFG.get("host", "127.0.0.1"))
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(CFG.get("port", 8000))
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="AirPaint 数据库与图片备份/恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="在线一致性备份")
    backup.add_argument("--output", type=Path)
    verify = sub.add_parser("verify", help="校验备份")
    verify.add_argument("archive", type=Path)
    restore = sub.add_parser("restore", help="离线恢复")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    if args.command == "backup":
        output = args.output or (
            DATABASE_PATH.parent.parent / "backups" /
            f"airpaint-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        result = create_backup(output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        print(json.dumps(verify_backup(args.archive), ensure_ascii=False, indent=2))
        return 0
    if _server_is_running():
        raise SystemExit("AirPaint 仍在运行；请先正常停止服务，再执行恢复")
    result = restore_backup(args.archive, replace=args.replace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
