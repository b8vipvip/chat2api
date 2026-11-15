"""FileStore index integrity and transactional deletion regressions."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable on Windows")
def test_uploaded_files_and_index_are_private_to_the_server_account(tmp_path):
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="private.txt",
        data_base64=base64.b64encode(b"private-content").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key_private",
        purpose="assistants",
    ))
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert (store.root / item.path).stat().st_mode & 0o777 == 0o600
    assert store.index_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks are unavailable on Windows")
def test_file_store_rejects_symlinked_index_and_preserves_target(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    target = tmp_path / "external-index.json"
    original = b'{"files":[]}'
    target.write_bytes(original)
    (root / "index.json").symlink_to(target)
    with pytest.raises(RuntimeError, match="symbolic link"):
        asyncio.run(FileStore(tmp_path).load())
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links are not portable on Windows")
def test_file_store_rejects_hardlinked_index_without_changing_target(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    target = tmp_path / "external-index.json"
    original = b'{"files":[]}'
    target.write_bytes(original)
    (root / "index.json").hardlink_to(target)
    with pytest.raises(RuntimeError, match="refusing to discard file metadata"):
        asyncio.run(FileStore(tmp_path).load())
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links are not portable on Windows")
def test_file_store_read_rejects_hardlinked_content(tmp_path):
    store = FileStore(tmp_path)
    item = _create(store)
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"important file bytes")
    stored_path = store.root / item.path
    stored_path.unlink()
    os.link(secret, stored_path)
    with pytest.raises(FileNotFoundError, match="original regular file"):
        store.read(item.file_id)
    assert secret.read_bytes() == b"important file bytes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX named pipes are unavailable on Windows")
def test_file_store_read_rejects_fifo_swapped_after_startup(tmp_path):
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="owned.txt",
        data_base64=base64.b64encode(b"owned-content").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key_owner",
        purpose="assistants",
    ))
    stored_path = store.root / item.path
    stored_path.unlink()
    os.mkfifo(stored_path)
    with pytest.raises(FileNotFoundError, match="regular file"):
        store.read(item.file_id)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks are unavailable on Windows")
def test_file_store_read_rejects_symlink_swapped_after_startup(tmp_path):
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="owned.txt",
        data_base64=base64.b64encode(b"owned-content").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key_owner",
        purpose="assistants",
    ))
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"must-not-be-served")
    stored_path = store.root / item.path
    stored_path.unlink()
    stored_path.symlink_to(secret)
    with pytest.raises(FileNotFoundError, match="regular file"):
        store.read(item.file_id)
    assert secret.read_bytes() == b"must-not-be-served"


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


def test_delete_unlink_failure_does_not_resurrect_committed_catalog_row(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    item = _create(store)
    file_path = store.root / item.path
    original_unlink = Path.unlink

    def fail_content_unlink(path, *args, **kwargs):
        if path == file_path:
            raise PermissionError("simulated content cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_content_unlink)
    asyncio.run(store.delete(item.file_id, owner_key_id="key_owner_123"))

    assert store.get(item.file_id) is None
    assert file_path.read_bytes() == b"important file bytes"
    persisted = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert all(row["file_id"] != item.file_id for row in persisted["files"])

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    assert reopened.get(item.file_id) is None
    with pytest.raises(KeyError, match="Unknown file_id"):
        reopened.read(item.file_id)


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




def test_legacy_file_index_migrates_content_digest_without_losing_file(tmp_path):
    store = FileStore(tmp_path)
    item = _create(store)
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    for row in index["files"]:
        row.pop("sha256", None)
    store.index_path.write_text(json.dumps(index), encoding="utf-8")

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    restored, payload = reopened.read(item.file_id)

    assert restored.sha256 == hashlib.sha256(payload).hexdigest()
    persisted = json.loads(reopened.index_path.read_text(encoding="utf-8"))
    assert persisted["files"][0]["sha256"] == restored.sha256



def test_file_store_read_rejects_same_size_content_tampering(tmp_path):
    store = FileStore(tmp_path)
    item = _create(store)
    stored_path = store.root / item.path
    assert stored_path.stat().st_size == item.size

    # Keep the file length unchanged so size-only integrity checks cannot
    # detect the modified bytes.
    stored_path.write_bytes(b"x" * item.size)

    with pytest.raises(FileNotFoundError, match="digest"):
        store.read(item.file_id)
