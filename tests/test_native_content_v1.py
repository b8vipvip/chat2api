import pytest
from app.native_content_v1 import NativeContentStore, validate_native_snapshot

def snapshot(revision=1):
    return {"version": 1, "revision": revision,
            "root": {"tag": "div", "style": {"display": "grid",
                       "gridTemplateColumns": "1fr 1fr 1fr", "gap": "8px"},
                     "children": [{"tag": "img", "media_index": 0},
                                  {"tag": "img", "media_index": 1},
                                  {"tag": "p", "text": "example"}]}}

def test_native_sidecar_is_versioned_and_owner_isolated(tmp_path):
    store = NativeContentStore(tmp_path)
    assert store.publish("req_12345678", "key_a", snapshot())
    assert not store.publish("req_12345678", "key_b", snapshot(2))
    assert not store.publish("req_12345678", "key_a", snapshot())
    assert store.publish("req_12345678", "key_a", snapshot(2))
    assert store.get("req_12345678", "key_b") is None
    assert store.persist("req_12345678", "key_a")
    assert NativeContentStore(tmp_path).get("req_12345678", "key_a")["revision"] == 2
    assert NativeContentStore(tmp_path).get("req_12345678", "key_b") is None

@pytest.mark.parametrize("payload", [
    {"version": 1, "revision": 1, "root": {"tag": "script", "text": "alert(1)"}},
    {"version": 1, "revision": 1, "root": {"tag": "img", "media_index": 0, "src": "https://evil"}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "style": {"backgroundImage": "url(x)"}}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "style": {"color": "red;position:fixed"}}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "children": [{"tag": "iframe"}]}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "children": []}, "html": "<div>"},
    {"version": 1, "revision": 0, "root": {"tag": "p"}},
])
def test_disallowed_native_content_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_native_snapshot(payload)

def test_safe_layout_is_preserved():
    assert validate_native_snapshot(snapshot()) == snapshot()

def test_private_snapshot_permissions(tmp_path):
    import os
    store = NativeContentStore(tmp_path)
    rid = "req_private12345678"
    assert store.publish(rid, "private_key", snapshot())
    assert store.persist(rid, "private_key")
    assert os.stat(store.root).st_mode & 0o777 == 0o700
    assert os.stat(store.root / (rid + ".json")).st_mode & 0o777 == 0o600

def test_native_worker_observer_node_regression():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "--test", "tests/native_content_v1.mjs"],
                   cwd=root, check=True, timeout=20)

def test_expired_disk_and_live_snapshots_are_unreadable(tmp_path, monkeypatch):
    import os
    import time
    from app import native_content_v1 as native
    store = NativeContentStore(tmp_path)
    rid = "imgreq_expired1234567"
    assert store.publish(rid, "key_a", snapshot())
    assert store.persist(rid, "key_a")
    old_time = time.time() - 91 * 86400
    path = store.root / (rid + ".json")
    os.utime(path, (old_time, old_time))
    # A freshly restarted store must refuse the expired persisted snapshot.
    assert NativeContentStore(tmp_path).get(rid, "key_a") is None
    assert not path.exists()
    # Long-running workers must also expire in-memory snapshots after 90 days.
    monkeypatch.setattr(native.time, "time", lambda: old_time + 91 * 86400)
    assert store.get(rid, "key_a") is None


def test_periodic_cleanup_covers_images_responses_and_chat_requests(tmp_path):
    import os
    import time
    store = NativeContentStore(tmp_path)
    stale = ["imgreq_oldimage12345", "resp_oldreply12345", "req_oldchat12345"]
    for rid in stale:
        assert store.publish(rid, "key_a", snapshot())
        assert store.persist(rid, "key_a")
        path = store.root / (rid + ".json")
        age = time.time() - 91 * 86400
        os.utime(path, (age, age))
    # 32 persisted snapshots trigger the same periodic cleanup across all
    # supported request namespaces, not just req_*.json.
    for i in range(29):
        rid = f"req_freshsnapshot{i:04d}"
        assert store.publish(rid, "key_a", snapshot())
        assert store.persist(rid, "key_a")
    for rid in stale:
        assert not (store.root / (rid + ".json")).exists()
