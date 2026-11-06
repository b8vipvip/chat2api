from __future__ import annotations

import base64
import sqlite3
import threading
from pathlib import Path

import pytest

from app.generated_file_deferred_v247 import (
    DeferredGeneratedFiles, FileStillProcessing, FileTransferFailed,
)


class FakeStorage:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _config(self):
        return {"auto_archive": True, "public_base_url": "https://example.test"}


class FakeArchive:
    def __init__(self, root: Path, gate: threading.Event, fail=False):
        self.storage = FakeStorage(root)
        self.db_path = root / "generated_files.sqlite3"
        self.gate = gate
        self.fail = fail
        self.published = {}
        sqlite3.connect(self.db_path).close()

    @staticmethod
    def validate(filename, mime, data):
        assert filename.endswith(".json") and mime == "application/json"
        assert data.startswith(b"{") and len(data) < 1024
        return filename, mime

    def store(self, data, filename, mime, owner, request_id):
        self.gate.wait(timeout=5)
        if self.fail:
            raise ValueError("simulated storage unavailability")
        file_id = "gen_ready"
        self.published[file_id] = (data, mime, filename)
        return {"id": file_id, "token": "strong_download_token"}

    def read(self, file_id, token):
        assert token == "strong_download_token"
        return self.published[file_id]


def _marker(data: bytes):
    return "![📎 result.json](data:application/json;base64," + base64.b64encode(data).decode() + ")"


def test_v247_text_is_returned_before_file_storage_unblocks(tmp_path):
    gate = threading.Event()
    archive = FakeArchive(tmp_path, gate)
    deferred = DeferredGeneratedFiles(archive)
    raw = b'{"ready":true}'
    text, files = deferred.rewrite("说明完成。\\n" + _marker(raw), "api-key-A", "req-A",
                                   "https://example.test")
    assert "说明完成。" in text
    assert len(files) == 1 and files[0]["status"] == "processing"
    assert "data:application/json" not in text
    assert "/api/generated-files/pending/pgen_" in text
    pending_id, token = files[0]["url"].split("/")[-2:]
    assert deferred.status(pending_id, token)["status"] in {"pending", "processing"}
    with pytest.raises(FileStillProcessing):
        deferred.read(pending_id, token)
    with pytest.raises(KeyError):
        deferred.read(pending_id, "invalid")
    gate.set()
    for future in list(deferred.pool._threads):
        future.join(timeout=5)
    meta = deferred.status(pending_id, token)
    assert meta["status"] == "ready"
    assert meta["filename"] == "result.json"
    assert meta["bytes"] == len(raw)
    import hashlib
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(KeyError):
        deferred.status(pending_id, "invalid")
    data, mime, name = deferred.read(pending_id, token)
    assert data == raw and mime == "application/json" and name == "result.json"
    assert not (deferred.root / (pending_id + ".bin")).exists()
    deferred.pool.shutdown(wait=True)


def test_v247_failed_backend_never_serves_fake_binary(tmp_path):
    gate = threading.Event()
    gate.set()
    archive = FakeArchive(tmp_path, gate, fail=True)
    deferred = DeferredGeneratedFiles(archive)
    _, files = deferred.rewrite(_marker(b'{"file":1}'), "api-key-B", "req-B",
                                "https://example.test")
    file_id, token = files[0]["url"].split("/")[-2:]
    for future in list(deferred.pool._threads):
        future.join(timeout=5)
    assert deferred.status(file_id, token)["status"] == "failed"
    with pytest.raises(FileTransferFailed):
        deferred.read(file_id, token)
    deferred.pool.shutdown(wait=True)


