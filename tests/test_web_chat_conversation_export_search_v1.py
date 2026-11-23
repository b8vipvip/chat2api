from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import Settings
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1
from app.web_chat_conversations_v1 import _read_bounded_request_body


def _conversation(conversation_id: str, title: str, message: str) -> dict:
    return {
        "id": conversation_id,
        "title": title,
        "model": "gpt-5.6-sol",
        "apiKeyId": "",
        "createdAt": "2026-11-21T00:00:00Z",
        "updatedAt": "2026-11-21T00:00:01Z",
        "messages": [
            {
                "id": "message_user_0001",
                "role": "user",
                "content": message,
                "createdAt": "2026-11-21T00:00:00Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "",
                "lastEventId": 0,
            },
            {
                "id": "message_assistant_01",
                "role": "assistant",
                "content": "已保存。",
                "createdAt": "2026-11-21T00:00:01Z",
                "requestId": "",
                "pending": False,
                "recoveryStatus": "completed",
                "lastEventId": 0,
            },
        ],
    }


def _client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(
        CHAT2API_API_KEY="web-chat-search-export-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    client = TestClient(app)
    client.headers.update({"Origin": "http://testserver"})
    return client


def _register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/user/register",
        headers={"Origin": "http://testserver"},
        json={"email": email, "password": "conversation-feature-password"},
    )
    assert response.status_code == 200, response.text
    return str(client.cookies.get(USER_SESSION_COOKIE))


