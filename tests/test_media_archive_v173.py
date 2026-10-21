import base64
import io
from pathlib import Path

import pytest
from PIL import Image
from app.media_archive_v173 import MediaArchive


def example_image() -> bytes:
    img = Image.new("RGB", (1254, 1254), (120, 190, 100))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_generated_image_is_locally_archived_with_distinct_thumbnail_and_opaque_url(tmp_path: Path):
    store = MediaArchive(tmp_path, "test-master-secret")
    original = example_image()
    encoded = base64.b64encode(original).decode()
    markdown = "开始\n![Generated image](data:image/png;base64," + encoded + ")\n结束"
    fixed, rows = store.rewrite_markdown(markdown, "key_1", "req_42", "https://chat2api.example")
    assert len(rows) == 1
    row = rows[0]
    assert "data:image" not in fixed
    assert "chat2api.example/api/media/" in fixed
    assert fixed.endswith("\n结束")
    assert row["backend"] == "local"
    assert row["url"] != row["thumbnail_url"]
    assert row["url"] in row["thumbnail_url"]
    assert row["bytes"] == len(original)
    assert store.public_config()["count"] == 1
    rec = store.list_items()[0]
    assert "token" not in rec and "secret" not in rec
    media_id, token = row["url"].split("/api/media/", 1)[1].split("/")
    stored, mime = store.load(media_id, token)
    assert stored == original and mime == "image/png"
    thumb, thumb_mime = store.load(media_id, token, thumbnail=True)
    assert thumb_mime == "image/webp" and len(thumb) < len(original)
    with Image.open(io.BytesIO(thumb)) as img:
        assert max(img.size) <= 384
    with pytest.raises(KeyError):
        store.load(media_id, "wrong")


def test_storage_config_secrets_are_encrypted_redacted_and_require_https(tmp_path: Path):
    store = MediaArchive(tmp_path, "master")
    config = store.configure({"backend": "local", "public_base_url": "https://chat2api.example"})
    assert config["backend"] == "local"
    with pytest.raises(ValueError):
        store.configure({"public_base_url": "http://unsafe.example"})
    with pytest.raises(ValueError):
        store.configure({"backend": "s3"})
    config = store.configure({"backend": "s3", "s3": {"bucket": "example",
        "region": "us-east-1", "access_key_id": "test-access", "secret_access_key": "extremely-secret"}})
    assert config["s3"]["credentials_configured"] is True
    assert "secret_access_key" not in config["s3"]
    assert b"extremely-secret" not in store.settings_path.read_bytes()
    assert store.settings_path.stat().st_mode & 0o777 == 0o600
    assert MediaArchive(tmp_path, "master").public_config()["backend"] == "s3"


def test_reject_non_image_and_corrupt_inline_data_without_external_network(tmp_path: Path):
    store = MediaArchive(tmp_path, "master")
    with pytest.raises(ValueError):
        store.rewrite_markdown("![file](data:image/png;base64,SGVsbG8=)",
                               "key", "req", "https://chat2api.example")
    assert store.public_config()["count"] == 0


def test_real_storage_healthcheck_for_local_and_fake_s3(tmp_path: Path, monkeypatch):
    store = MediaArchive(tmp_path, "master")
    assert store.test_backend() == {"ok": True, "backend": "local", "verified": "write-read"}
    store.configure({"backend": "s3", "s3": {"bucket": "bucket-1", "region": "us-east-1",
                    "access_key_id": "AK", "secret_access_key": "SK"}})

    class Body:
        def __init__(self, content):
            self.content = content
        def read(self):
            return self.content

    class FakeS3:
        def __init__(self):
            self.items = {}
        def put_object(self, **kw):
            assert kw["ServerSideEncryption"] == "AES256"
            self.items[kw["Key"]] = kw["Body"]
        def get_object(self, **kw):
            return {"Body": Body(self.items[kw["Key"]])}
        def delete_object(self, **kw):
            self.items.pop(kw["Key"], None)

    remote = FakeS3()
    monkeypatch.setattr(store, "_s3", lambda config: remote)
    assert store.test_backend() == {"ok": True, "backend": "s3", "verified": "write-read"}
    assert remote.items == {}, "temporary healthcheck object must be removed"
    row = store.store_image(example_image(), "image/png", "key-1", "req-2")
    payload, mime = store.load(row["id"], row["token"])
    assert payload.startswith(b"\x89PNG") and mime == "image/png"
    store.configure({"backend": "local"})
    payload, _ = store.load(row["id"], row["token"])
    assert payload.startswith(b"\x89PNG"), "existing remote items remain readable after default changes"


def test_chatgpt_cdn_image_is_copied_and_untrusted_remote_image_not_fetched(tmp_path: Path, monkeypatch):
    store = MediaArchive(tmp_path, "master")
    payload = example_image()
    requests_seen = []
    class Response:
        headers = {"content-type": "image/png", "content-length": str(len(payload))}
        def raise_for_status(self):
            return None
        def iter_bytes(self):
            yield payload[:100]
            yield payload[100:]
        def __enter__(self): return self
        def __exit__(self, *_): return False
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def stream(self, method, url, headers):
            assert method == "GET"
            requests_seen.append(url)
            return Response()
    monkeypatch.setattr("app.media_archive_v173.httpx.Client", Client)
    url = "https://files.oaiusercontent.com/generated/photo.png"
    fixed, media = store.rewrite_markdown(
        f"![Generated image]({url})", "client", "req", "https://chat2api.example")
    assert "files.oaiusercontent.com" not in fixed
    assert "chat2api.example/api/media/" in fixed
    assert len(media) == 1 and requests_seen == [url]
    dangerous = "https://files.oaiusercontent.com.evil.test/private"
    fixed, media = store.rewrite_markdown(
        f"![Some image]({dangerous})", "client", "req", "https://chat2api.example")
    assert fixed == f"![Some image]({dangerous})"
    assert media == []
    assert requests_seen == [url], "never fetch arbitrary URL targets"


def test_admin_delete_expires_download_token_and_removes_both_variants(tmp_path: Path):
    store = MediaArchive(tmp_path, "master")
    row = store.store_image(example_image(), "image/png", "admin", "req")
    assert store.delete(row["id"]) is True
    assert store.delete(row["id"]) is False
    assert store.list_items() == []
    with pytest.raises(KeyError):
        store.load(row["id"], row["token"])
