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
    assert next(row for row in store.list("a") if row["id"] == task["id"])["status"] == "interrupted"
    with pytest.raises(ValueError):
        store.claim("a", task["id"])
    with pytest.raises(ValueError):
        store.finish("a", task["id"], running["request_id"], "completed")
    assert store.claim("a", second["id"])["status"] == "running"
