from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _conversation(conversation_id: str, *, title: str = "跨设备会话") -> dict:
    return {
        "id": conversation_id,
        "title": title,
        "model": "gpt-5.6-sol",
        "apiKeyId": "",
        "createdAt": "2026-11-20T00:00:00Z",
        "updatedAt": "2026-11-20T00:00:01Z",
        "messages": [
            {
                "id": "message_user_0001",
                "role": "user",
                "content": "请记住这个会话",
                "createdAt": "2026-11-20T00:00:00Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "",
            },
            {
                "id": "message_assistant_01",
                "role": "assistant",
                "content": "已保存到服务端。",
                "createdAt": "2026-11-20T00:00:01Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "completed",
            },
        ],
    }


def test_conversation_store_persists_owner_scoped_rows_and_revision_tombstones(tmp_path: Path) -> None:
    store = WebChatConversationStoreV1(tmp_path)
    conversation = _conversation("c2a_persist_test_0001")
    saved, row = store.upsert("usr_owner", conversation, 0)
    assert saved is True
    assert row is not None
    assert row["revision"] == 1
    assert row["deleted"] is False

    # A new store instance models a process restart / another application worker.
    reopened = WebChatConversationStoreV1(tmp_path)
    owner_rows = reopened.list_for_user("usr_owner")
    assert len(owner_rows) == 1
    assert owner_rows[0]["messages"][1]["content"] == "已保存到服务端。"
    assert owner_rows[0]["revision"] == 1
    assert reopened.list_for_user("usr_other") == []

    stale_saved, latest = reopened.upsert("usr_owner", conversation, 0)
    assert stale_saved is False
    assert latest is not None and latest["revision"] == 1

    deleted, tombstone = reopened.delete("usr_owner", conversation["id"], 1)
    assert deleted is True
    assert tombstone is not None
    assert tombstone["deleted"] is True
    assert tombstone["revision"] == 2
    assert tombstone["messages"] == []
    assert reopened.list_for_user("usr_owner")[0]["deleted"] is True


def test_web_chat_conversation_api_auth_ownership_sync_and_conflicts(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-conversation-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        unauthenticated = client.get("/api/user/chat/conversations")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in unauthenticated.headers["vary"].split(",")}

        owner = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-owner@example.test", "password": "conversation-owner-password"},
        )
        assert owner.status_code == 200, owner.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        key = client.post(
            "/api/user/keys",
            headers={"Origin": "http://testserver"},
            json={"name": "conversation key"},
        )
        assert key.status_code == 200, key.text
        key_id = key.json()["key"]["key_id"]

        conversation_id = "c2a_sync_test_000001"
        conversation = _conversation(conversation_id)
        conversation["apiKeyId"] = key_id
        created = client.put(
            f"/api/user/chat/conversations/{conversation_id}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert created.status_code == 200, created.text
        assert created.json()["conversation"]["revision"] == 1
        assert created.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in created.headers["vary"].split(",")}

        # A stale browser tab cannot silently overwrite a newer server revision.
        stale = client.put(
            f"/api/user/chat/conversations/{conversation_id}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert stale.status_code == 409
        assert stale.json()["conversation"]["revision"] == 1

        updated_conversation = {**conversation, "title": "服务端最新标题"}
        updated = client.put(
            f"/api/user/chat/conversations/{conversation_id}",
            json={"expected_revision": 1, "conversation": updated_conversation},
        )
        assert updated.status_code == 200
        assert updated.json()["conversation"]["revision"] == 2

        # The owner boundary is the signed-in user, not a caller-supplied user ID.
        client.cookies.clear()
        other = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-other@example.test", "password": "conversation-other-password"},
        )
        assert other.status_code == 200, other.text
        assert client.get("/api/user/chat/conversations").json()["data"] == []
        cross_owner = client.put(
            f"/api/user/chat/conversations/{conversation_id}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert cross_owner.status_code == 404

        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        stale_delete = client.delete(
            f"/api/user/chat/conversations/{conversation_id}?expected_revision=1"
        )
        assert stale_delete.status_code == 409
        assert stale_delete.json()["conversation"]["revision"] == 2

        deleted = client.delete(
            f"/api/user/chat/conversations/{conversation_id}?expected_revision=2"
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["conversation"]["deleted"] is True
        assert deleted.json()["conversation"]["revision"] == 3
        listed = client.get("/api/user/chat/conversations")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["deleted"] is True


def test_conversation_api_rejects_foreign_api_key_and_invalid_pending_message(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-conversation-validation-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        first = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-key-owner@example.test", "password": "conversation-key-owner-password"},
        )
        assert first.status_code == 200
        owner_key = client.post("/api/user/keys", json={"name": "owner key"})
        assert owner_key.status_code == 200
        foreign_key_id = owner_key.json()["key"]["key_id"]

        client.cookies.clear()
        second = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-key-other@example.test", "password": "conversation-key-other-password"},
        )
        assert second.status_code == 200

        conversation = _conversation("c2a_foreign_key_0001")
        conversation["apiKeyId"] = foreign_key_id
        foreign_key = client.put(
            f"/api/user/chat/conversations/{conversation['id']}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert foreign_key.status_code == 404

        pending = _conversation("c2a_pending_msg_0001")
        pending["messages"] = [{
            "id": "message_pending_0001",
            "role": "assistant",
            "content": "",
            "createdAt": "2026-11-20T00:00:00Z",
            "requestId": "req_pendingmessage1234",
            "pending": True,
            "recoveryStatus": "streaming",
        }]
        accepted = client.put(
            f"/api/user/chat/conversations/{pending['id']}",
            json={"expected_revision": 0, "conversation": pending},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["conversation"]["messages"][0]["pending"] is True

