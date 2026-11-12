"""W-02 persistent conversation contract, owner isolation, and concurrent writes."""
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
        assert alice.get(path).status_code == 401
        assert alice.get("/api/user/chat/conversations").status_code == 401
        register(alice, "alice@example.org")
        register(bob, "bob@example.org")
        assert alice.put(path, json=snapshot()).status_code == 403
        assert alice.put(path, json=snapshot(), headers={"Origin": "https://evil.example"}).status_code == 403
        first = alice.put(path, json=snapshot(), headers=ORIGIN)
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "private, no-store"
        assert first.headers["vary"] == "Cookie"
        assert first.json()["revision"] == 1
        assert first.json()["messages"][0]["content"] == "<script>alert(1)</script>"
        # A retried mutation returns the original revision, and reusing the key
        # with different content is forbidden even if the revision matches.
        assert alice.put(path, json=snapshot(), headers=ORIGIN).json()["revision"] == 1
        assert alice.put(path, json=snapshot(title="tampered"), headers=ORIGIN).status_code == 409
        assert bob.get(path).status_code == 404
        assert bob.delete(path, params={"expected_revision": 1}, headers=ORIGIN).status_code == 404
        assert bob.get("/api/user/chat/conversations").json()["total"] == 0
        assert alice.get("/api/user/chat/conversations").json()["total"] == 1
        assert "messages" not in alice.get("/api/user/chat/conversations").json()["data"][0]

        assert alice.put(path, json=snapshot(revision=0, mutation="mutation_0002"), headers=ORIGIN).status_code == 409
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
