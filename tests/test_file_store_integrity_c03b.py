"""FileStore index integrity and transactional deletion regressions."""
from __future__ import annotations

import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.file_store import FileStore


def _create(store: FileStore):
    return asyncio.run(store.create(
        filename="owned.txt",
        data_base64=base64.b64encode(b"important file bytes").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key_owner_123",
        purpose="assistants",
    ))


def test_concurrent_creates_serialize_index_snapshots_and_preserve_every_file(tmp_path):
    store = FileStore(tmp_path)

    def create(index: int):
        payload = f"concurrent-file-{index}".encode("ascii")
        return asyncio.run(store.create(
            filename=f"concurrent-{index}.txt",
            data_base64=base64.b64encode(payload).decode("ascii"),
            mime_type="text/plain",
            owner_key_id=f"key_owner_{index}",
            purpose="assistants",
        ))

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(create, range(16)))

    persisted = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert len(created) == 16
    assert len(store.items) == 16
    assert len(persisted["files"]) == 16

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    assert len(reopened.items) == 16
    for index, item in enumerate(created):
        restored, payload = reopened.read(item.file_id)
        assert restored.owner_key_id == f"key_owner_{index}"
        assert payload == f"concurrent-file-{index}".encode("ascii")


def test_delete_does_not_drop_metadata_when_file_rename_fails(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    item = _create(store)
    file_path = store.root / item.path
    original_replace = Path.replace

    def fail_source_rename(path, target):
        if path == file_path:
            raise PermissionError("simulated file deletion permission failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_source_rename)
    with pytest.raises(PermissionError, match="permission failure"):
        asyncio.run(store.delete(item.file_id, owner_key_id="key_owner_123"))

    assert store.get(item.file_id) is item
    assert file_path.read_bytes() == b"important file bytes"
    persisted = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert any(row["file_id"] == item.file_id for row in persisted["files"])


def test_delete_restores_file_and_metadata_when_index_commit_fails(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    item = _create(store)
    file_path = store.root / item.path
    original_index = store.index_path.read_bytes()

    def fail_index_save():
        raise OSError("simulated index volume failure")

    monkeypatch.setattr(store, "save_index", fail_index_save)
    with pytest.raises(OSError, match="index volume failure"):
        asyncio.run(store.delete(item.file_id, owner_key_id="key_owner_123"))

    assert store.get(item.file_id) is item
    assert file_path.read_bytes() == b"important file bytes"
    assert store.index_path.read_bytes() == original_index


def test_corrupt_file_index_fails_closed_and_rejects_path_traversal(tmp_path):
    store = FileStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    original = json.dumps({
        "files": [{
            "file_id": "file_unsafe123",
            "filename": "secret.txt",
            "mime_type": "text/plain",
            "size": 12,
            "created_at": "2026-11-15T00:00:00+00:00",
            "owner_key_id": "key_owner_123",
            "path": "../../outside.txt",
            "purpose": "assistants",
        }]
    }).encode("utf-8")
    store.index_path.write_bytes(original)

    with pytest.raises(RuntimeError, match="refusing to discard file metadata"):
        asyncio.run(store.load())

    assert store.index_path.read_bytes() == original
    assert store.items == {}



def test_create_removes_phantom_file_when_index_commit_fails(tmp_path, monkeypatch):
    store = FileStore(tmp_path)

    def fail_index_save():
        raise OSError("simulated file index write failure")

    monkeypatch.setattr(store, "save_index", fail_index_save)
    with pytest.raises(OSError, match="file index write failure"):
        _create(store)

    assert store.items == {}
    assert not list(store.root.glob("file_*"))
