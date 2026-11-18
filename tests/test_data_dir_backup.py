from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.data_dir_backup import BackupError, create_backup, restore_backup, verify_backup
from app.runtime_single_process_guard import SingleProcessRuntimeGuard


def make_data_dir(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "api_keys.json").write_text('{"version":1,"keys":[]}', encoding="utf-8")
    (root / "telemetry.jsonl").write_text('{"request_id":"req_safe","status":"completed"}\n', encoding="utf-8")
    (root / "opaque.bin").write_bytes(b"private-file-bytes")
    connection = sqlite3.connect(root / "users.sqlite3")
    connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO users(id) VALUES ('usr_test1234')")
    connection.commit()
    connection.close()


def test_backup_verify_and_restore_check_hashes_json_sqlite_and_binary(tmp_path: Path):
    source = tmp_path / "source-data"
    backup = tmp_path / "backup"
    target = tmp_path / "restored"
    make_data_dir(source)

    with pytest.raises(BackupError, match="acknowledgement"):
        create_backup(source, backup)

    created = create_backup(
        source, backup, acknowledge_plaintext_sensitive_data=True
    )
    assert created["ok"] is True
    assert created["file_count"] == 4
    assert not (backup / ".chat2api-runtime.lock").exists()
    before_verify = sorted(path.relative_to(backup).as_posix() for path in backup.rglob("*"))
    assert verify_backup(backup) == {
        "ok": True, "file_count": 4, "schema_version": 1
    }
    after_verify = sorted(path.relative_to(backup).as_posix() for path in backup.rglob("*"))
    assert after_verify == before_verify

    restored = restore_backup(
        backup, target, acknowledge_plaintext_sensitive_data=True
    )
    assert restored["ok"] is True
    assert restored["restored"] is True
    assert restored["file_count"] == 4
    for name in ("api_keys.json", "telemetry.jsonl", "opaque.bin", "users.sqlite3"):
        assert (target / name).read_bytes() == (source / name).read_bytes()
    assert (target / ".chat2api-runtime.lock").exists()


def test_backup_refuses_while_server_holds_data_directory_lock(tmp_path: Path):
    source = tmp_path / "source-data"
    make_data_dir(source)
    guard = SingleProcessRuntimeGuard(source)
    guard.acquire()
    try:
        with pytest.raises(BackupError, match="while another server process owns"):
            create_backup(
                source, tmp_path / "backup",
                acknowledge_plaintext_sensitive_data=True,
            )
    finally:
        guard.release()
    assert not (tmp_path / "backup").exists()


def test_backup_verification_detects_tampering_and_path_traversal(tmp_path: Path):
    source = tmp_path / "source-data"
    backup = tmp_path / "backup"
    make_data_dir(source)
    create_backup(source, backup, acknowledge_plaintext_sensitive_data=True)

    (backup / "opaque.bin").write_bytes(b"tampered")
    with pytest.raises(BackupError, match="checksum"):
        verify_backup(backup)

    manifest_path = backup / ".chat2api-backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="Unsafe backup path"):
        verify_backup(backup)


def test_backup_refuses_symlinked_source_and_restore_target(tmp_path: Path):
    source = tmp_path / "source-data"
    make_data_dir(source)
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source, target_is_directory=True)
    with pytest.raises(BackupError, match="symbolic links"):
        create_backup(
            source_link, tmp_path / "backup",
            acknowledge_plaintext_sensitive_data=True,
        )


def test_backup_refuses_symlinked_restore_target(tmp_path: Path):
    source = tmp_path / "source-data"
    make_data_dir(source)
    backup = tmp_path / "backup"
    create_backup(source, backup, acknowledge_plaintext_sensitive_data=True)
    target = tmp_path / "restore-link"
    target.symlink_to(tmp_path / "nonexistent-target", target_is_directory=True)
    with pytest.raises(BackupError, match="symbolic links"):
        restore_backup(
            backup, target, acknowledge_plaintext_sensitive_data=True
        )


def test_backup_manifest_rejects_non_scalar_schema_and_file_kind(tmp_path: Path):
    source = tmp_path / "source-data"
    backup = tmp_path / "backup"
    make_data_dir(source)
    create_backup(source, backup, acknowledge_plaintext_sensitive_data=True)
    manifest_path = backup / ".chat2api-backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest["schema_version"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="Unsupported"):
        verify_backup(backup)

    manifest["schema_version"] = 1
    manifest["files"][0]["kind"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="file kind"):
        verify_backup(backup)


def test_backup_refuses_invalid_json_and_sqlite_integrity_failure(tmp_path: Path):
    source = tmp_path / "source-data"
    source.mkdir()
    (source / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(BackupError, match="invalid JSON"):
        create_backup(
            source, tmp_path / "backup-json",
            acknowledge_plaintext_sensitive_data=True,
        )

    (source / "broken.json").unlink()
    connection = sqlite3.connect(source / "broken.sqlite3")
    connection.execute("CREATE TABLE sample (id INTEGER)")
    connection.commit()
    connection.close()
    # A truncated SQLite header must not be accepted as a verified backup.
    (source / "broken.sqlite3").write_bytes(b"SQLite format 3\x00")
    with pytest.raises(BackupError, match="SQLite"):
        create_backup(
            source, tmp_path / "backup-sqlite",
            acknowledge_plaintext_sensitive_data=True,
        )


def test_backup_rejects_file_count_over_manifest_cap_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source-data"
    destination = tmp_path / "backup"
    make_data_dir(source)
    monkeypatch.setattr("app.data_dir_backup.MAX_MANIFEST_FILES", 3)

    with pytest.raises(BackupError, match="backup limit"):
        create_backup(
            source, destination,
            acknowledge_plaintext_sensitive_data=True,
        )
    assert not destination.exists()
    assert (source / "api_keys.json").exists()
