from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_conversations_v1 import WebChatConversationStoreV1
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _install_web_chat_test_features(app) -> None:
    install_control_plane_v1(app)
    shared_key, _shared_token = asyncio.run(app.state.api_keys.create("Web Chat conversation test shared"))
    app.state.control_plane.update_client({"api_key_id": shared_key["key_id"]})
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)


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
    _install_web_chat_test_features(app)

    with TestClient(app) as client:
        client.headers.update({"Origin": "http://testserver"})
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
            "resultPreview": json.dumps({"tool":"github.list_pull_review_threads",
                "results":[{"thread_id":"PRRT_42","comments_next_cursor":"x"*3000}]}),
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
        assert created.json()["conversation"]["messages"][1]["toolEvents"][0]["resultPreview"] == conversation["messages"][1]["toolEvents"][0]["resultPreview"]
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

        # Long assistant responses survive server-side conversation persistence,
        # while user input remains bounded by the composer limit.
        long_reply_id = "c2a_longreply_000001"
        long_reply = _conversation(long_reply_id)
        long_reply["apiKeyId"] = key_id
        long_reply_text = "长回答内容" * 4000
        long_reply["messages"][1]["content"] = long_reply_text
        long_reply_saved = client.put(
            f"/api/user/chat/conversations/{long_reply_id}",
            json={"expected_revision": 0, "conversation": long_reply},
        )
        assert long_reply_saved.status_code == 200, long_reply_saved.text
        assert long_reply_saved.json()["conversation"]["messages"][1]["content"] == long_reply_text
        long_reply_search = client.get(
            "/api/user/chat/conversations/search", params={"q": "长回答内容"}
        )
        assert long_reply_search.status_code == 200, long_reply_search.text
        assert any(row["id"] == long_reply_id for row in long_reply_search.json()["data"])

        too_long_reply = _conversation("c2a_longreply_bad_0001")
        too_long_reply["apiKeyId"] = key_id
        too_long_reply["messages"][1]["content"] = "x" * 32001
        rejected_reply = client.put(
            f"/api/user/chat/conversations/{too_long_reply['id']}",
            json={"expected_revision": 0, "conversation": too_long_reply},
        )
        assert rejected_reply.status_code == 422

        too_long_user = _conversation("c2a_longuser_bad_0001")
        too_long_user["apiKeyId"] = key_id
        too_long_user["messages"][0]["content"] = "x" * 8001
        rejected_user = client.put(
            f"/api/user/chat/conversations/{too_long_user['id']}",
            json={"expected_revision": 0, "conversation": too_long_user},
        )
        assert rejected_user.status_code == 422

        # Server history retains up to 100 ordered messages; the browser's
        # smaller offline cache must not impose the server-side conversation cap.
        long_history_id = "c2a_history_100_0001"
        long_history = _conversation(long_history_id)
        long_history["apiKeyId"] = key_id
        long_history["messages"] = [
            {
                "id": f"message_history_{index:04d}",
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"历史消息 {index}",
                "createdAt": f"2026-11-20T00:{index // 60:02d}:{index % 60:02d}Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "",
                "lastEventId": 0,
            }
            for index in range(100)
        ]
        long_history_saved = client.put(
            f"/api/user/chat/conversations/{long_history_id}",
            json={"expected_revision": 0, "conversation": long_history},
        )
        assert long_history_saved.status_code == 200, long_history_saved.text
        assert len(long_history_saved.json()["conversation"]["messages"]) == 100
        assert long_history_saved.json()["conversation"]["messages"][0]["id"] == "message_history_0000"

        too_many_history = _conversation("c2a_history_101_0001")
        too_many_history["apiKeyId"] = key_id
        too_many_history["messages"] = [
            {
                "id": f"message_history_{index:04d}",
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"历史消息 {index}",
                "createdAt": "2026-11-20T00:00:00Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "",
                "lastEventId": 0,
            }
            for index in range(101)
        ]
        rejected_history = client.put(
            f"/api/user/chat/conversations/{too_many_history['id']}",
            json={"expected_revision": 0, "conversation": too_many_history},
        )
        assert rejected_history.status_code == 422

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
    _install_web_chat_test_features(app)

    with TestClient(app) as client:
        client.headers.update({"Origin": "http://testserver"})
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



