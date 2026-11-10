"""Image originals and thumbnails must survive a byte-exact storage readback.

A successful local/S3 write call is not equivalent to proven downloadable
original image bytes. Do not publish a media row or URL for corrupt storage.
"""
import hashlib
import io

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive


def picture():
    output = io.BytesIO()
    Image.new("RGB", (173, 117), (104, 55, 210)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("victim", ["original", "thumbnail"])
@pytest.mark.parametrize("failure", ["same-length-corruption", "missing"])
def test_failed_image_readback_keeps_no_media_manifest_or_stale_objects(
    tmp_path, monkeypatch, victim, failure,
):
    store = MediaArchive(tmp_path, "test-secret")
    before_read = store._read
    observed = []

    def damaged_read(cfg, backend, key):
        data = before_read(cfg, backend, key)
        name = "thumbnail" if key.endswith("-thumb.webp") else "original"
        observed.append((backend, name))
        if name != victim:
            return data
        if failure == "missing":
            raise FileNotFoundError("provider object vanished before readback")
        return bytes([data[0] ^ 0x01]) + data[1:]

    monkeypatch.setattr(store, "_read", damaged_read)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.store_image(picture(), "image/png", "owner-one", "req_test_v323")
    assert ("local", victim) in observed
    assert store.list_items() == []
    assert list((store.root / "objects").glob("*")) == []


def test_storage_success_binds_verified_full_original_hash_not_thumbnail_hash(tmp_path):
    store = MediaArchive(tmp_path, "test-secret")
    raw = picture()
    record = store.store_image(raw, "image/png", "owner-one", "req_success_v323")
    original, original_type = store.load(record["id"], record["token"])
    thumbnail, thumb_type = store.load(record["id"], record["token"], thumbnail=True)
    assert original == raw and original_type == "image/png"
    assert thumbnail and thumbnail != raw and thumb_type == "image/webp"
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert len(store.list_items()) == 1


@pytest.mark.parametrize("victim", ["original", "thumbnail"])
def test_s3_provider_ack_does_not_certify_corrupt_original_bytes(
    tmp_path, monkeypatch, victim,
):
    store = MediaArchive(tmp_path, "test-secret")
    store.configure({"backend": "s3", "s3": {
        "bucket": "bucket", "region": "us-east-1",
        "access_key_id": "dummy-id", "secret_access_key": "dummy-key",
    }})
    objects = {}

    class Payload:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data

    class Storage:
        def put_object(self, **kw):
            data = kw["Body"]
            name = "thumbnail" if kw["Key"].endswith("-thumb.webp") else "original"
            if name == victim:
                # Simulate a provider returning success but storing bad bytes.
                data = bytes([data[0] ^ 1]) + data[1:]
            objects[kw["Key"]] = data
        def get_object(self, **kw):
            return {"Body": Payload(objects[kw["Key"]])}
        def delete_object(self, **kw):
            objects.pop(kw["Key"], None)

    fake = Storage()
    monkeypatch.setattr(store, "_s3", lambda cfg: fake)
    with pytest.raises(ValueError, match="readback integrity mismatch"):
        store.store_image(picture(), "image/png", "owner-one", "req_s3_v323")
    assert objects == {}, "unpublished original and thumbnail both rolled back"
    assert store.list_items() == []
