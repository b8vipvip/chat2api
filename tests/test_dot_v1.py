import uuid

import pytest

from app.dot_v1 import DotGoal, DotProfile, DotStore


def goal():
    return DotGoal(goal="审查项目", model="default", idempotency_key=uuid.uuid4())


def test_dot_owner_isolation_idempotency_and_persistence(tmp_path):
    store = DotStore(tmp_path)
    store.profile("a", DotProfile(name="Dot", instructions="职责 A"))
    body = goal()
    first = store.create("a", body)
    assert first == store.create("a", body)
    assert store.list("b") == []
    assert store.profile("b")["instructions"] == ""
    with pytest.raises(KeyError):
        store.claim("b", first["id"])
    with pytest.raises(KeyError):
        store.remove("b", first["id"])
    claimed = store.claim("a", first["id"])
    assert claimed["instructions"] == "职责 A"
    with pytest.raises(ValueError):
        DotStore(tmp_path).claim("a", first["id"])
    with pytest.raises(ValueError):
        store.finish("a", first["id"], "wrong", "completed", "fake")
    done = store.finish("a", first["id"], claimed["request_id"], "completed", "answer")
    assert done["result"] == "answer"
    assert DotStore(tmp_path).list("a")[0]["status"] == "completed"
    store.remove("a", first["id"])
    assert store.list("a") == []


def test_dot_expired_lease_not_replayed(tmp_path, monkeypatch):
    import app.dot_v1 as module
    monkeypatch.setattr(module.time, "time", lambda: 100)
    store = DotStore(tmp_path)
    task = store.create("a", goal())
    running = store.claim("a", task["id"])
    second = store.create("a", goal())
    with pytest.raises(ValueError):
        store.claim("a", second["id"])
    monkeypatch.setattr(module.time, "time", lambda: 100 + module.LEASE_SECONDS + 1)
    # An expired lease is not mutated by an ordinary read; the executor must
    # reconcile the original Worker terminal first.
    assert next(row for row in store.list("a") if row["id"] == task["id"])["status"] == "running"
    store.finish("a", task["id"], running["request_id"], "interrupted")
    with pytest.raises(ValueError):
        store.claim("a", task["id"])
    with pytest.raises(ValueError):
        store.finish("a", task["id"], running["request_id"], "completed")
    assert store.claim("a", second["id"])["status"] == "running"


def test_dot_api_auth_and_real_adapter_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    import app.dot_v1 as module

    async def identity(request):
        owner = request.headers.get("x-test-owner")
        if not owner:
            raise HTTPException(401)
        return {"user_id": owner, "is_guest": owner == "guest"}, SimpleNamespace(key_id=owner)

    allowed = {"yes": True}
    monkeypatch.setattr(module, "resolve_web_chat_principal", identity)
    async def background_identity(app, owner):
        return {"user_id": owner}, SimpleNamespace(key_id=owner)
    monkeypatch.setattr(module, "resolve_dot_task_principal", background_identity)
    monkeypatch.setattr(module, "web_chat_model_decision",
                        lambda *args: {"allowed": allowed["yes"]})
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    calls = []

    async def adapter(body, request, client, rid, principal):
        assert request.scope["chat2api_mode1_web_chat"] is True
        assert request.scope["chat2api_web_chat_user_id"] == principal.key_id
        assert body.stream is False
        calls.append((body, rid, principal.key_id))
        return JSONResponse({"choices": [{"message": {"content": "真实 adapter 结果"}}]})

    app.state.chat_completions_adapter = adapter
    module.install_dot_v1(app)
    module.install_dot_v1(app)
    client = TestClient(app)
    base = "/api/user/chat/dot"
    a = {"x-test-owner": "a", "X-Chat2API-User-ID": "a", "Origin": "http://testserver"}
    b = {"x-test-owner": "b", "X-Chat2API-User-ID": "b", "Origin": "http://testserver"}
    assert client.get(base).status_code == 401
    assert client.get(base, headers={"x-test-owner": "guest"}).status_code == 403
    assert client.get("/chat/dot", headers=a).status_code == 200
    assert client.get(base, headers=a).headers["cache-control"] == "private, no-store"
    assert client.put(base + "/profile", headers={**a, "Origin": "https://evil.test"},
                      json={"name": "A"}).status_code == 403
    assert client.put(base + "/profile", headers={**a, "X-Chat2API-User-ID": "b"},
                      json={"name": "A"}).status_code == 409
    assert client.put(base + "/profile", headers=a, json={"name": "A", "instructions": "审查"}).status_code == 200
    data = {"goal": "测试", "model": "default", "idempotency_key": str(uuid.uuid4())}
    task = client.post(base + "/tasks", headers=a, json=data).json()["data"]
    run = base + "/tasks/" + task["id"] + "/run"
    assert client.post(run, headers=b).status_code == 404
    assert client.post(run, headers=a).json()["data"]["status"] == "scheduled"
    import asyncio
    async def tick():
        await app.state.dot_runtime.tick()
        await asyncio.gather(*app.state.dot_runtime.jobs.values())
        await app.state.dot_runtime.tick()
    asyncio.run(tick())
    assert client.get(base, headers=a).json()["tasks"][0]["result"] == "真实 adapter 结果"
    assert client.post(run, headers=a).status_code == 409
    assert len(calls) == 1
    assert calls[0][0].messages[0].content == "审查"
    assert client.get(base, headers=b).json()["tasks"] == []
    data["idempotency_key"] = str(uuid.uuid4())
    task = client.post(base + "/tasks", headers=a, json=data).json()["data"]
    allowed["yes"] = False
    assert client.post(base + "/tasks/" + task["id"] + "/run", headers=a).json()["data"]["status"] == "scheduled"
    asyncio.run(tick())
    assert client.get(base, headers=a).json()["tasks"][0]["status"] == "failed"
    assert len(calls) == 1


