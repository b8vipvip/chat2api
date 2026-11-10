"""Thumbnail maintenance must not claim repair on a wrong provider write."""
import io

from PIL import Image

from app.media_archive_v173 import MediaArchive


def picture():
    out = io.BytesIO()
    Image.new("RGB", (840, 520), (170, 65, 120)).save(out, "PNG")
    return out.getvalue()


def test_repair_rejects_same_length_corruption_and_retries_without_touching_original(
    tmp_path, monkeypatch,
):
    store = MediaArchive(tmp_path, "master")
    original = picture()
    item = store.store_image(original, "image/png", "owner", "req_repair_v325")
    original_token = item["token"]
    thumb_path = store.root / "objects" / f"{item['id']}-thumb.webp"
    thumb_path.unlink()

    saved_write = store._write
    def corrupt_thumb(cfg, key, data, mime):
        if key.endswith("-thumb.webp"):
            data = bytes([data[0] ^ 1]) + data[1:]
        return saved_write(cfg, key, data, mime)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", corrupt_thumb)
        failed = store.repair_thumbnails(limit=1)
    assert failed["scanned"] == 1
    assert failed["repaired"] == 0
    assert failed["failed"] == 1
    assert failed["failures"][0]["reason"] == "ValueError"
    assert store.load(item["id"], original_token)[0] == original
    assert store.describe(item["id"], "owner")["token"] == original_token

    # A later healthy provider re-upload can recover the exact same media
    # object. The stable original object and opaque download token survive.
    repaired = store.repair_thumbnails(limit=1)
    assert repaired["repaired"] == 1 and repaired["failed"] == 0
    thumbnail = store.load(item["id"], original_token, thumbnail=True)[0]
    with Image.open(io.BytesIO(thumbnail)) as image:
        image.verify()
        assert image.format == "WEBP"
    assert store.load(item["id"], original_token)[0] == original


def test_repair_readback_missing_after_successful_put_is_not_reported_complete(
    tmp_path, monkeypatch,
):
    store = MediaArchive(tmp_path, "master")
    raw = picture()
    item = store.store_image(raw, "image/png", "owner", "req_missing_v325")
    thumbnail = store.root / "objects" / f"{item['id']}-thumb.webp"
    thumbnail.unlink()
    read = store._read
    def missing_after_write(cfg, backend, key):
        if key.endswith("-thumb.webp"):
            raise FileNotFoundError("provider says missing")
        return read(cfg, backend, key)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_read", missing_after_write)
        result = store.repair_thumbnails(limit=1)
    assert result["repaired"] == 0 and result["failed"] == 1
    assert result["failures"][0]["reason"] == "FileNotFoundError"
    assert store.load(item["id"], item["token"])[0] == raw
    assert store.repair_thumbnails(limit=1)["repaired"] == 1
