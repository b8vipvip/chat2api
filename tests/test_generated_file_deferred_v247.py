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
    assert deferred.status(pending_id, token)["status"] == "ready"
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
