"""Generated files must be readable and SHA-identical before any ready URL.

Historical req_b38f14d had five 'ready' manifest records but no HTTP
downloaded-byte acceptance. These tests do not claim those files were corrupt;
they close a deterministic hole where a provider write ACK or a replayed
manifest could advertise bytes that the public downloader could never read.
"""
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.media_archive_v173 import MediaArchive
from app.generated_files_v196 import GeneratedFileArchive
from app.generated_file_deferred_v247 import (
    DeferredGeneratedFiles, FileTransferFailed,
)


def _archive(tmp_path):
    return GeneratedFileArchive(MediaArchive(tmp_path, "secret"))


def _row_path(files: GeneratedFileArchive, file_id: str) -> Path:
    with files._db() as conn:
        row = conn.execute(
            "SELECT object_key FROM generated_files WHERE id=?", (file_id,)
        ).fetchone()
    assert row is not None
    return files.storage.root / row["object_key"]


def test_replay_dedup_requires_matching_provider_bytes(tmp_path):
    files = _archive(tmp_path)
    raw = b"%PDF-1.7\noriginal version"
    prior = files.store(raw, "report.pdf", "application/pdf", "owner", "req-replay")
    replay = files.store(raw, "report.pdf", "application/pdf", "owner", "req-replay")
    assert replay["id"] == prior["id"]
    assert replay["token"] == prior["token"]
    assert replay["_created"] is False
    assert files.read(replay["id"], replay["token"])[0] == raw

    # A valid-looking header and IDENTICAL LENGTH still cannot fool SHA.
    dest = _row_path(files, prior["id"])
    changed = raw.replace(b"original", b"modified")
    assert len(changed) == len(raw)
    dest.write_bytes(changed)
    with pytest.raises(ValueError, match="readback integrity mismatch"):
        files.store(raw, "report.pdf", "application/pdf", "owner", "req-replay")
    assert len(files.list_items()) == 1
    assert dest.read_bytes() == changed  # never delete or rewrite prior URLs


def test_replay_missing_previous_object_never_returns_false_ready(tmp_path):
    files = _archive(tmp_path)
    raw = b'{"ready":true}'
    prior = files.store(raw, "result.json", "application/json", "owner", "req-missing")
    path = _row_path(files, prior["id"])
    path.unlink()
    with pytest.raises(ValueError, match="readback unavailable"):
        files.store(raw, "result.json", "application/json", "owner", "req-missing")
    assert len(files.list_items()) == 1
    assert not path.exists()


def test_first_write_ack_not_sufficient_when_provider_stores_truncated_bytes(
    tmp_path, monkeypatch,
):
    files = _archive(tmp_path)
    source = b'{"output":"complete"}'

    def lying_write(cfg, key, raw, mime):
        dest = files.storage.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw[:5])  # provider says OK but actually truncated

    monkeypatch.setattr(files.storage, "_write", lying_write)
    with pytest.raises(ValueError, match="storage readback integrity mismatch"):
        files.store(source, "result.json", "application/json", "owner", "req-new")
    assert files.list_items() == []
    assert list((files.storage.root / "objects").glob("gen_*")) == []


def test_first_write_readback_failure_rolls_back_new_object(tmp_path, monkeypatch):
    files = _archive(tmp_path)
    original_read = files.storage._read

    def unavailable(cfg, backend, key):
        if key.startswith("objects/"):
            raise FileNotFoundError("simulated delayed or lost object")
        return original_read(cfg, backend, key)

    monkeypatch.setattr(files.storage, "_read", unavailable)
    with pytest.raises(FileNotFoundError):
        files.store(b"%PDF-1.7\nnew file", "report.pdf", "application/pdf",
                    "owner", "req-not-readable")
    assert files.list_items() == []
    assert list((files.storage.root / "objects").glob("gen_*")) == []


def test_real_http_download_and_sha_verified_for_ready_file(tmp_path):
    import hashlib
    app = create_app(Settings(
        CHAT2API_API_KEY="test-key", CHAT2API_PAIRING_CODE="test-pair",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    raw = b'{"ready":true,"count":5}'
    with TestClient(app) as client:
        row = app.state.generated_files.store(
            raw, "result.json", "application/json", "owner", "req-http"
        )
        response = client.get(f"/api/generated-files/{row['id']}/{row['token']}")
        assert response.status_code == 200
        assert response.content == raw
        assert hashlib.sha256(response.content).hexdigest() == row["sha256"]
        assert len(response.content) == row["bytes"]


def test_deferred_writer_does_not_transition_to_ready_if_provider_lied(
    tmp_path, monkeypatch,
):
    import base64
    generated = _archive(tmp_path)
    deferred = DeferredGeneratedFiles(generated)
    raw = b'{"download":"must fail"}'

    def truncated_write(cfg, key, source, mime):
        dest = generated.storage.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source[:-1])

    monkeypatch.setattr(generated.storage, "_write", truncated_write)
    marker = "![📎 result.json](data:application/json;base64," + base64.b64encode(raw).decode() + ")"
    _, pending = deferred.rewrite(marker, "owner", "req-pending", "https://example.test")
    deferred.pool.shutdown(wait=True)
    pending_id, token = pending[0]["url"].split("/")[-2:]
    assert deferred.status(pending_id, token)["status"] == "failed"
    with pytest.raises(FileTransferFailed):
        deferred.read(pending_id, token)
    assert generated.list_items() == []
    assert list((generated.storage.root / "objects").glob("gen_*")) == []
    assert not (deferred.root / (pending_id + ".bin")).exists()