def test_conversation_attachment_references_protect_files_until_last_cross_device_tombstone(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="web-chat-conversation-files-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    _install_web_chat_test_features(app)

    with TestClient(app) as client:
        client.headers.update({"Origin": "http://testserver"})
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
        first_page = client.get("/api/user/chat/library?limit=1")
        assert first_page.status_code == 200, first_page.text
        page = first_page.json()
        assert len(page["data"]) == 1 and page["next_cursor"]
        second_page = client.get("/api/user/chat/library", params={"limit": 1, "after": page["next_cursor"]}).json()
        assert second_page["next_cursor"] is None
        indexed = {item["id"]: item for item in page["data"] + second_page["data"]}
        assert set(indexed) == {image_id, document_id}
        assert indexed[document_id]["reference_count"] == 2
        assert indexed[document_id]["filename"] == "notes.txt"
        assert client.get("/api/user/chat/library?limit=101").status_code == 422

        # Another signed-in account cannot attach a file owned by the first.
        client.cookies.clear()
        other = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "conversation-file-other@example.test", "password": "conversation-file-other-password"},
        )
        assert other.status_code == 200, other.text
        assert client.get("/api/user/chat/library").json()["data"] == []
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
        assert client.get("/api/user/chat/library?scope=unreferenced").json()["data"] == []
        app.state.file_store.get(image_id).created_at = "2020-01-01T00:00:00+00:00"
        app.state.file_store.get(document_id).created_at = "2020-01-01T00:00:00+00:00"
        from contextlib import contextmanager
        guard_calls = []
        original_guard = store.file_reference_snapshot_guard

        @contextmanager
        def checked_reference_guard():
            # Save, delete, retention and library listing all take FileStore first.
            assert app.state.file_store.lock._is_owned()
            guard_calls.append(True)
            with original_guard() as referenced:
                yield referenced

        monkeypatch.setattr(store, "file_reference_snapshot_guard", checked_reference_guard)
        unused = client.get("/api/user/chat/library?scope=unreferenced").json()["data"]
        assert guard_calls == [True]
        assert [(item["id"], item["reference_count"]) for item in unused] == [(image_id, 0)]
        remaining = client.get("/api/user/chat/library").json()["data"]
        assert [(item["id"], item["reference_count"]) for item in remaining] == [(document_id, 1)]
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
        assert client.get("/api/user/chat/library").json()["data"] == []
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4


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


def test_stale_tab_account_header_cannot_write_to_replacement_session(tmp_path: Path):
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    _install_web_chat_test_features(app)
    with TestClient(app) as client:
        owner = client.post("/api/user/register", json={
            "email": "stale-tab-owner@example.test", "password": "stale-tab-password"
        })
        assert owner.status_code == 200, owner.text
        owner_id = owner.json()["user"]["user_id"]
        client.cookies.clear()
        other = client.post("/api/user/register", json={
            "email": "replacement-session@example.test", "password": "stale-tab-password"
        })
        assert other.status_code == 200, other.text
        headers = {"Origin": "http://testserver", "X-Chat2API-User-ID": owner_id}
        conversation = _conversation("c2a_stale_tab_owner_0001")
        base = "/api/user/chat/conversations"
        assert client.get(base, headers=headers).status_code == 409
        assert client.get(base + "/search?q=test", headers=headers).status_code == 409
        saved = client.put(base + "/" + conversation["id"], headers=headers, json={
            "expected_revision": 0, "conversation": conversation,
        })
        assert saved.status_code == 409, saved.text
        deleted = client.delete(base + "/" + conversation["id"] + "?expected_revision=0", headers=headers)
        assert deleted.status_code == 409, deleted.text
        assert client.get(base).json()["data"] == []


