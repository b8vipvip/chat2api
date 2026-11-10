"""A thumbnail rebuild cannot trust already-modified original bytes.

Only a byte-exact source from the owner/request's persisted original digest
can be used to generate the replacement preview. No Worker terminal is changed.
"""
import io

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive


def png():
    out = io.BytesIO()
    Image.new("RGB", (315, 180), (143, 52, 76)).save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize("mode", ["same-length-substitution", "truncated"])
def test_corrupt_original_prevents_thumbnail_reconstruction(tmp_path, monkeypatch, mode):
    store = MediaArchive(tmp_path, "master")
    original = png()
    rec = store.store_image(original, "image/png", "user", "req_source_v326")
    thumbnail = store.root / "objects" / f"{rec['id']}-thumb.webp"
    thumbnail.unlink()
    actual_read = store._read
    writes = []
    actual_write = store._write

    def source_corrupted(cfg, backend, key):
        data = actual_read(cfg, backend, key)
        if key.endswith(".png"):
            return (bytes([data[0] ^ 1]) + data[1:]
                    if mode == "same-length-substitution" else data[:-1])
        return data

    def track_write(cfg, key, data, mime):
        writes.append(key)
        return actual_write(cfg, key, data, mime)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_read", source_corrupted)
        patch.setattr(store, "_write", track_write)
        report = store.repair_thumbnails(limit=1)

    assert report["scanned"] == 1
    assert report["repaired"] == 0 and report["failed"] == 1
    assert report["failures"][0]["reason"] == "ValueError"
    assert not writes, "untrusted original must not cause any replacement write"
    assert not thumbnail.exists()
    assert store.load(rec["id"], rec["token"])[0] == original

    recovery = store.repair_thumbnails(limit=1)
    assert recovery["repaired"] == 1 and recovery["failed"] == 0


def test_sha_less_legacy_rebuild_checks_size_without_claiming_new_digest(tmp_path):
    store = MediaArchive(tmp_path, "master")
    raw = png()
    rec = store.store_image(raw, "image/png", "owner", "req_legacy_v326")
    thumb = store.root / "objects" / f"{rec['id']}-thumb.webp"
    with store._db() as conn:
        conn.execute("UPDATE media SET sha256='' WHERE id=?", (rec["id"],))
    thumb.unlink()
    assert store.repair_thumbnails(limit=1)["repaired"] == 1
    with store._db() as conn:
        row = conn.execute("SELECT sha256 FROM media WHERE id=?", (rec["id"],)).fetchone()
    assert row["sha256"] == "", "do not invent historical source SHA"


def test_sha_less_legacy_original_size_mismatch_still_blocks_rebuild(tmp_path, monkeypatch):
    store = MediaArchive(tmp_path, "master")
    raw = png()
    rec = store.store_image(raw, "image/png", "owner", "req_old_size_v326")
    thumb = store.root / "objects" / f"{rec['id']}-thumb.webp"
    with store._db() as conn:
        conn.execute("UPDATE media SET sha256='' WHERE id=?", (rec["id"],))
    thumb.unlink()
    before = store._read
    def shortened(cfg, backend, key):
        data = before(cfg, backend, key)
        return data[:-1] if key.endswith(".png") else data
    with monkeypatch.context() as patch:
        patch.setattr(store, "_read", shortened)
        assert store.repair_thumbnails(limit=1)["failed"] == 1
    assert not thumb.exists()
