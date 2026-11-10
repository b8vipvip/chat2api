"""A cross-process durable revision must never be hidden by a stale API worker.

#764 made COMMIT monotonic; an unrelated API process still returned its old
in-memory revision on GET, so the UI/SSE could display a partial layout.
"""
import asyncio
import json
import os
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.native_content_v1 import NativeContentStore


RID = "req_crossworkerreadv3351"


def snap(revision, marker):
    return {"version": 1, "revision": revision,
            "root": {"tag": "div", "children": [
                {"tag": "p", "text": marker}
            ]}}


def test_running_worker_reads_newer_snapshot_committed_by_another_store(tmp_path):
    first, second = NativeContentStore(tmp_path), NativeContentStore(tmp_path)
    assert first.publish(RID, "owner", snap(1, "partial"))
    assert first.get(RID, "owner") == snap(1, "partial")
    assert second.publish(RID, "owner", snap(2, "complete"))
    assert second.persist(RID, "owner")
    assert first.get(RID, "owner") == snap(2, "complete")
    assert first.persist(RID, "owner") is False, "stale local writer cannot ACK a sibling's durable revision"
    assert first.get(RID, "owner") == snap(2, "complete")
    assert NativeContentStore(tmp_path).get(RID, "owner") == snap(2, "complete")


def test_valid_local_new_revision_precedes_older_persisted_revision(tmp_path):
    store = NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snap(2, "old"))
    assert store.persist(RID, "owner")
    assert store.publish(RID, "owner", snap(3, "new live"))
    assert store.get(RID, "owner") == snap(3, "new live")
    assert store.persist(RID, "owner")
    assert NativeContentStore(tmp_path).get(RID, "owner") == snap(3, "new live")


def test_same_revision_different_byte_layout_is_not_resolved_by_process_cache(tmp_path):
    first, second = NativeContentStore(tmp_path), NativeContentStore(tmp_path)
    assert first.publish(RID, "owner", snap(8, "content A"))
    assert second.publish(RID, "owner", snap(8, "content B"))
    assert second.persist(RID, "owner")
    assert first.get(RID, "owner") is None, "cannot silently choose one immutable-revision conflict"
    assert first.persist(RID, "owner") is False
    assert NativeContentStore(tmp_path).get(RID, "owner") == snap(8, "content B")


def test_disk_owner_collision_wins_over_stale_in_memory_request_id(tmp_path):
    first, second = NativeContentStore(tmp_path), NativeContentStore(tmp_path)
    assert first.publish(RID, "owner-A", snap(1, "private A"))
    assert second.publish(RID, "owner-B", snap(2, "private B"))
    assert second.persist(RID, "owner-B")
    assert first.get(RID, "owner-A") is None
    assert first.get(RID, "owner-B") is None, "local conflicting owner still fails closed"
    assert first.persist(RID, "owner-A") is False
    assert NativeContentStore(tmp_path).get(RID, "owner-A") is None
    assert NativeContentStore(tmp_path).get(RID, "owner-B") == snap(2, "private B")


def test_corrupt_durable_manifest_never_falls_back_to_a_cached_past_version(tmp_path):
    store = NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snap(1, "partial"))
    assert store.persist(RID, "owner")
    file = store.root / (RID + ".json")
    file.write_text("{partial manifest", encoding="utf-8")
    assert store.get(RID, "owner") is None
    assert store.persist(RID, "owner") is False
    assert file.read_text() == "{partial manifest"


def test_expiration_cleanup_must_not_unlink_concurrently_published_new_revision(
    tmp_path, monkeypatch,
):
    from app import native_content_v1 as native
    store = NativeContentStore(tmp_path)
    old = NativeContentStore(tmp_path)
    assert old.publish(RID, "owner", snap(1, "old"))
    assert old.persist(RID, "owner")
    path = old.root / (RID + ".json")
    old_time = time.time() - 91 * 86400
    os.utime(path, (old_time, old_time))
    real_lock = native._native_crossprocess_commit_lock
    @contextmanager
    def replace_before_expiry_lock(root):
        # Another Uvicorn worker commits a new copy AFTER read of the old
        # file's stat but BEFORE the old worker is allowed to unlink it.
        current = path.with_name(RID + ".fresh")
        current.write_text(json.dumps({"owner": "owner",
                     "snapshot": snap(3, "new durable")}), encoding="utf-8")
        os.replace(current, path)
        with real_lock(root):
            yield
    monkeypatch.setattr(native, "_native_crossprocess_commit_lock", replace_before_expiry_lock)
    assert store.get(RID, "owner") == snap(3, "new durable")
    assert path.exists()


def test_authenticated_native_http_route_reads_other_worker_new_revision(tmp_path):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        async def record():
            await app.state.telemetry.upsert({
                "request_id": RID, "api_key_id": "master", "status": "completed",
            })
            await app.state.telemetry.merge_diagnostics(RID, {
                "native_snapshot_terminal_complete": True,
                "native_capture_revision": 2,
                "native_final_capture_committed": True,
                "native_chunk_complete": True,
                "native_snapshot_persisted": True,
                "native_reply_scope_verified": True,
            })
        asyncio.run(record())
        assert app.state.native_content.publish(RID, "master", snap(1, "old"))
        second=NativeContentStore(tmp_path)
        assert second.publish(RID, "master", snap(2, "new verified"))
        assert second.persist(RID, "master")
        output=client.get(f"/v1/native-content/{RID}",
            headers={"Authorization":"Bearer test-key"})
        assert output.status_code==200, output.text
        assert output.json()["revision"]==2
        assert output.json()["root"]["children"][0]["text"]=="new verified"
