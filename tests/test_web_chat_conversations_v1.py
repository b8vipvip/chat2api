from __future__ import annotations

import base64
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess

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
                "lastEventId": 0,
            },
            {
                "id": "message_assistant_01",
                "role": "assistant",
                "content": "已保存到服务端。",
                "createdAt": "2026-11-20T00:00:01Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "completed",
                "lastEventId": 0,
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
        conversation["messages"][1]["lastEventId"] = 42
        conversation["messages"][1]["references"] = [
            {
                "id": f"turn0search{index}",
                "url": f"https://docs{index}.python.org/reference/",
                "title": f"Python source {index}",
                "snippet": f"Reference snippet {index}",
            }
            for index in range(8)
        ] + [{"id": "unsafe", "url": "javascript:alert(1)", "title": "Unsafe"}]
        conversation["messages"][1]["toolEvents"] = [{
            "toolName": "browser.search", "callId": "call-123",
            "resultId": "result-123", "status": "completed",
            "arguments": '{"query":"Mode 1"}',
            "output": "Found two relevant pages.",
            "references": [{
                "id": "turn0search0",
                "url": "https://docs.python.org/3/library/",
                "title": "Python documentation",
                "snippet": "Official library reference",
            }],
        }]
        created = client.put(
            f"/api/user/chat/conversations/{conversation_id}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert created.status_code == 200, created.text
        assert created.json()["conversation"]["revision"] == 1
        assert created.json()["conversation"]["messages"][1]["toolEvents"][0]["toolName"] == "browser.search"
        assert created.json()["conversation"]["messages"][1]["toolEvents"][0]["output"] == "Found two relevant pages."
        assert created.json()["conversation"]["messages"][1]["toolEvents"][0]["references"][0]["id"] == "turn0search0"
        assert created.json()["conversation"]["messages"][1]["toolEvents"][0]["references"][0]["url"] == "https://docs.python.org/3/library/"
        assert created.json()["conversation"]["messages"][1]["lastEventId"] == 42
        persisted_references = created.json()["conversation"]["messages"][1]["references"]
        assert len(persisted_references) == 8
        assert persisted_references[0]["id"] == "turn0search0"
        assert persisted_references[-1]["id"] == "turn0search7"
        assert all(reference["url"].startswith("https://") for reference in persisted_references)
        assert created.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in created.headers["vary"].split(",")}

        # A tool-only assistant message is still a valid persisted activity record.
        tool_only_id = "c2a_toolonly_000001"
        tool_only = _conversation(tool_only_id)
        tool_only["apiKeyId"] = key_id
        tool_only["messages"][1]["content"] = ""
        tool_only["messages"][1]["toolEvents"] = conversation["messages"][1]["toolEvents"]
        tool_only_saved = client.put(
            f"/api/user/chat/conversations/{tool_only_id}",
            json={"expected_revision": 0, "conversation": tool_only},
        )
        assert tool_only_saved.status_code == 200, tool_only_saved.text
        assert tool_only_saved.json()["conversation"]["messages"][1]["toolEvents"][0]["status"] == "completed"

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
        listed_rows = listed.json()["data"]
        deleted_row = next(row for row in listed_rows if row["id"] == conversation_id)
        assert deleted_row["deleted"] is True
        assert any(row["id"] == tool_only_id and not row["deleted"] for row in listed_rows)


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




def test_browser_conversation_merge_conflict_resolution() -> None:
    node = shutil.which("node")
    if node is None:
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/web_chat_conversation_sync_v1.mjs"],
        cwd=root, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "conversation merge keeps remote messages" in result.stdout



def test_conversation_attachment_references_protect_files_until_last_cross_device_tombstone(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="web-chat-conversation-files-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        registered = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-file-owner@example.test", "password": "conversation-file-password"},
        )
        assert registered.status_code == 200, registered.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        key = client.post("/api/user/keys", json={"name": "conversation attachment key"})
        assert key.status_code == 200, key.text
        token = key.json()["token"]
        key_id = key.json()["key"]["key_id"]

        image = client.post(
            "/v1/files",
            headers={
                "Authorization": "Bearer " + token,
                "X-Chat2API-Source": "web-chat",
            },
            json={
                "filename": "diagram.png", "mime_type": "image/png",
                "data_base64": base64.b64encode(b"image bytes").decode("ascii"),
                "purpose": "vision",
            },
        )
        document = client.post(
            "/v1/files",
            headers={
                "Authorization": "Bearer " + token,
                "X-Chat2API-Source": "web-chat",
            },
            json={
                "filename": "notes.txt", "mime_type": "text/plain",
                "data_base64": base64.b64encode(b"document bytes").decode("ascii"),
                "purpose": "assistants",
            },
        )
        assert image.status_code == 200, image.text
        assert document.status_code == 200, document.text
        image_id, document_id = image.json()["id"], document.json()["id"]
        assert app.state.file_store.get(image_id).source == "web_chat"
        assert app.state.file_store.get(document_id).source == "web_chat"

        first = _conversation("c2a_file_refs_0001")
        first["apiKeyId"] = key_id
        first["messages"][0]["content"] = [
            {"type": "text", "text": "请查看附件"},
            {"type": "image_url", "image_url": {"url": image_id}},
            {
                "type": "file", "file_id": document_id,
                "filename": "spoofed.txt", "mime_type": "text/plain", "size": 1,
            },
        ]
        saved_first = client.put(
            f"/api/user/chat/conversations/{first['id']}",
            json={"expected_revision": 0, "conversation": first},
        )
        assert saved_first.status_code == 200, saved_first.text
        normalized_file = saved_first.json()["conversation"]["messages"][0]["content"][2]
        assert normalized_file["filename"] == "notes.txt"
        assert normalized_file["size"] == len(b"document bytes")

        second = _conversation("c2a_file_refs_0002")
        second["apiKeyId"] = key_id
        second["messages"][0]["content"] = [
            {"type": "text", "text": "复用同一个文件"},
            {
                "type": "file", "file_id": document_id,
                "filename": "notes.txt", "mime_type": "text/plain",
                "size": len(b"document bytes"),
            },
        ]
        saved_second = client.put(
            f"/api/user/chat/conversations/{second['id']}",
            json={"expected_revision": 0, "conversation": second},
        )
        assert saved_second.status_code == 200, saved_second.text

        store = app.state.web_chat_conversations
        assert store.file_reference_count(image_id) == 1
        assert store.file_reference_count(document_id) == 2
        assert store.referenced_file_ids() >= {image_id, document_id}

        # Another signed-in account cannot attach a file owned by the first.
        client.cookies.clear()
        other = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-file-other@example.test", "password": "conversation-file-other-password"},
        )
        assert other.status_code == 200, other.text
        foreign = _conversation("c2a_file_foreign_0001")
        foreign["messages"][0]["content"] = [
            {"type": "text", "text": "不能引用别人的文件"},
            {
                "type": "file", "file_id": document_id,
                "filename": "notes.txt", "mime_type": "text/plain",
                "size": len(b"document bytes"),
            },
        ]
        denied = client.put(
            f"/api/user/chat/conversations/{foreign['id']}",
            json={"expected_revision": 0, "conversation": foreign},
        )
        assert denied.status_code == 404

        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        blocked_api_delete = client.delete(
            f"/v1/files/{document_id}",
            headers={"Authorization": "Bearer " + token},
        )
        assert blocked_api_delete.status_code == 409
        blocked_session_delete = client.delete(
            f"/api/user/files/{document_id}",
            headers={"Origin": "http://testserver"},
        )
        assert blocked_session_delete.status_code == 409

        deleted_first = client.delete(
            f"/api/user/chat/conversations/{first['id']}?expected_revision=1"
        )
        assert deleted_first.status_code == 200, deleted_first.text
        assert deleted_first.json()["conversation"]["deleted"] is True
        assert store.file_reference_count(image_id) == 0
        assert store.file_reference_count(document_id) == 1
        synced = client.get("/api/user/chat/conversations")
        assert any(row["id"] == first["id"] and row["deleted"] for row in synced.json()["data"])
        assert client.delete(
            f"/api/user/files/{image_id}",
            headers={"Origin": "http://testserver"},
        ).status_code == 200

        deleted_second = client.delete(
            f"/api/user/chat/conversations/{second['id']}?expected_revision=1"
        )
        assert deleted_second.status_code == 200, deleted_second.text
        assert store.file_reference_count(document_id) == 0
        assert client.delete(
            f"/api/user/files/{document_id}",
            headers={"Origin": "http://testserver"},
        ).status_code == 200
        assert app.state.file_store.get(document_id) is None



