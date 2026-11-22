from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import app.private_storage as private_storage
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.web_chat_event_journal_v1 import WebChatEventJournal


def _simulate_sqlite_sidecar_unlink(monkeypatch, sidecar: Path) -> dict[str, bool]:
    original = private_storage.open_private_regular_file
    state = {"triggered": False}

    def raced_open(path, flags, *, mode=0o600):
        fd = original(path, flags, mode=mode)
        if Path(path) == sidecar and not state["triggered"]:
            os.close(fd)
            sidecar.unlink()
            state["triggered"] = True
            raise RuntimeError("Private storage files must be single-link regular files")
        return fd

    monkeypatch.setattr(private_storage, "open_private_regular_file", raced_open)
    return state


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite sidecar race simulation")
def test_conversation_store_tolerates_transient_rollback_journal_unlink(tmp_path, monkeypatch):
    store = WebChatConversationStoreV1(tmp_path)
    sidecar = Path(str(store.path) + "-journal")
    sidecar.write_bytes(b"temporary SQLite journal")

    state = _simulate_sqlite_sidecar_unlink(monkeypatch, sidecar)
    store._secure_sqlite_files()

    assert state["triggered"] is True
    assert not sidecar.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite sidecar race simulation")
def test_event_journal_tolerates_transient_rollback_journal_unlink(tmp_path, monkeypatch):
    journal = WebChatEventJournal(tmp_path)
    asyncio.run(journal.open())
    sidecar = Path(str(journal.path) + "-journal")
    sidecar.write_bytes(b"temporary SQLite journal")

    state = _simulate_sqlite_sidecar_unlink(monkeypatch, sidecar)
    journal._secure_sqlite_files()

    assert state["triggered"] is True
    assert not sidecar.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink boundary")
def test_event_journal_rejects_symlinked_rollback_journal(tmp_path):
    journal = WebChatEventJournal(tmp_path)
    asyncio.run(journal.open())
    sidecar = Path(str(journal.path) + "-journal")
    target = tmp_path / "outside-journal"
    target.write_text("must remain untouched", encoding="utf-8")
    sidecar.symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic links"):
        journal._secure_sqlite_files()
    assert target.read_text(encoding="utf-8") == "must remain untouched"
