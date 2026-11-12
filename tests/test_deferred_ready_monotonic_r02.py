"""R-02: a stale generated-file worker cannot downgrade ready to failed."""
from __future__ import annotations

import base64
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app.generated_file_deferred_v247 import DeferredGeneratedFiles


class _Storage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _config(self):
        return {"auto_archive": True, "public_base_url": "https://example.test"}


class _ConcurrentReadyArchive:
    def __init__(self, root: Path) -> None:
        self.storage = _Storage(root)
        self.db_path = root / "generated_files.sqlite3"
        self.published = {}
        sqlite3.connect(self.db_path).close()

    @staticmethod
    def validate(filename, mime, data):
        assert filename == "result.json"
        assert mime == "application/json"
        return filename, mime

    def store(self, data, filename, mime, owner, request_id):
        # Another live worker/process commits a fully archived original
        # before this (stale) worker raises its independent storage error.
        with sqlite3.connect(self.db_path) as conn:
            pending_id = conn.execute(
                "SELECT id FROM deferred_generated_files WHERE request_id=?",
                (request_id,),
            ).fetchone()[0]
            self.published["gen_other"] = (data, mime, filename)
            conn.execute(
                """UPDATE deferred_generated_files
                   SET state='ready', file_id='gen_other',
                       file_token='download_other'
                   WHERE id=? AND state='processing'""",
                (pending_id,),
            )
        raise RuntimeError("stale worker lost race after other process committed ready")

    def read(self, file_id, token):
        assert (file_id, token) == ("gen_other", "download_other")
        return self.published[file_id]


def test_failed_stale_worker_does_not_overwrite_other_process_ready_commit(tmp_path) -> None:
    archive = _ConcurrentReadyArchive(tmp_path)
    deferred = DeferredGeneratedFiles(archive)
    data = b'{"correct":true}'
    marker = "![📎 result.json](data:application/json;base64," + base64.b64encode(data).decode() + ")"
    _, entries = deferred.rewrite(
        marker, "owner-A", "req_r02_ready_race", "https://example.test"
    )
    deferred.pool.shutdown(wait=True)
    pending_id, token = entries[0]["url"].rsplit("/", 2)[-2:]
    status = deferred.status(pending_id, token)
    assert status["status"] == "ready"
    assert status["sha256"] == hashlib.sha256(data).hexdigest()
    assert deferred.read(pending_id, token)[0] == data
    with sqlite3.connect(archive.db_path) as conn:
        state = conn.execute(
            "SELECT state FROM deferred_generated_files WHERE id=?", (pending_id,)
        ).fetchone()[0]
    assert state == "ready"