def test_conversation_attachment_reference_migration_backfills_legacy_rows(tmp_path: Path) -> None:
    database = tmp_path / "web_chat_conversations_v1.sqlite3"
    file_id = "file_legacyattachment1234"
    first = _conversation("c2a_legacy_ref_0001")
    first["messages"][0]["content"] = [
        {"type": "text", "text": "legacy attachment"},
        {"type": "image_url", "image_url": {"url": file_id}},
        {"type": "image_url", "image_url": {"url": file_id}},
    ]
    second = _conversation("c2a_legacy_ref_0002")
    second["messages"][0]["content"] = [
        {"type": "text", "text": "shared legacy attachment"},
        {"type": "file", "file_id": file_id, "filename": "legacy.txt",
         "mime_type": "text/plain", "size": 10},
    ]
    deleted = _conversation("c2a_legacy_ref_0003")
    deleted["messages"][0]["content"] = [
        {"type": "text", "text": "already deleted"},
        {"type": "file", "file_id": "file_deletedlegacy1234",
         "filename": "gone.txt", "mime_type": "text/plain", "size": 4},
    ]

    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE web_chat_conversations (
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                PRIMARY KEY (user_id, conversation_id)
            )
        """)
        connection.execute("PRAGMA user_version=1")
        for conversation, is_deleted in ((first, 0), (second, 0), (deleted, 1)):
            connection.execute(
                """
                INSERT INTO web_chat_conversations
                    (user_id, conversation_id, revision, updated_at, deleted, payload)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (
                    "legacy-user", conversation["id"], "2026-11-20T00:00:00Z",
                    is_deleted, json.dumps(conversation),
                ),
            )

    migrated = WebChatConversationStoreV1(tmp_path)
    assert migrated.file_reference_count(file_id, "legacy-user") == 2
    assert migrated.referenced_file_ids() == {file_id}
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_conversation_tool_events_preserve_unknown_and_missing_status_as_unknown() -> None:
    from app.web_chat_conversations_v1 import _normalize_tool_events

    unknown = _normalize_tool_events([{
        "toolName": "browser.search",
        "callId": "call-unknown-1",
        "status": "unknown",
        "arguments": "",
        "output": "",
    }], "assistant")
    assert unknown[0]["status"] == "unknown"

    missing = _normalize_tool_events([{
        "toolName": "browser.search",
        "callId": "call-unknown-2",
        "arguments": "",
        "output": "",
    }], "assistant")
    assert missing[0]["status"] == "unknown"

    for invalid in ("success-ish", "mystery", "future_untrusted_state"):
        import pytest
        with pytest.raises(ValueError):
            _normalize_tool_events([{
                "toolName": "browser.search",
                "callId": "call-invalid-1",
                "status": invalid,
            }], "assistant")
