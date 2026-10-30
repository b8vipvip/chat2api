"""Regression for v0.22.252 7efd: five verified files are legal under byte caps."""
import base64
import io
import zipfile

import pytest

from app.generated_files_v196 import (
    GeneratedFileArchive, MAX_FILES_PER_RESPONSE, MAX_TOTAL_BYTES,
)
from app.generated_file_deferred_v247 import DeferredGeneratedFiles
from app.media_archive_v173 import MediaArchive


def _zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("[Content_Types].xml", "<Types/>")
        handle.writestr("xl/workbook.xml", "verified")
    return buffer.getvalue()


def _handoffs():
    payload = _zip()
    files = [
        ("Report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", payload),
        ("Slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", payload),
        ("Data.json", "application/json", b'{"ok":true}'),
        ("Notes.md", "text/markdown", b"# Notes"),
        ("Archive.zip", "application/zip", payload),
    ]
    return "\n".join(
        "![📎 " + name + "](data:" + mime + ";base64,"
        + base64.b64encode(data).decode("ascii") + ")"
        for name, mime, data in files
    )


def test_five_distinct_native_files_archive_without_changing_byte_limits(tmp_path):
    assert MAX_FILES_PER_RESPONSE >= 5
    assert MAX_TOTAL_BYTES == 24 * 1024 * 1024
    archive = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    text, records = archive.rewrite_markdown(_handoffs(), "owner", "req-7efd", "https://example.test")
    assert len(records) == 5
    assert "data:" not in text
    assert all(item["status"] == "archived" for item in records)
    assert {item["filename"] for item in records} == {
        "Report.xlsx", "Slides.pptx", "Data.json", "Notes.md", "Archive.zip"
    }
    for record in records:
        details = archive.describe(record["id"], "owner")
        raw, mime, filename = archive.read(record["id"], details["token"])
        assert len(raw) == details["size"] and filename == record["filename"]


def test_five_distinct_files_can_be_deferred_and_reach_ready(tmp_path):
    archive = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    pending = DeferredGeneratedFiles(archive)
    text, records = pending.rewrite(_handoffs(), "owner", "req-7efd", "https://example.test")
    assert len(records) == 5
    assert text.count("/api/generated-files/pending/") == 5
    pending.pool.shutdown(wait=True)
    for item in records:
        file_id, token = item["url"].split("/")[-2:]
        assert pending.status(file_id, token)["status"] == "ready"
        _, _, filename = pending.read(file_id, token)
        assert filename == item["filename"]


def test_generated_file_count_is_still_bounded_before_side_effects(tmp_path):
    archive = GeneratedFileArchive(MediaArchive(tmp_path, "secret"))
    source = "\n".join(_handoffs().splitlines() + [_handoffs().splitlines()[0]] * MAX_FILES_PER_RESPONSE)
    with pytest.raises(ValueError, match="Too many generated files"):
        archive.rewrite_markdown(source, "owner", "req-rejected", "https://example.test")
    assert archive.list_items() == []
    pending = DeferredGeneratedFiles(archive)
    with pytest.raises(ValueError, match="Too many generated files"):
        pending.rewrite(source, "owner", "req-rejected", "https://example.test")
    pending.pool.shutdown(wait=True)
