"""Deterministic timeout signaling without sleeping for the production 180s."""
from __future__ import annotations

import base64
import inspect
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from app.archive_write_stop_v280 import ArchiveWriteStop, ArchiveWriteStopped
from app.media_archive_v173 import MediaArchive
from app.mixed_media_archive_v280 import archive_mixed_reply
from app.generated_files_v196 import GeneratedFileArchive
from app.generated_file_deferred_v247 import DeferredGeneratedFiles


def _picture() -> str:
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), (150, 60, 80)).save(stream, "PNG")
    return "![image](data:image/png;base64," + base64.b64encode(stream.getvalue()).decode() + ")"


class _NoFiles:
    def rewrite(self, text, owner, request_id, base_url, **kwargs):
        return text, []


def _objects(store):
    return list((store.root / "objects").glob("*"))


def test_stop_before_archive_start_writes_nothing(tmp_path):
    store, stop = MediaArchive(tmp_path, "secret"), ArchiveWriteStop()
    stop.stop()
    with pytest.raises(ArchiveWriteStopped):
        archive_mixed_reply(
            _picture(), "owner", "req-pre", "https://test.example",
            media_archive=store, deferred_generated_files=_NoFiles(), stop=stop,
        )
    assert store.public_config()["count"] == 0 and not _objects(store)


def test_stop_after_blocking_original_write_compensates_unpublished_bytes(tmp_path, monkeypatch):
    store, stop = MediaArchive(tmp_path, "secret"), ArchiveWriteStop()
    entered, release = threading.Event(), threading.Event()
    original_write = store._write

    def block_after_original(cfg, key, content, mime):
        original_write(cfg, key, content, mime)
        if not key.endswith("-thumb.webp"):
            entered.set()
            assert release.wait(3), "test must unblock storage worker"

    monkeypatch.setattr(store, "_write", block_after_original)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            archive_mixed_reply, _picture(), "owner", "req-block",
            "https://test.example", media_archive=store,
            deferred_generated_files=_NoFiles(), stop=stop,
        )
        assert entered.wait(3)
        stop.stop()  # Equivalent to the outer HTTP-independent 180s timeout.
        release.set()
        with pytest.raises(ArchiveWriteStopped):
            pending.result(3)
    assert store.public_config()["count"] == 0
    assert not _objects(store)


def test_stop_after_image_row_is_written_reclaims_that_row(tmp_path, monkeypatch):
    store, stop = MediaArchive(tmp_path, "secret"), ArchiveWriteStop()
    original_store = store.store_image

    def stop_after_new_media(*args, **kwargs):
        record = original_store(*args, **kwargs)
        stop.stop()
        return record

    monkeypatch.setattr(store, "store_image", stop_after_new_media)
    with pytest.raises(ArchiveWriteStopped):
        archive_mixed_reply(
            _picture(), "owner", "req-late", "https://test.example",
            media_archive=store, deferred_generated_files=_NoFiles(), stop=stop,
        )
    assert store.public_config()["count"] == 0 and not _objects(store)


def test_stop_during_file_staging_cleans_bin_before_workers_start(tmp_path, monkeypatch):
    store, stop = MediaArchive(tmp_path, "secret"), ArchiveWriteStop()
    generated = GeneratedFileArchive(store)
    deferred = DeferredGeneratedFiles(generated)
    original_replace = Path.replace

    def stop_after_pending_rename(self, target):
        result = original_replace(self, target)
        if self.parent == deferred.root and self.suffix == ".part":
            stop.stop()
        return result

    monkeypatch.setattr(Path, "replace", stop_after_pending_rename)
    data = base64.b64encode(b'{"file":true}').decode()
    handoff = "![📎 result.json](data:application/json;base64," + data + ")"
    try:
        with pytest.raises(ArchiveWriteStopped):
            deferred.rewrite(handoff, "owner", "req-file", "https://test.example", stop=stop)
        deferred.pool.shutdown(wait=True)
        assert not list(deferred.root.glob("pgen_*"))
        with deferred._db() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM deferred_generated_files"
            ).fetchone()[0] == 0
        assert generated.list_items() == []
    finally:
        deferred.pool.shutdown(wait=True)


def test_owner_task_signals_after_timeout_and_preserves_singleflight_contract():
    from app.main import create_app
    src = inspect.getsource(create_app)
    assert "stop = ArchiveWriteStop()" in src
    assert "stop.stop()" in src
    assert "server_finalization_cooperative_stop_signaled" in src
    assert "return await archive_once(" in src
    assert "timeout=180.0" in src
