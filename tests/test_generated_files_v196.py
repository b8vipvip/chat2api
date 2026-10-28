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