def test_server_search_reaches_older_rows_and_treats_query_as_literal(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _register(client, "search-owner@example.test")
        for index in range(22):
            conversation = _conversation(
                f"c2a_search_{index:06d}",
                f"历史记录 {index}",
                "包含 server-search-needle 的内容",
            )
            if index == 0:
                conversation["messages"][0]["content"] = "literal 100%_match marker"
            saved = client.put(
                f"/api/user/chat/conversations/{conversation['id']}",
                json={"expected_revision": 0, "conversation": conversation},
            )
            assert saved.status_code == 200, saved.text

        search = client.get("/api/user/chat/conversations/search?q=server-search-needle&limit=50")
        assert search.status_code == 200, search.text
        rows = search.json()["data"]
        assert len(rows) == 21
        assert any(row["id"] == "c2a_search_000021" for row in rows)

        literal = client.get("/api/user/chat/conversations/search?q=100%25_match")
        assert literal.status_code == 200, literal.text
        assert [row["id"] for row in literal.json()["data"]] == ["c2a_search_000000"]

        deleted = client.delete(
            "/api/user/chat/conversations/c2a_search_000000?expected_revision=1"
        )
        assert deleted.status_code == 200
        assert client.get("/api/user/chat/conversations/search?q=100%25_match").json()["data"] == []
        assert client.get("/api/user/chat/conversations/search?q=").status_code == 422
        assert client.get("/api/user/chat/conversations/search?q=" + ("x" * 121)).status_code == 422


def test_json_export_import_is_owner_scoped_and_never_overwrites(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        owner_cookie = _register(client, "export-owner@example.test")
        conversation = _conversation(
            "c2a_export_source_0001", "可移植会话", "需要保留的内容",
        )
        conversation["messages"][1]["toolEvents"] = [{
            "toolName": "browser.search",
            "callId": "call-export-1",
            "resultId": "result-export-1",
            "status": "completed",
            "arguments": '{"query":"export"}',
            "output": "export result",
            "references": [{
                "id": "turn0search0",
                "url": "https://docs.python.org/3/",
                "title": "Python docs",
                "snippet": "Official docs",
            }],
        }]
        saved = client.put(
            f"/api/user/chat/conversations/{conversation['id']}",
            json={"expected_revision": 0, "conversation": conversation},
        )
        assert saved.status_code == 200, saved.text

        exported = client.get(
            f"/api/user/chat/conversations/{conversation['id']}/export"
        )
        assert exported.status_code == 200, exported.text
        assert exported.headers["cache-control"] == "private, no-store"
        assert "attachment;" in exported.headers["content-disposition"]
        payload = exported.json()
        assert payload["format"] == "chat2api.web-chat-conversation"
        assert payload["version"] == 1
        assert payload["conversation"]["id"] == conversation["id"]
        assert "revision" not in payload["conversation"]
        assert payload["conversation"]["messages"][1]["toolEvents"][0]["references"][0]["url"] == "https://docs.python.org/3/"

        imported_conversation = dict(payload["conversation"])
        imported_conversation["id"] = "c2a_export_imported_01"
        imported = client.post(
            "/api/user/chat/conversations/import",
            json={**payload, "conversation": imported_conversation},
        )
        assert imported.status_code == 201, imported.text
        restored = imported.json()["conversation"]
        assert restored["revision"] == 1
        assert restored["messages"][1]["toolEvents"][0]["output"] == "export result"

        duplicate = client.post(
            "/api/user/chat/conversations/import",
            json={**payload, "conversation": imported_conversation},
        )
        assert duplicate.status_code == 409

        client.cookies.clear()
        _register(client, "export-other@example.test")
        foreign_export = client.get(
            f"/api/user/chat/conversations/{conversation['id']}/export"
        )
        assert foreign_export.status_code == 404
        foreign_import = client.post(
            "/api/user/chat/conversations/import",
            json=payload,
        )
        assert foreign_import.status_code == 201

        client.cookies.clear()
        unauthenticated = client.post(
            "/api/user/chat/conversations/import",
            json=payload,
        )
        assert unauthenticated.status_code == 401


def test_json_import_rejects_unsupported_format_and_oversized_body(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _register(client, "import-validation@example.test")
        unsupported = client.post(
            "/api/user/chat/conversations/import",
            json={"format": "other", "version": 1, "conversation": {}},
        )
        assert unsupported.status_code == 422

        oversized = client.post(
            "/api/user/chat/conversations/import",
            content=json.dumps({"padding": "x" * (5 * 1024 * 1024 + 16 * 1024)}),
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413



def test_chunked_json_import_body_is_bounded_without_content_length() -> None:
    async def scenario() -> None:
        chunks = [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": False},
        ]

        async def receive():
            return chunks.pop(0) if chunks else {"type": "http.disconnect"}

        request = Request(
            {"type": "http", "method": "POST", "path": "/", "headers": []},
            receive,
        )
        with pytest.raises(HTTPException) as raised:
            await _read_bounded_request_body(request, 8)
        assert raised.value.status_code == 413

        valid_chunks = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]

        async def receive_valid():
            return valid_chunks.pop(0) if valid_chunks else {"type": "http.disconnect"}

        valid_request = Request(
            {"type": "http", "method": "POST", "path": "/", "headers": []},
            receive_valid,
        )
        assert await _read_bounded_request_body(valid_request, 8) == b"12345678"

    asyncio.run(scenario())



def test_conversation_mutations_require_same_origin_and_bound_put_body(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _register(client, "mutation-origin@example.test")
        conversation = _conversation(
            "c2a_origin_guard_01", "Origin guard", "same-origin mutation",
        )
        endpoint = f"/api/user/chat/conversations/{conversation['id']}"
        payload = {"expected_revision": 0, "conversation": conversation}

        missing_origin = client.put(endpoint, headers={"Origin": ""}, json=payload)
        assert missing_origin.status_code == 403
        cross_origin = client.put(
            endpoint, headers={"Origin": "https://attacker.example"}, json=payload,
        )
        assert cross_origin.status_code == 403

        oversized_payload = json.dumps({
            "expected_revision": 0,
            "conversation": {
                **conversation,
                "messages": [],
                "padding": "x" * (5 * 1024 * 1024 + 16 * 1024),
            },
        })
        oversized = client.put(
            endpoint,
            content=oversized_payload,
            headers={
                "Origin": "http://testserver",
                "Content-Type": "application/json",
            },
        )
        assert oversized.status_code == 413

        created = client.put(endpoint, json=payload)
        assert created.status_code == 200, created.text
        blocked_delete = client.delete(
            endpoint + "?expected_revision=1",
            headers={"Origin": "https://attacker.example"},
        )
        assert blocked_delete.status_code == 403
        still_present = client.get("/api/user/chat/conversations")
        assert any(row["id"] == conversation["id"] for row in still_present.json()["data"])

        import_payload = {
            "format": "chat2api.web-chat-conversation",
            "version": 1,
            "conversation": _conversation(
                "c2a_origin_import_01", "Import origin guard", "not imported cross-origin",
            ),
        }
        blocked_import = client.post(
            "/api/user/chat/conversations/import",
            headers={"Origin": "https://attacker.example"},
            json=import_payload,
        )
        assert blocked_import.status_code == 403
