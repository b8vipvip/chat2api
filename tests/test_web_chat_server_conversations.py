"""W-02 persistent conversation contract, owner isolation, and concurrent writes."""
import base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_conversation_api import install_web_chat_conversations


ORIGIN = {"Origin": "http://testserver"}


def application(data_dir: Path):
    app = create_app(Settings(CHAT2API_API_KEY="web-chat-test-key", CHAT2API_DATA_DIR=str(data_dir)))
    install_user_console_v104_patch(app)
    install_web_chat_conversations(app)
    install_web_chat_conversations(app)  # installing twice must not duplicate routes
    return app


def register(client: TestClient, email: str):
    response = client.post("/api/user/register", json={"email": email, "password": "secure-password-123"})
    assert response.status_code == 200, response.text


def snapshot(*, revision: int = 0, mutation: str = "mutation_0001", title: str = "A chat"):
    return {
        "title": title, "model": "gpt-5.6-sol",
        "expected_revision": revision, "mutation_id": mutation,
        "messages": [
            {"id": "message_001", "role": "user", "content": "<script>alert(1)</script>", "createdAt": ""},
            {"id": "message_002", "role": "assistant", "content": "Hello", "createdAt": ""},
        ],
    }


def test_user_session_csrf_owner_isolation_and_revisions(tmp_path):
    app = application(tmp_path)
    path = "/api/user/chat/conversations/conversation_001"
    with TestClient(app) as alice, TestClient(app) as bob:
        unauthorized = alice.get(path)
        unauthorized_list = alice.get("/api/user/chat/conversations")
        assert unauthorized.status_code == 401
        assert unauthorized_list.status_code == 401
        for denied in (unauthorized, unauthorized_list):
            assert denied.headers["cache-control"] == "private, no-store"
            assert "cookie" in {value.strip().lower() for value in denied.headers["vary"].split(",")}
        register(alice, "alice@example.org")
        register(bob, "bob@example.org")
        missing_origin = alice.put(path, json=snapshot())
        cross_origin = alice.put(path, json=snapshot(), headers={"Origin": "https://evil.example"})
        assert missing_origin.status_code == 403
        assert cross_origin.status_code == 403
        for denied in (missing_origin, cross_origin):
            assert denied.headers["cache-control"] == "private, no-store"
            assert "cookie" in {value.strip().lower() for value in denied.headers["vary"].split(",")}
        first = alice.put(path, json=snapshot(), headers=ORIGIN)
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in first.headers["vary"].split(",")}
        assert first.json()["revision"] == 1
        assert first.json()["messages"][0]["content"] == "<script>alert(1)</script>"
        # A retried mutation returns the original revision, and reusing the key
        # with different content is forbidden even if the revision matches.
        assert alice.put(path, json=snapshot(), headers=ORIGIN).json()["revision"] == 1
        assert alice.put(path, json=snapshot(title="tampered"), headers=ORIGIN).status_code == 409
        hidden = bob.get(path)
        hidden_delete = bob.delete(path, params={"expected_revision": 1}, headers=ORIGIN)
        assert hidden.status_code == 404
        assert hidden_delete.status_code == 404
        for denied in (hidden, hidden_delete):
            assert denied.headers["cache-control"] == "private, no-store"
            assert "cookie" in {value.strip().lower() for value in denied.headers["vary"].split(",")}
        assert bob.get("/api/user/chat/conversations").json()["total"] == 0
        assert alice.get("/api/user/chat/conversations").json()["total"] == 1
        assert "messages" not in alice.get("/api/user/chat/conversations").json()["data"][0]

        stale_write = alice.put(path, json=snapshot(revision=0, mutation="mutation_0002"), headers=ORIGIN)
        assert stale_write.status_code == 409
        assert stale_write.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in stale_write.headers["vary"].split(",")}
        update = alice.put(path, json=snapshot(revision=1, mutation="mutation_0002", title="new"), headers=ORIGIN)
        assert update.status_code == 200 and update.json()["revision"] == 2
        assert alice.delete(path, params={"expected_revision": 1}, headers=ORIGIN).status_code == 409
        assert alice.delete(path, params={"expected_revision": 2}, headers=ORIGIN).status_code == 204
        assert alice.delete(path, params={"expected_revision": 2}, headers=ORIGIN).status_code == 204
        assert alice.get(path).status_code == 404
        assert alice.put(path, json=snapshot(revision=0, mutation="mutation_0003"), headers=ORIGIN).status_code == 409


def test_restart_persistence_and_validation(tmp_path):
    path = "/api/user/chat/conversations/conversation_002"
    with TestClient(application(tmp_path)) as client:
        register(client, "persist@example.org")
        response = client.put(path, json=snapshot(), headers=ORIGIN)
        assert response.status_code == 200, response.text
        cookies = dict(client.cookies)
        assert client.put(
            path, json={**snapshot(revision=1, mutation="mutation_0004"), "messages": [
                {"id": "message_003", "role": "user", "content": "x" * 8001}
            ]}, headers=ORIGIN,
        ).status_code == 422
        assert client.put(
            path, json={**snapshot(revision=1, mutation="mutation_0004"), "messages": [
                {"id": f"message_{i:04d}", "role": "user", "content": "x"} for i in range(31)
            ]}, headers=ORIGIN,
        ).status_code == 422
    with TestClient(application(tmp_path)) as client:
        client.cookies.update(cookies)
        restored = client.get(path)
        assert restored.status_code == 200 and restored.json()["revision"] == 1
        assert restored.json()["messages"][0]["content"] == "<script>alert(1)</script>"



