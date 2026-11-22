from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from app.media_archive_v173 import MediaArchive
from app.telemetry import TelemetryStore
from app.web_chat_conversations_v1 import WebChatConversationStoreV1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_web_chat_conversation_wal_and_shm_sidecars_reject_symlinks(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(store.path) + suffix)
        sidecar.unlink(missing_ok=True)
        target = tmp_path / f"outside{suffix}"
        target.write_text("must remain untouched", encoding="utf-8")
        sidecar.symlink_to(target)
        try:
            with pytest.raises(RuntimeError, match="symbolic links"):
                store._connect()
            assert target.read_text(encoding="utf-8") == "must remain untouched"
        finally:
            sidecar.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link assertions")
def test_web_chat_conversation_database_rejects_hard_links(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    linked = tmp_path / "linked-conversation-db.sqlite3"
    os.link(store.path, linked)
    with pytest.raises(RuntimeError, match="private regular files"):
        store._connect()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_media_archive_settings_reject_symlink_and_keep_outside_file_untouched(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-settings.enc"
    outside.write_bytes(b"outside settings must remain untouched")
    store.settings_path.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symbolic link"):
        store.public_config()
    assert outside.read_bytes() == b"outside settings must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertions")
def test_media_archive_rejects_symlinked_intermediate_object_directory(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-objects"
    outside.mkdir()
    (store.root / "objects").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="object directories"):
        store._read({}, "local", "objects/image.png")


def test_media_archive_settings_write_ignores_legacy_predictable_temp_path(tmp_path: Path) -> None:
    store = MediaArchive(tmp_path, "master")
    outside = tmp_path / "outside-temp"
    outside.write_bytes(b"must remain untouched")
    predictable_temp = store.settings_path.with_suffix(".tmp")
    try:
        predictable_temp.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported")
    configured = store.configure({"backend": "local"})
    assert configured["backend"] == "local"
    assert outside.read_bytes() == b"must remain untouched"
    assert store.settings_path.is_file()
    assert not store.settings_path.is_symlink()


def test_telemetry_load_uses_the_validated_open_descriptor(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "request_history.jsonl"
    path.write_text(
        '{"request_id":"req_descriptor_read","status":"completed","response_text":"ok"}\n',
        encoding="utf-8",
    )
    original_open = Path.open

    def reject_history_reopen(self, *args, **kwargs):
        if self == path:
            raise AssertionError("validated telemetry path must not be reopened")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_history_reopen)
    store = TelemetryStore(tmp_path)
    asyncio.run(store.load())
    assert store.get("req_descriptor_read")["response_text"] == "ok"