def test_schedule_pause_resume_stop_and_restart(tmp_path, monkeypatch):
    import app.dot_v1 as module
    monkeypatch.setattr(module.time, "time", lambda: 100)
    store = DotStore(tmp_path)
    task = store.create("a", goal())
    tid = task["id"]
    assert store.schedule("a", tid, 60)["run_at"] == 160
    assert store.due() == []
    assert store.control("a", tid, "pause")["status"] == "paused"
    monkeypatch.setattr(module.time, "time", lambda: 200)
    assert DotStore(tmp_path).due() == []
    with pytest.raises(KeyError):
        store.control("b", tid, "resume")
    store.control("a", tid, "resume")
    assert DotStore(tmp_path).due() == [{"owner": "a", "id": tid}]
    running = store.claim("a", tid, scheduled=True)
    assert store.control("a", tid, "stop")["stop_requested"] == 1
    assert store.stopped("a", tid, running["request_id"])
    assert store.list("a")[0]["status"] == "running"
    with pytest.raises(ValueError):
        store.remove("a", tid)
    store.finish("a", tid, running["request_id"], "cancelled")
    assert store.list("a")[0]["status"] == "cancelled"
    assert store.due() == []


def test_global_capacity_and_original_terminal_recovery(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.dot_v1 as module

    store = DotStore(tmp_path)
    first = store.create("a", goal())
    second = store.create("b", goal())
    store.schedule("a", first["id"])
    store.schedule("b", second["id"])
    running = store.claim("a", first["id"], scheduled=True)
    with pytest.raises(ValueError, match="capacity"):
        DotStore(tmp_path).claim("b", second["id"], scheduled=True)
    rid = running["request_id"]

    class Telemetry:
        def get(self, request_id):
            return None

        def get_many_persisted(self, ids):
            assert ids == {rid}
            return {rid: {"status": "completed", "api_key_id": "a-key",
                          "web_chat_mode": True, "response_text": "persisted response"}}

    class Journal:
        async def snapshot(self, request_id, owner):
            assert request_id == rid and owner == "a-key"
            return {"terminal_status": "completed", "delivered_text": "exact delivered response"}

    app = SimpleNamespace(state=SimpleNamespace(
        telemetry=Telemetry(),
        user_accounts=SimpleNamespace(owns_key=lambda owner, key: owner == "a" and key == "a-key"),
        web_chat_event_journal=Journal(),
    ))
    runtime = module.DotRuntime(app, DotStore(tmp_path))
    assert asyncio.run(runtime.reconcile(running)) is True
    final = store.list("a")[0]
    assert final["status"] == "completed"
    assert final["result"] == "exact delivered response"
    assert final["request_id"] == rid
    assert store.claim("b", second["id"], scheduled=True)["status"] == "running"


def test_runtime_rejects_other_owner_terminal(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.dot_v1 as module

    store = DotStore(tmp_path)
    task = store.create("a", goal())
    store.schedule("a", task["id"])
    running = store.claim("a", task["id"], scheduled=True)
    telemetry = SimpleNamespace(get=lambda rid: {"status": "completed", "api_key_id": "b-key",
                                                "web_chat_mode": True, "response_text": "private"})
    app = SimpleNamespace(state=SimpleNamespace(telemetry=telemetry,
                          user_accounts=SimpleNamespace(owns_key=lambda owner, key: False)))
    runtime = module.DotRuntime(app, store)
    assert asyncio.run(runtime.reconcile(running)) is False
    assert store.list("a")[0]["result"] == ""
    monkeypatch.setattr(module.time, "time", lambda: running["lease_until"] + 1)
    assert asyncio.run(runtime.reconcile(running)) is True
    assert store.list("a")[0]["status"] == "interrupted"
    assert store.list("a")[0]["result"] == ""
