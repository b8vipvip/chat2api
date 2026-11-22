from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _conversation(conversation_id: str, *, with_file: bool = False) -> dict:
    content: str | list[dict]
    if with_file:
        content = [
            {"type": "text", "text": "retention attachment"},
            {"type": "image_url", "image_url": {"url": "file_retention1234"}},
        ]
    else:
        content = "old retention marker"
    return {
        "id": conversation_id,
        "title": "retention test",
        "model": "gpt-5.6-sol",
        "apiKeyId": "",
        "createdAt": "2026-11-01T00:00:00Z",
        "updatedAt": "2026-11-01T00:00:01Z",
        "messages": [{
            "id": "message_user_0001",
            "role": "user",
            "content": content,
            "createdAt": "2026-11-01T00:00:00Z",
            "requestId": "",
            "pending": False,
            "recoveryStatus": "",
            "lastEventId": 0,
        }],
    }


def test_retention_defaults_disabled_and_expires_to_permanent_tombstone(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    assert store.retention_policy()["retention_days"] == 0
    conversation = _conversation("c2a_retention_test_0001")
    saved, row = store.upsert("usr_retention", conversation, 0)
    assert saved and row and row["revision"] == 1

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE web_chat_conversations SET updated_at=? WHERE user_id=? AND conversation_id=?",
            ("2026-01-01T00:00:00Z", "usr_retention", conversation["id"]),
        )
    assert store.expire_old_conversations() == 0
    assert store.get_for_user("usr_retention", conversation["id"]) is not None

    policy = store.set_retention_days(30)
    assert policy["retention_days"] == 30
    assert store.expire_old_conversations() == 1

    tombstone = store.list_for_user("usr_retention")[0]
    assert tombstone["deleted"] is True
    assert tombstone["revision"] == 2
    assert tombstone["messages"] == []
    assert store.get_for_user("usr_retention", conversation["id"]) is None
    assert store.search_for_user("usr_retention", "old retention marker") == []

    # Tombstones remain after a process restart so an offline browser cannot
    # silently resurrect the expired conversation.
    reopened = WebChatConversationStoreV1(tmp_path)
    assert reopened.list_for_user("usr_retention")[0]["deleted"] is True
    assert reopened.expire_old_conversations() == 0


def test_expiring_conversation_releases_attachment_reference_transactionally(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    (tmp_path / "attachment.bin").write_bytes(b"image")
    fake_file_store = SimpleNamespace(
        root=tmp_path,
        get=lambda file_id: SimpleNamespace(path="attachment.bin", size=5) if file_id == "file_retention1234" else None,
    )
    conversation = _conversation("c2a_retention_file_0001", with_file=True)
    saved, row = store.upsert("usr_retention", conversation, 0, fake_file_store)
    assert saved and row
    assert store.file_reference_count("file_retention1234", "usr_retention") == 1

    store.set_retention_days(30)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE web_chat_conversations SET updated_at=? WHERE user_id=? AND conversation_id=?",
            ("2026-01-01T00:00:00Z", "usr_retention", conversation["id"]),
        )
    assert store.expire_old_conversations() == 1
    assert store.file_reference_count("file_retention1234", "usr_retention") == 0


def test_admin_retention_api_is_master_key_protected_and_bounded(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="retention-admin-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        assert client.get("/api/admin/web-chat-retention").status_code == 401
        headers = {"Authorization": "Bearer retention-admin-master-key"}
        current = client.get("/api/admin/web-chat-retention", headers=headers)
        assert current.status_code == 200, current.text
        assert current.json()["retention_days"] == 0
        assert current.json()["tombstones_retained"] is True

        updated = client.put(
            "/api/admin/web-chat-retention",
            headers=headers,
            json={"retention_days": 90},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["retention_days"] == 90

        for invalid in (-1, 3651, True, "30"):
            rejected = client.put(
                "/api/admin/web-chat-retention",
                headers=headers,
                json={"retention_days": invalid},
            )
            assert rejected.status_code == 422

        page = client.get("/admin")
        assert page.status_code == 200
        assert 'id="webChatRetentionPanel"' in page.text
        assert 'id="webChatRetentionSave"' in page.text