def test_delta_returns_only_changed_owner_rows_and_old_tombstones(tmp_path: Path):
    store = WebChatConversationStoreV1(tmp_path)
    old = _conversation("c2a_delta_old_0001")
    assert store.upsert("owner", old, 0)[0]
    for index in range(20):
        assert store.upsert("owner", _conversation(f"c2a_delta_new_{index:04d}"), 0)[0]
    assert store.upsert("other", _conversation("c2a_delta_foreign_0001"), 0)[0]
    known = {row["id"]: row["revision"] for row in store.delta_for_user("owner", {})}
    known[old["id"]] = 1
    assert store.delta_for_user("owner", known) == []
    assert store.delete("owner", old["id"], 1)[0]
    changed = store.delta_for_user("owner", known)
    assert len(changed) == 1
    assert changed[0]["id"] == old["id"] and changed[0]["deleted"]
    assert not any(row["id"] == old["id"] for row in store.delta_for_user("other", known))


def test_change_generation_is_durable_and_owner_scoped(tmp_path: Path):
    a = WebChatConversationStoreV1(tmp_path)
    b = WebChatConversationStoreV1(tmp_path)
    row = _conversation("c2a_changes_0001")
    assert a.change_generation("alice") == 0
    assert a.upsert("alice", row, 0)[0]
    assert b.change_generation("alice") == 1
    assert b.change_generation("bob") == 0
    assert not b.upsert("alice", row, 0)[0]
    assert a.change_generation("alice") == 1
    assert b.delete("alice", row["id"], 1)[0]
    assert WebChatConversationStoreV1(tmp_path).change_generation("alice") == 2
    assert b.change_generation("bob") == 0


def test_plugin_task_lease_is_fenced_across_processes_and_restart(tmp_path: Path, monkeypatch):
    import app.web_chat_conversations_v1 as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    a = WebChatConversationStoreV1(tmp_path)
    b = WebChatConversationStoreV1(tmp_path)
    cid, mid = "c2a_task_0001", "message_task_0001"
    assert a.plugin_task("alice", cid, mid, "claim", "holder_first")["granted"]
    assert b.plugin_task("alice", cid, mid, "status")["state"] == "running"
    assert not b.plugin_task("alice", cid, mid, "claim", "holder_second")["granted"]
    assert b.plugin_task("bob", cid, mid, "status")["state"] == "missing"
    now[0] += 100
    assert a.plugin_task("alice", cid, mid, "renew", "holder_first")["granted"]
    now[0] += 181
    assert b.plugin_task("alice", cid, mid, "status")["state"] == "interrupted"
    assert not a.plugin_task("alice", cid, mid, "renew", "holder_first")["granted"]
    assert not b.plugin_task("alice", cid, mid, "claim", "holder_second")["granted"]
    assert a.plugin_task("alice", cid, "message_task_0002", "claim", "holder_first")["granted"]
    assert b.plugin_task("alice", cid, "message_task_0002", "completed", "holder_first")["granted"]
    assert WebChatConversationStoreV1(tmp_path).plugin_task("alice", cid, "message_task_0002", "status")["state"] == "completed"