def test_v247_malformed_handoff_rejected_without_public_pending_link(tmp_path):
    archive = FakeArchive(tmp_path, threading.Event())
    deferred = DeferredGeneratedFiles(archive)
    with pytest.raises(ValueError):
        deferred.rewrite("![📎 malicious.json](https://external.invalid/asset)",
                         "api-key", "req-x", "https://example.test")
    with sqlite3.connect(archive.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM deferred_generated_files").fetchone()[0] == 0
    deferred.pool.shutdown(wait=True)


def test_deferred_manifest_status_is_owner_scoped_without_exposing_tokens(tmp_path):
    gate = threading.Event()
    archive = FakeArchive(tmp_path, gate)
    deferred = DeferredGeneratedFiles(archive)
    raw = b'{"manifest":true}'
    _, files = deferred.rewrite(_marker(raw), "key-A", "req-A", "https://example.test")
    original = files[0]
    status = deferred.list_for_request("req-A", "key-A")
    assert len(status) == 1
    assert status[0]["pending_id"] == original["id"]
    assert status[0]["bytes"] == len(raw)
    assert len(status[0]["sha256"]) == 64
    assert status[0]["status"] in {"pending", "processing"}
    assert deferred.list_for_request("req-A", "key-B") == []
    assert deferred.list_for_request("req-B", "key-A") == []
    assert "token" not in str(status)
    assert "url" not in str(status)
    gate.set()
    for thread in list(deferred.pool._threads):
        thread.join(timeout=5)
    assert deferred.list_for_request("req-A", "key-A")[0]["status"] == "ready"
    deferred.pool.shutdown(wait=True)


def _assert_no_partial_pending_files(deferred: DeferredGeneratedFiles, archive: FakeArchive) -> None:
    assert list(deferred.root.glob("pgen_*")) == []
    with sqlite3.connect(archive.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deferred_generated_files").fetchone()[0] == 0


def test_staged_write_failure_removes_part_file_before_sqlite_registration(tmp_path, monkeypatch):
    archive = FakeArchive(tmp_path, threading.Event())
    deferred = DeferredGeneratedFiles(archive)
    real_write = Path.write_bytes

    def fail_after_partial_write(self, data):
        if self.parent == deferred.root and self.suffix == ".part":
            real_write(self, b"partial")
            raise OSError("injected disk-full write")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_after_partial_write)
    with pytest.raises(OSError, match="disk-full"):
        deferred.rewrite(_marker(b'{"ok":true}'), "owner", "req-part", "https://example.test")
    _assert_no_partial_pending_files(deferred, archive)
    deferred.pool.shutdown(wait=True)


def test_staged_insert_failure_removes_renamed_bin_before_registration(tmp_path):
    archive = FakeArchive(tmp_path, threading.Event())
    deferred = DeferredGeneratedFiles(archive)
    with sqlite3.connect(archive.db_path) as conn:
        conn.execute("""CREATE TRIGGER fail_pending BEFORE INSERT ON deferred_generated_files
                     BEGIN SELECT RAISE(ABORT, 'injected pending insert'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected pending insert"):
        deferred.rewrite(_marker(b'{"ok":true}'), "owner", "req-db", "https://example.test")
    _assert_no_partial_pending_files(deferred, archive)
    deferred.pool.shutdown(wait=True)


def test_second_pending_file_failure_cleans_first_and_second_files(tmp_path):
    archive = FakeArchive(tmp_path, threading.Event())
    deferred = DeferredGeneratedFiles(archive)
    with sqlite3.connect(archive.db_path) as conn:
        conn.execute("""CREATE TRIGGER fail_second_pending BEFORE INSERT ON deferred_generated_files
                        WHEN NEW.filename = 'broken.json'
                        BEGIN SELECT RAISE(ABORT, 'injected second insert'); END""")
    first = _marker(b'{"first":1}')
    second = "![📎 broken.json](data:application/json;base64," + (
        base64.b64encode(b'{"second":2}').decode()) + ")"
    with pytest.raises(sqlite3.IntegrityError, match="injected second insert"):
        deferred.rewrite(first + "\\n" + second, "owner", "req-two", "https://example.test")
    _assert_no_partial_pending_files(deferred, archive)
    deferred.pool.shutdown(wait=True)


@pytest.mark.parametrize("fail_at", [1, 2])
def test_failed_background_submission_cannot_publish_rolled_back_file_batch(
    tmp_path, monkeypatch, fail_at,
):
    """An already accepted first job may not store bytes if second submit fails."""
    gate = threading.Event()
    gate.set()  # Make any incorrectly started _finish run immediately.
    archive = FakeArchive(tmp_path, gate)
    deferred = DeferredGeneratedFiles(archive)
    real_submit = deferred.pool.submit
    calls = 0

    def fail_submission(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise RuntimeError("injected executor shutdown during batch")
        return real_submit(*args, **kwargs)

    monkeypatch.setattr(deferred.pool, "submit", fail_submission)
    with pytest.raises(RuntimeError, match="executor shutdown"):
        deferred.rewrite(
            _marker(b'{"first":1}') + "\n" + _marker(b'{"second":2}'),
            "owner-A", "req-batch-abort", "https://example.test",
        )
    deferred.pool.shutdown(wait=True)
    _assert_no_partial_pending_files(deferred, archive)
    assert archive.published == {}, (
        "an accepted job may not outlive a failed batch and store orphaned bytes"
    )


def test_successful_background_batch_releases_all_jobs(tmp_path):
    gate = threading.Event()
    gate.set()
    archive = FakeArchive(tmp_path, gate)
    deferred = DeferredGeneratedFiles(archive)
    text, files = deferred.rewrite(
        _marker(b'{"first":1}') + "\n" + _marker(b'{"second":2}'),
        "owner-A", "req-batch-success", "https://example.test",
    )
    deferred.pool.shutdown(wait=True)
    assert len(files) == 2
    assert text.count("/api/generated-files/pending/") == 2
    assert all(
        deferred.status(*item["url"].rsplit("/", 2)[-2:])["status"] == "ready"
        for item in files
    )



def _real_deferred(tmp_path):
    from app.media_archive_v173 import MediaArchive
    from app.generated_files_v196 import GeneratedFileArchive

    media = MediaArchive(tmp_path, "master")
    generated = GeneratedFileArchive(media)
    return generated, DeferredGeneratedFiles(generated)


def test_ready_manifest_insert_error_discards_only_newly_written_file(tmp_path):
    generated, deferred = _real_deferred(tmp_path)
    with deferred._db() as conn:
        conn.execute("""CREATE TRIGGER fail_ready BEFORE UPDATE ON deferred_generated_files
                        WHEN NEW.state = 'ready'
                        BEGIN SELECT RAISE(ABORT, 'injected ready update failure'); END""")
    _, files = deferred.rewrite(
        _marker(b'{"new":true}'), "owner", "req-ready-fail", "https://example.test",
    )
    deferred.pool.shutdown(wait=True)
    fid, token = files[0]["url"].rsplit("/", 2)[-2:]
    assert deferred.status(fid, token)["status"] == "failed"
    with pytest.raises(FileTransferFailed):
        deferred.read(fid, token)
    assert generated.list_items() == [], "failed ready-commit cannot strand original bytes"
    assert not list((generated.storage.root / "objects").glob("gen_*"))


def test_failed_ready_commit_must_not_delete_deduplicated_existing_file(tmp_path):
    generated, deferred = _real_deferred(tmp_path)
    raw = b'{"existing":true}'
    previous = generated.store(raw, "result.json", "application/json", "owner", "req-same")
    assert previous["_created"] is True
    with deferred._db() as conn:
        conn.execute("""CREATE TRIGGER fail_ready BEFORE UPDATE ON deferred_generated_files
                        WHEN NEW.state = 'ready'
                        BEGIN SELECT RAISE(ABORT, 'injected ready update failure'); END""")
    _, files = deferred.rewrite(
        _marker(raw), "owner", "req-same", "https://example.test",
    )
    deferred.pool.shutdown(wait=True)
    fid, token = files[0]["url"].rsplit("/", 2)[-2:]
    assert deferred.status(fid, token)["status"] == "failed"
    assert generated.read(previous["id"], previous["token"])[0] == raw
    assert len(generated.list_items()) == 1


def test_staging_unlink_error_after_ready_does_not_make_download_fail(tmp_path, monkeypatch):
    generated, deferred = _real_deferred(tmp_path)
    original_unlink = Path.unlink

    def fail_pending_unlink(self, *args, **kwargs):
        if self.parent == deferred.root and self.suffix == ".bin":
            raise OSError("injected staging cleanup failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_pending_unlink)
    raw = b'{"ready":true}'
    _, files = deferred.rewrite(
        _marker(raw), "owner", "req-unlink-ready", "https://example.test",
    )
    deferred.pool.shutdown(wait=True)
    fid, token = files[0]["url"].rsplit("/", 2)[-2:]
    assert deferred.status(fid, token)["status"] == "ready"
    assert deferred.read(fid, token)[0] == raw
    assert len(generated.list_items()) == 1


def test_disappeared_pending_row_after_original_store_is_compensated(tmp_path, monkeypatch):
    generated, deferred = _real_deferred(tmp_path)
    real_store = generated.store

    def store_then_remove_manifest(*args, **kwargs):
        record = real_store(*args, **kwargs)
        with deferred._db() as conn:
            conn.execute(
                "DELETE FROM deferred_generated_files WHERE request_id=?",
                ("req-missing",),
            )
        return record

    monkeypatch.setattr(generated, "store", store_then_remove_manifest)
    _, files = deferred.rewrite(
        _marker(b'{"new":2}'), "owner", "req-missing", "https://example.test",
    )
    deferred.pool.shutdown(wait=True)
    assert generated.list_items() == []
    assert not list((generated.storage.root / "objects").glob("gen_*"))
    fid, token = files[0]["url"].rsplit("/", 2)[-2:]
    with pytest.raises(KeyError):
        deferred.status(fid, token)
