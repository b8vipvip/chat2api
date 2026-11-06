"""Regression: an incomplete image archive cannot publish orphaned bytes/rows."""
from __future__ import annotations

import base64
import io
import sqlite3

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 20), (20, 100, 200)).save(output, format="PNG")
    return output.getvalue()


def _assert_empty(store: MediaArchive) -> None:
    with store._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    assert list((store.root / "objects").glob("*")) == []


def test_thumbnail_failure_cleans_written_original_and_partial_file(tmp_path, monkeypatch):
    store = MediaArchive(tmp_path, "master")
    original_write = store._write

    def fail_second_write(cfg, key, content, mime):
        if key.endswith("-thumb.webp"):
            partial = (store.root / key).with_suffix(".part")
            partial.write_bytes(b"partial")
            raise OSError("injected thumbnail failure")
        return original_write(cfg, key, content, mime)

    monkeypatch.setattr(store, "_write", fail_second_write)
    with pytest.raises(OSError, match="injected thumbnail failure"):
        store.store_image(_png(), "image/png", "key", "req")
    _assert_empty(store)
    monkeypatch.setattr(store, "_write", original_write)
    record = store.store_image(_png(), "image/png", "key", "req")
    assert store.load(record["id"], record["token"])[0] == _png()


def test_database_insert_failure_cleans_both_uploaded_images(tmp_path):
    store = MediaArchive(tmp_path, "master")
    with store._db() as conn:
        conn.execute("""CREATE TRIGGER reject_media BEFORE INSERT ON media
                     BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected insert failure"):
        store.store_image(_png(), "image/png", "key", "req")
    _assert_empty(store)


def test_multi_image_reply_failure_rolls_back_earlier_success(tmp_path):
    store = MediaArchive(tmp_path, "master")
    encoded = base64.b64encode(_png()).decode("ascii")
    markdown = (
        "first ![good](data:image/png;base64," + encoded + ")\n"
        "second ![bad](data:image/png;base64,SGVsbG8=)"
    )
    with pytest.raises(ValueError, match="could not be transferred"):
        store.rewrite_markdown(markdown, "key", "req", "https://site.example")
    _assert_empty(store)


def test_failed_remote_thumbnail_write_compensates_object_store(tmp_path, monkeypatch):
    store = MediaArchive(tmp_path, "master")
    cfg = {"backend": "s3", "auto_archive": True, "s3": {"bucket": "test-bucket"}}

    class FakeS3:
        def __init__(self):
            self.objects = {}
            self.deleted = []

        def put_object(self, **kwargs):
            if kwargs["Key"].endswith("-thumb.webp"):
                raise OSError("injected S3 thumbnail failure")
            self.objects[kwargs["Key"]] = kwargs["Body"]

        def delete_object(self, **kwargs):
            self.deleted.append(kwargs["Key"])
            self.objects.pop(kwargs["Key"], None)

    fake = FakeS3()
    monkeypatch.setattr(store, "_config", lambda: cfg)
    monkeypatch.setattr(store, "_s3", lambda _: fake)
    with pytest.raises(OSError, match="injected S3 thumbnail failure"):
        store.store_image(_png(), "image/png", "key", "req")
    assert fake.objects == {}
    assert len(fake.deleted) == 2
    with store._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
