"""Regression: mixed image/file replies are one fail-closed API presentation."""
from __future__ import annotations

import base64
import inspect
import io

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive
from app.generated_files_v196 import GeneratedFileArchive
from app.generated_file_deferred_v247 import DeferredGeneratedFiles
from app.mixed_media_archive_v280 import archive_mixed_reply


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), (20, 40, 60)).save(stream, "PNG")
    return stream.getvalue()


def picture() -> str:
    return "![picture](data:image/png;base64," + base64.b64encode(png()).decode() + ")"


def file_marker() -> str:
    payload = base64.b64encode(b'{"ok":true}').decode()
    return "![📎 result.json](data:application/json;base64," + payload + ")"


class FailingFiles:
    def __init__(self):
        self.calls = []

    def rewrite(self, text, owner, request_id, base_url):
        self.calls.append((owner, request_id))
        raise ValueError("invalid verified generated file")


class UnexpectedFileMarker:
    def rewrite(self, text, owner, request_id, base_url):
        return text + " ![📎 malformed]", []


def _empty(store):
    assert store.public_config()["count"] == 0
    assert not list((store.root / "objects").glob("*"))


def test_file_validation_failure_rolls_back_previously_archived_images(tmp_path):
    store = MediaArchive(tmp_path, "master")
    files = FailingFiles()
    with pytest.raises(ValueError, match="invalid verified generated file"):
        archive_mixed_reply(
            "text\n" + picture() + "\n" + file_marker(),
            "api-key-A", "req-mixed-A", "https://public.example",
            media_archive=store, deferred_generated_files=files,
        )
    assert files.calls == [("api-key-A", "req-mixed-A")]
    _empty(store)


def test_failed_mixed_reply_never_deletes_previous_or_foreign_media(tmp_path):
    store = MediaArchive(tmp_path, "master")
    previous = store.store_image(png(), "image/png", "api-key-A", "req-earlier")
    foreign = store.store_image(png(), "image/png", "api-key-B", "req-foreign")
    with pytest.raises(ValueError, match="invalid verified"):
        archive_mixed_reply(
            picture() + "\n" + file_marker(), "api-key-A", "req-new",
            "https://public.example", media_archive=store,
            deferred_generated_files=FailingFiles(),
        )
    assert store.public_config()["count"] == 2
    assert store.load(previous["id"], previous["token"])[0] == png()
    assert store.load(foreign["id"], foreign["token"])[0] == png()


def test_partial_image_marker_rejected_before_creating_pending_file_jobs(tmp_path):
    store = MediaArchive(tmp_path, "master")
    files = FailingFiles()
    with pytest.raises(ValueError, match="Incomplete generated attachment"):
        archive_mixed_reply(
            picture() + "\nunfinished data:image/png;base64,A\n" + file_marker(),
            "owner", "req-invalid", "https://public.example",
            media_archive=store, deferred_generated_files=files,
        )
    assert files.calls == []
    _empty(store)


def test_bad_file_marker_after_file_rewrite_does_not_publish_image(tmp_path):
    store = MediaArchive(tmp_path, "master")
    with pytest.raises(ValueError, match="Incomplete generated attachment"):
        archive_mixed_reply(
            picture(), "owner", "req-leftover", "https://public.example",
            media_archive=store, deferred_generated_files=UnexpectedFileMarker(),
        )
    _empty(store)


def test_successful_mixed_reply_preserves_original_image_and_real_file_handoff(tmp_path):
    store = MediaArchive(tmp_path, "master")
    generated = GeneratedFileArchive(store)
    deferred = DeferredGeneratedFiles(generated)
    try:
        result, media, files = archive_mixed_reply(
            "first\n" + picture() + "\n" + file_marker() + "\nlast",
            "owner", "req-success", "https://public.example",
            media_archive=store, deferred_generated_files=deferred,
        )
        assert result.startswith("first\n") and result.endswith("\nlast")
        assert "data:image" not in result and "data:application/json" not in result
        assert len(media) == len(files) == 1
        assert "/api/media/" in result and "/api/generated-files/pending/" in result
        assert store.public_config()["count"] == 1
        media_id, token = media[0]["url"].split("/api/media/")[1].split("/")
        raw, mime = store.load(media_id, token)
        assert raw == png() and mime == "image/png"
        deferred.pool.shutdown(wait=True)
        pending_id, pending_token = files[0]["url"].rsplit("/", 2)[-2:]
        assert deferred.status(pending_id, pending_token)["status"] == "ready"
        content, mime, filename = deferred.read(pending_id, pending_token)
        assert content == b'{"ok":true}'
        assert filename == "result.json"
    finally:
        deferred.pool.shutdown(wait=True)


def test_formal_terminal_path_calls_cross_media_fail_closed_helper():
    from app.main import create_app
    source = inspect.getsource(create_app)
    assert "return archive_mixed_reply(" in source
    assert "async def archive_verified_terminal(" in source
    assert "return await archive_once(" in source