def test_plugin_checkpoint_survives_restart_and_is_fenced_and_deleted(tmp_path: Path, monkeypatch):
    import app.web_chat_conversations_v1 as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    a = WebChatConversationStoreV1(tmp_path)
    cid, mid = "c2a_checkpoint_0001", "message_assistant_01"
    assert a.upsert("alice", _conversation(cid), 0)[0]
    assert a.plugin_task("alice", cid, mid, "claim", "holder_first")["granted"]
    first = {"rounds": 1, "tool_calls": 0, "tool_events": []}
    evidence = {"toolName": "GitHub", "callId": "call_1", "resultId": "call_1_result",
                "status": "completed", "arguments": "{}", "output": "done",
                "resultPreview": '{"tool":"github.list_issues","results":[{"number":42}]}'}
    second = {"rounds": 1, "tool_calls": 1, "tool_events": [evidence]}
    assert a.plugin_task("alice", cid, mid, "checkpoint", "holder_first", first)["granted"]
    assert a.plugin_task("alice", cid, mid, "checkpoint", "holder_first", second)["granted"]
    b = WebChatConversationStoreV1(tmp_path)
    assert not b.plugin_task("alice", cid, mid, "checkpoint", "holder_second", second)["granted"]
    assert not b.plugin_task("alice", cid, mid, "checkpoint", "holder_first", first)["granted"]
    assert b.plugin_task("alice", cid, mid, "checkpoint", "holder_first", second)["granted"]
    assert "checkpoint" not in b.plugin_task("alice", cid, mid, "status")
    assert "checkpoint" not in b.plugin_task("bob", cid, mid, "status")
    now[0] += 181
    saved = b.plugin_task("alice", cid, mid, "status")
    assert saved["state"] == "interrupted"
    assert saved["checkpoint"] == second
    assert not a.plugin_task("alice", cid, mid, "checkpoint", "holder_first", second)["granted"]
    assert b.delete("alice", cid, 1)[0]
    assert b.plugin_task("alice", cid, mid, "status")["state"] == "deleted"
    with sqlite3.connect(b.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM web_chat_plugin_checkpoints").fetchone()[0] == 0

def test_generated_library_pagination_and_session_download_are_owner_scoped(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY="generated-library-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    _install_web_chat_test_features(app)
    with TestClient(app) as client:
        client.headers.update({"Origin": "http://testserver"})
        assert client.post("/api/user/register", json={
            "email": "generated-library-owner@example.test", "password": "generated-library-password",
        }).status_code == 200
        key = client.post("/api/user/keys", json={"name": "owner key"})
        assert key.status_code == 200, key.text
        owner_key = key.json()["key"]["key_id"]
        archive = app.state.generated_files
        first = archive.store(b"%PDF-1.7\nfirst", "one.pdf", "application/pdf", owner_key, "req_one")
        second = archive.store(b"%PDF-1.7\nsecond", "two.pdf", "application/pdf", owner_key, "req_two")
        foreign = archive.store(b"%PDF-1.7\nforeign", "foreign.pdf", "application/pdf", "foreign-key", "req_foreign")
        library = "/api/user/chat/library"
        response = client.get(library, params={"scope": "generated", "limit": 1})
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["data"]) == 1 and page["next_cursor"]
        next_page = client.get(library, params={
            "scope": "generated", "limit": 1, "after": page["next_cursor"],
        }).json()
        assert next_page["next_cursor"] is None
        rows = page["data"] + next_page["data"]
        assert {row["id"] for row in rows} == {first["id"], second["id"]}
        assert all(row["source"] == "generated" for row in rows)
        assert all("token" not in row and "url" not in row and "owner" not in row for row in rows)
        assert client.get(library, params={"scope": "generated", "after": "file_12345678"}).status_code == 422
        url = f"/api/user/chat/generated-files/{first['id']}/download"
        download = client.get(url)
        assert download.status_code == 200 and download.content == b"%PDF-1.7\nfirst"
        assert "attachment" in download.headers["content-disposition"]
        assert download.headers["cache-control"] == "private, no-store"
        assert client.get(f"/api/user/chat/generated-files/{foreign['id']}/download").status_code == 404
        conversation = _conversation("c2a_generated_api_ref")
        conversation["messages"][1]["content"] = f"[file](/api/user/chat/generated-files/{first['id']}/download)"
        owner_id = app.state.user_accounts.owner_for_key(owner_key)
        assert owner_id
        store = app.state.web_chat_conversations
        assert store.upsert(owner_id, conversation, 0)[0]
        rows = client.get(library, params={"scope": "generated"}).json()["data"]
        assert next(row for row in rows if row["id"] == first["id"])["reference_count"] == 1
        admin_url = f"/api/admin/media-storage/generated-files/{first['id']}"
        assert client.delete(admin_url, headers={"Authorization": "Bearer generated-library-master-key"}).status_code == 409
        assert client.get(url).status_code == 200
        assert store.delete(owner_id, conversation["id"], 1)[0]
        assert client.delete(admin_url, headers={"Authorization": "Bearer generated-library-master-key"}).status_code == 200
        assert client.get(url).status_code == 404
        client.cookies.clear()
        assert client.get(url).status_code == 401
        assert client.post("/api/user/register", json={
            "email": "generated-library-other@example.test", "password": "generated-library-password",
        }).status_code == 200
        assert client.get(library, params={"scope": "generated"}).json()["data"] == []
        assert client.get(f"/api/user/chat/generated-files/{second['id']}/download").status_code == 404


def test_generated_references_count_by_request_and_link_and_backfill(tmp_path: Path):
    store = WebChatConversationStoreV1(tmp_path)
    file_id = "gen_abcdefghijk123"
    request_id = "req_generated_12345"
    first = _conversation("c2a_generated_ref_01")
    first["messages"][1]["requestId"] = request_id
    first["messages"][1]["content"] = f"[file](/api/generated-files/{file_id}/opaque-token)"
    second = _conversation("c2a_generated_ref_02")
    second["messages"][1]["content"] = f"[file](/api/user/chat/generated-files/{file_id}/download)"
    assert store.upsert("alice", first, 0)[0]
    assert store.upsert("alice", second, 0)[0]
    assert store.upsert("bob", first, 0)[0]
    assert store.generated_reference_count("alice", file_id, request_id) == 2
    assert store.generated_reference_count("bob", file_id, request_id) == 1
    with store.generated_deletion_guard("alice", file_id, request_id) as allowed:
        assert not allowed
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE web_chat_generated_references")
        connection.execute("PRAGMA user_version=3")
    reopened = WebChatConversationStoreV1(tmp_path)
    assert reopened.generated_reference_count("alice", file_id, request_id) == 2
    assert reopened.delete("alice", first["id"], 1)[0]
    assert reopened.generated_reference_count("alice", file_id, request_id) == 1
    assert reopened.delete("alice", second["id"], 1)[0]
    with reopened.generated_deletion_guard("alice", file_id, request_id) as allowed:
        assert allowed
    assert reopened.generated_reference_count("bob", file_id, request_id) == 1


def test_plugin_takeover_only_expired_pending_tasks_and_fences_previous_holder(tmp_path: Path, monkeypatch):
    import app.web_chat_conversations_v1 as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    a = WebChatConversationStoreV1(tmp_path)
    b = WebChatConversationStoreV1(tmp_path)
    cid, mid = "c2a_takeover_0001", "message_assistant_01"
    conversation = _conversation(cid)
    conversation["messages"][1].update(pending=True, recoveryStatus="tool_planning")
    assert a.upsert("alice", conversation, 0)[0]
    checkpoint = {"rounds": 1, "tool_calls": 0, "tool_events": []}
    assert a.plugin_task("alice", cid, mid, "claim", "holder_first")["granted"]
    assert a.plugin_task("alice", cid, mid, "checkpoint", "holder_first", checkpoint)["granted"]
    assert not b.plugin_task("alice", cid, mid, "takeover", "holder_second")["granted"]
    now[0] += 181
    assert not b.plugin_task("bob", cid, mid, "takeover", "holder_second")["granted"]
    assert not b.plugin_task("alice", cid, mid, "takeover", "holder_first")["granted"]
    resumed = b.plugin_task("alice", cid, mid, "takeover", "holder_second")
    assert resumed == {"state": "running", "granted": True, "checkpoint": checkpoint}
    assert not a.plugin_task("alice", cid, mid, "renew", "holder_first")["granted"]
    assert not a.plugin_task("alice", cid, mid, "completed", "holder_first")["granted"]
    assert not a.plugin_task("alice", cid, mid, "takeover", "holder_third")["granted"]
    assert b.plugin_task("alice", cid, mid, "interrupted", "holder_second")["granted"]
    now[0] += 181
    assert not a.plugin_task("alice", cid, mid, "takeover", "holder_third")["granted"]


def test_plugin_takeover_retry_budget_survives_restart(tmp_path: Path, monkeypatch):
    import app.web_chat_conversations_v1 as module
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store = WebChatConversationStoreV1(tmp_path)
    cid, mid = "c2a_takeover_0002", "message_assistant_01"
    conversation = _conversation(cid)
    conversation["messages"][1].update(pending=True, recoveryStatus="tool_planning")
    assert store.upsert("alice", conversation, 0)[0]
    assert store.plugin_task("alice", cid, mid, "claim", "holder_first")["granted"]
    assert store.plugin_task("alice", cid, mid, "checkpoint", "holder_first",
        {"rounds": 1, "tool_calls": 0, "tool_events": []})["granted"]
    for attempt in range(3):
        now[0] += 181
        store = WebChatConversationStoreV1(tmp_path)
        assert store.plugin_task("alice", cid, mid, "takeover", f"holder_{attempt:04d}")["granted"]
    now[0] += 181
    assert not store.plugin_task("alice", cid, mid, "takeover", "holder_fourth")["granted"]
