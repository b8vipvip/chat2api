"""Generated file archival, tenant isolation and same-origin download regression."""
import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.generated_files_v196 import GeneratedFileArchive, MAX_FILE_BYTES
from app.main import create_app


def handoff(name: str, mime: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"**文件已生成**\n\n![📎 {name}](data:{mime};base64,{encoded})"


def office_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<word/>")
    return out.getvalue()


@pytest.mark.parametrize("filename,mime,payload", [
    ("报告.pdf", "application/pdf", b"%PDF-1.7\nsample"),
    ("项目.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", office_zip()),
    ("数据.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", office_zip()),
    ("演示.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", office_zip()),
    ("data.csv", "text/csv", b"id,value\n1,2"),
    ("run.py", "text/x-python", b"print('hello')\n"),
    ("data.json", "application/json", b'{"ok": true}'),
    ("package.zip", "application/zip", office_zip()),
])
def test_store_and_rewrite_supported_generated_files(tmp_path, filename, mime, payload):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    original = handoff(filename, mime, payload)
    text, rows = files.rewrite_markdown(original, "key1", "req_1", "https://chat2api.example")
    assert len(rows) == 1
    assert text.startswith("**文件已生成**\n\n[📎 ")
    assert "data:" not in text
    assert rows[0]["url"].startswith("https://chat2api.example/api/generated-files/gen_")
    assert rows[0]["sha256"] == __import__("hashlib").sha256(payload).hexdigest()
    row = files.describe(rows[0]["id"], "key1")
    assert files.read(row["id"], row["token"]) == (payload, mime, filename)
    with pytest.raises(KeyError):
        files.describe(row["id"], "key2")


def test_unknown_unreadable_and_disguised_generated_files_fail_closed(tmp_path):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    bad = [
        handoff("attack.exe", "application/octet-stream", b"malware"),
        handoff("fake.pdf", "application/pdf", b"not a pdf"),
        handoff("fake.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"wrong"),
        handoff("oversize.pdf", "application/pdf", b"%PDF-" + b"a" * MAX_FILE_BYTES),
        "![📎 invalid](blob:https://chatgpt.com/uuid)",
    ]
    for source in bad:
        with pytest.raises(ValueError):
            files.rewrite_markdown(source, "key", "req", "https://example.com")
    assert files.rewrite_markdown("Normal [link](https://example.com)", "key", "req", "https://example.com") == (
        "Normal [link](https://example.com)", [])


def test_generated_files_require_owner_and_bearer_capability(tmp_path):
    app = create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    with TestClient(app) as client:
        owner, token = __import__("asyncio").run(app.state.api_keys.create("owner"))
        other, other_token = __import__("asyncio").run(app.state.api_keys.create("other"))
        record = app.state.generated_files.store(
            b"%PDF-1.4\nexample", "测试文档.pdf", "application/pdf", owner["key_id"], "req_file",
        )
        metadata = f"/v1/generated-files/{record['id']}"
        assert client.get(metadata).status_code == 401
        assert client.get(metadata, headers={"Authorization": "Bearer " + other_token}).status_code == 404
        response = client.get(metadata, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200
        info = response.json()
        assert "token" not in info
        assert info["filename"] == "测试文档.pdf"
        assert info["sha256"] == record["sha256"]
        url = info["url"]
        data = client.get(url)
        assert data.status_code == 200 and data.content.startswith(b"%PDF")
        assert data.headers["content-type"] == "application/octet-stream"
        assert data.headers["x-content-type-options"] == "nosniff"
        assert data.headers["content-disposition"].startswith("attachment; filename*=")
        assert "sandbox" in data.headers["content-security-policy"]
        assert client.get(url.replace(record["token"], "wrong")).status_code == 404
        assert client.get("/api/admin/media-storage/generated-files").status_code == 401
        assert client.get(
            "/api/admin/media-storage/generated-files",
            headers={"Authorization": "Bearer test-key"},
        ).json()["data"][0]["id"] == record["id"]


def test_p1_replayed_file_terminal_reuses_same_archive_url(tmp_path):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    src = handoff("报告.pdf", "application/pdf", b"%PDF-1.7\\nretry payload")
    text1, rows1 = files.rewrite_markdown(src, "owner", "request-A", "https://example.org")
    text2, rows2 = files.rewrite_markdown(src, "owner", "request-A", "https://example.org")
    assert text1 == text2
    assert rows1 == rows2
    assert rows1[0]["status"] == "archived"
    assert len(files.list_items()) == 1
    # No cross-request or cross-owner bearer-URL reuse.
    _, other_request = files.rewrite_markdown(src, "owner", "request-B", "https://example.org")
    _, other_owner = files.rewrite_markdown(src, "other", "request-A", "https://example.org")
    assert len({rows1[0]["id"], other_request[0]["id"], other_owner[0]["id"]}) == 3
    assert all("_created" not in row and "token" not in row for row in rows1)


def test_p1_invalid_last_file_does_not_write_first_three(tmp_path):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    valid = handoff("report.pdf", "application/pdf", b"%PDF-1.7\\nokay")
    invalid = handoff("bad.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        b"not a real Office file")
    with pytest.raises(ValueError, match="signature"):
        files.rewrite_markdown(valid + "\\n" + invalid, "owner", "req-atomic", "https://example.org")
    assert files.list_items() == []


def test_p1_second_store_failure_rolls_back_only_new_artifacts(tmp_path, monkeypatch):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    first = handoff("a.pdf", "application/pdf", b"%PDF-1.7\\nfirst")
    second = handoff("b.pdf", "application/pdf", b"%PDF-1.7\\nsecond")
    existing, rows = files.rewrite_markdown(first, "owner", "old", "https://example.org")
    original_write = files.storage._write
    def flaky_write(config, key, payload, mime):
        if payload.endswith(b"second"):
            raise OSError("simulated-backend-failure")
        return original_write(config, key, payload, mime)
    monkeypatch.setattr(files.storage, "_write", flaky_write)
    with pytest.raises(OSError, match="simulated-backend-failure"):
        files.rewrite_markdown(first + "\\n" + second,
            "owner", "req-rollback", "https://example.org")
    assert [row["id"] for row in files.list_items()] == [rows[0]["id"]]
    assert "data:" not in existing


def test_p1_parallel_terminal_retries_are_one_stable_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.media_archive_v173 import MediaArchive

    # Independent archive instances share SQLite, as happens with parallel
    # API request tasks/processes. No in-memory mutex may be required.
    archive = MediaArchive(tmp_path, "secret")
    stores = [GeneratedFileArchive(archive) for _ in range(8)]
    payload = b"%PDF-1.7\nparallel request bytes"
    def one(i):
        return stores[i].store(
            payload, "shared.pdf", "application/pdf", "api-owner", "req-replay")
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, range(8)))
    assert len({r["id"] for r in rows}) == 1
    assert len({r["token"] for r in rows}) == 1
    assert sum(r["_created"] for r in rows) == 1
    assert len(stores[0].list_items()) == 1
    assert len(list((archive.root / "objects").glob("gen_*"))) == 1


def test_p1_multi_file_rollback_removes_unindexed_partial_bytes(tmp_path, monkeypatch):
    from app.media_archive_v173 import MediaArchive
    archive = MediaArchive(tmp_path, "secret")
    files = GeneratedFileArchive(archive)
    original_write = archive._write
    source = (
        handoff("one.pdf", "application/pdf", b"%PDF-1.7\none")
        + "\n" + handoff("two.pdf", "application/pdf", b"%PDF-1.7\ntwo")
    )
    def fail_after_partial_write(cfg, key, data, mime):
        original_write(cfg, key, data, mime)
        if data.endswith(b"two"):
            raise RuntimeError("write-succeeded-but-ack-lost")
    monkeypatch.setattr(archive, "_write", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="ack-lost"):
        files.rewrite_markdown(source, "owner", "req-failed", "https://example.org")
    assert files.list_items() == []
    assert list((archive.root / "objects").glob("gen_*")) == []


def test_p1_mixed_valid_and_malformed_handoff_is_never_partially_published(tmp_path):
    from app.media_archive_v173 import MediaArchive
    files = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    good = handoff("one.pdf", "application/pdf", b"%PDF-1.7\none")
    bad = "![📎 bogus.pdf](data:application/pdf;base64,!!!!)"
    with pytest.raises(ValueError, match="malformed"):
        files.rewrite_markdown(good + "\n" + bad, "owner", "req-bad", "https://example.org")
    assert files.list_items() == []


def test_p1_simultaneous_two_file_batches_are_replayed_without_duplication(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.media_archive_v173 import MediaArchive
    archive = MediaArchive(tmp_path, "secret")
    stores = [GeneratedFileArchive(archive) for _ in range(4)]
    source = (handoff("a.pdf", "application/pdf", b"%PDF-1.7\nA")
              + "\n" + handoff("b.pdf", "application/pdf", b"%PDF-1.7\nB"))
    def one(i):
        return stores[i].rewrite_markdown(source, "owner", "same-request", "https://example.org")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, range(4)))
    assert len({pair[0] for pair in rows}) == 1
    assert all(len(pair[1]) == 2 for pair in rows)
    assert len(stores[0].list_items()) == 2


def test_p1_persisted_corrupt_bytes_never_download_as_success(tmp_path):
    from app.media_archive_v173 import MediaArchive
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    archive = app.state.generated_files
    record = archive.store(
        b"%PDF-1.7\noriginal content", "sample.pdf", "application/pdf",
        "owner", "request-integrity",
    )
    with archive._db() as conn:
        object_key = conn.execute(
            "SELECT object_key FROM generated_files WHERE id=?", (record["id"],)
        ).fetchone()["object_key"]
    filename = (archive.storage.root / object_key)
    filename.write_bytes(b"%PDF-1.7\ncorrupted but plausible")
    with pytest.raises(ValueError, match="integrity verification"):
        archive.read(record["id"], record["token"])
    with TestClient(app) as client:
        response = client.get(f"/api/generated-files/{record['id']}/{record['token']}")
        assert response.status_code == 503
        assert "storage integrity" in response.json()["detail"].lower()
        assert "corrupted" not in response.text
