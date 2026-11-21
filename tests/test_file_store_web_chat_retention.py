from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from app.file_store import FileStore


def test_retention_deletes_only_expired_unreferenced_web_chat_uploads(tmp_path: Path) -> None:
    store = FileStore(tmp_path)

    async def create(filename: str, source: str):
        return await store.create(
            filename=filename,
            data_base64=base64.b64encode((filename + " bytes").encode()).decode("ascii"),
            mime_type="text/plain",
            owner_key_id="key-owner",
            purpose="assistants",
            source=source,
        )

    expired_web = asyncio.run(create("expired-web.txt", "web_chat"))
    current_web = asyncio.run(create("current-web.txt", "web_chat"))
    expired_api = asyncio.run(create("expired-api.txt", "api"))

    expired_web.created_at = "2000-01-01T00:00:00+00:00"
    expired_api.created_at = "2000-01-01T00:00:00+00:00"
    store.save_index()

    # A referenced file remains intact even when its upload is past retention.
    assert store.delete_expired_web_chat_files({expired_web.file_id}) == 0
    assert store.get(expired_web.file_id) is not None

    # Once the last conversation reference is released, only the marked
    # Web Chat upload is eligible. General API files remain user-managed.
    assert store.delete_expired_web_chat_files(set()) == 1
    assert store.get(expired_web.file_id) is None
    assert not (store.root / expired_web.path).exists()
    assert store.get(current_web.file_id) is not None
    assert store.get(expired_api.file_id) is not None

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    assert reopened.get(current_web.file_id).source == "web_chat"
    assert reopened.get(expired_api.file_id).source == "api"



def test_retention_keeps_failed_unlinks_indexed_for_next_pass(tmp_path: Path, monkeypatch) -> None:
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="locked-web-chat.txt",
        data_base64=base64.b64encode(b"locked").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key-owner",
        purpose="assistants",
        source="web_chat",
    ))
    item.created_at = "2000-01-01T00:00:00+00:00"
    store.save_index()
    target = store.root / item.path
    original_unlink = Path.unlink

    def fail_target_once(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("simulated Windows file lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_once)
    assert store.delete_expired_web_chat_files(set()) == 0
    assert store.get(item.file_id) is not None
    assert target.exists()

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    assert reopened.get(item.file_id) is not None

    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert reopened.delete_expired_web_chat_files(set()) == 1
    assert reopened.get(item.file_id) is None
    assert not target.exists()



def test_retention_index_failure_never_unlinks_content(tmp_path: Path, monkeypatch) -> None:
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="index-failure-web-chat.txt",
        data_base64=base64.b64encode(b"must survive index failure").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key-owner",
        purpose="assistants",
        source="web_chat",
    ))
    item.created_at = "2000-01-01T00:00:00+00:00"
    store.save_index()
    target = store.root / item.path

    def fail_save():
        raise OSError("simulated index disk failure")

    monkeypatch.setattr(store, "save_index", fail_save)
    with pytest.raises(OSError, match="simulated index disk failure"):
        store.delete_expired_web_chat_files(set())

    assert store.get(item.file_id) is item
    assert item.pending_delete is False
    assert target.read_bytes() == b"must survive index failure"

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    restored, payload = reopened.read(item.file_id)
    assert restored.pending_delete is False
    assert payload == b"must survive index failure"


def test_retention_recovers_when_final_index_commit_fails(tmp_path: Path, monkeypatch) -> None:
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="final-index-failure-web-chat.txt",
        data_base64=base64.b64encode(b"expired content").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key-owner",
        purpose="assistants",
        source="web_chat",
    ))
    item.created_at = "2000-01-01T00:00:00+00:00"
    store.save_index()
    target = store.root / item.path
    original_save = store.save_index
    calls = 0

    def fail_second_save():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated final index failure")
        original_save()

    monkeypatch.setattr(store, "save_index", fail_second_save)
    with pytest.raises(OSError, match="simulated final index failure"):
        store.delete_expired_web_chat_files(set())

    assert calls == 2
    assert store.get(item.file_id) is item
    assert item.pending_delete is True
    assert not target.exists()

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    restored = reopened.get(item.file_id)
    assert restored is not None and restored.pending_delete is True
    assert reopened.delete_expired_web_chat_files(set()) == 1
    assert reopened.get(item.file_id) is None



def test_retention_cancels_pending_delete_when_file_becomes_referenced(tmp_path: Path, monkeypatch) -> None:
    store = FileStore(tmp_path)
    item = asyncio.run(store.create(
        filename="referenced-again-web-chat.txt",
        data_base64=base64.b64encode(b"keep this attachment").decode("ascii"),
        mime_type="text/plain",
        owner_key_id="key-owner",
        purpose="assistants",
        source="web_chat",
    ))
    item.created_at = "2000-01-01T00:00:00+00:00"
    store.save_index()
    target = store.root / item.path
    original_unlink = Path.unlink

    def fail_target_once(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("simulated Windows file lock")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_once)
    assert store.delete_expired_web_chat_files(set()) == 0
    assert item.pending_delete is True
    assert target.exists()

    reopened = FileStore(tmp_path)
    asyncio.run(reopened.load())
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert reopened.delete_expired_web_chat_files({item.file_id}) == 0
    restored = reopened.get(item.file_id)
    assert restored is not None and restored.pending_delete is False
    assert target.read_bytes() == b"keep this attachment"