def test_message_ids_must_be_unique_and_metadata_nonblank(tmp_path):
    app = application(tmp_path)
    path = "/api/user/chat/conversations/conversation_004"
    with TestClient(app) as client:
        register(client, "validation@example.org")
        duplicate = snapshot()
        duplicate["messages"] = [
            {"id": "message_same", "role": "user", "content": "one", "createdAt": ""},
            {"id": "message_same", "role": "assistant", "content": "two", "createdAt": ""},
        ]
        assert client.put(path, json=duplicate, headers=ORIGIN).status_code == 422
        assert client.put(path, json={**snapshot(), "title": "   "}, headers=ORIGIN).status_code == 422
        assert client.get("/api/user/chat/conversations").json()["total"] == 0


def test_owner_scoped_search_finds_titles_and_message_text_without_like_wildcards(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "search-alice@example.org")
        register(bob, "search-bob@example.org")
        first = snapshot(mutation="mutation_0005", title="Release notes")
        first["messages"] = [
            {"id": "message_search1", "role": "user", "content": "needle 100% complete", "createdAt": ""}
        ]
        second = snapshot(mutation="mutation_0006", title="Another chat")
        second["messages"] = [
            {"id": "message_search2", "role": "user", "content": "unrelated", "createdAt": ""}
        ]
        assert alice.put(
            "/api/user/chat/conversations/conversation_005", json=first, headers=ORIGIN
        ).status_code == 200
        assert alice.put(
            "/api/user/chat/conversations/conversation_006", json=second, headers=ORIGIN
        ).status_code == 200
        found = alice.get("/api/user/chat/conversations", params={"q": "NEEDLE 100%"}).json()
        assert found["total"] == 1
        assert found["data"][0]["id"] == "conversation_005"
        assert "messages" not in found["data"][0]
        assert bob.get(
            "/api/user/chat/conversations", params={"q": "NEEDLE 100%"}
        ).json()["total"] == 0
        assert alice.get(
            "/api/user/chat/conversations", params={"q": "x" * 121}
        ).status_code == 422


def test_structured_image_references_are_owner_scoped_and_round_trip(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "attachment-alice@example.org")
        register(bob, "attachment-bob@example.org")
        key = alice.post(
            "/api/user/keys", headers=ORIGIN, json={"name": "image owner"}
        )
        assert key.status_code == 200, key.text
        token = key.json()["token"]
        uploaded = alice.post(
            "/v1/files",
            headers={"Authorization": "Bearer " + token},
            json={
                "filename": "chart.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(b"fake-png-bytes").decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]
        message = {
            "id": "message_image01",
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this chart"},
                {"type": "image_url", "image_url": {"url": file_id}},
            ],
            "createdAt": "",
        }
        body = {
            **snapshot(),
            "messages": [message],
            "mutation_id": "mutation_image01",
        }
        path = "/api/user/chat/conversations/conversation_image01"
        saved = alice.put(path, json=body, headers=ORIGIN)
        assert saved.status_code == 200, saved.text
        assert saved.json()["messages"][0]["content"] == message["content"]

        foreign = bob.put(
            "/api/user/chat/conversations/conversation_image02",
            json={**body, "mutation_id": "mutation_image02"},
            headers=ORIGIN,
        )
        assert foreign.status_code == 404
        assert foreign.headers["cache-control"] == "private, no-store"

        remote = {
            **body,
            "mutation_id": "mutation_image03",
            "messages": [{
                **message,
                "id": "message_image02",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://evil.example/image.png"}}
                ],
            }],
        }
        assert alice.put(
            "/api/user/chat/conversations/conversation_image03",
            json=remote, headers=ORIGIN,
        ).status_code == 422

        assistant_image = {
            **body,
            "mutation_id": "mutation_image04",
            "messages": [{
                **message,
                "id": "message_image03",
                "role": "assistant",
            }],
        }
        assert alice.put(
            "/api/user/chat/conversations/conversation_image04",
            json=assistant_image, headers=ORIGIN,
        ).status_code == 422

def test_concurrent_expected_revision_only_one_writer_wins(tmp_path):
    app = application(tmp_path)
    store = app.state.web_chat_conversations
    user, conversation_id = "user_abcdef12", "conversation_003"
    store.put(user, conversation_id, title="base", model="m", messages=[], expected_revision=0,
              mutation_id="mutation_0001")

    def write(idx):
        try:
            return store.put(user, conversation_id, title=f"writer-{idx}", model="m", messages=[],
                             expected_revision=1, mutation_id=f"mutation_{idx:04d}")["revision"]
        except Exception as err:
            return type(err).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, (2, 3)))
    assert sorted(map(str, outcomes)) == ["2", "ConversationConflict"]
