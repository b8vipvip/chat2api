"""Even two independent API workers cannot downgrade a persisted native reply.

The Broker/request-v6 owns terminal; this is only the optional native
snapshot's disk state machine and must be owner- and revision-monotonic.
"""
import json
import threading
from pathlib import Path

from app.native_content_v1 import NativeContentStore


RID = "req_crossprocessnative327"


def snapshot(revision, text):
    return {"version": 1, "revision": revision,
            "root": {"tag": "p", "text": text}}


def test_stale_store_cannot_overwrite_newer_durable_revision(tmp_path):
    first = NativeContentStore(tmp_path)
    later = NativeContentStore(tmp_path)
    assert first.publish(RID, "owner", snapshot(1, "old"))
    assert later.publish(RID, "owner", snapshot(2, "new"))
    assert later.persist(RID, "owner") is True
    assert first.persist(RID, "owner") is False
    assert NativeContentStore(tmp_path).get(RID, "owner") == snapshot(2, "new")
    assert not list((tmp_path / "native_content").glob("*.tmp"))


def test_same_revision_idempotence_requires_identical_bytes_and_owner(tmp_path):
    store_a = NativeContentStore(tmp_path)
    store_b = NativeContentStore(tmp_path)
    store_c = NativeContentStore(tmp_path)
    assert store_a.publish(RID, "owner", snapshot(4, "actual reply"))
    assert store_b.publish(RID, "owner", snapshot(4, "actual reply"))
    assert store_c.publish(RID, "owner", snapshot(4, "DIFFERENT reply"))
    assert store_a.persist(RID, "owner")
    path = tmp_path / "native_content" / (RID + ".json")
    before = path.read_bytes()
    assert store_b.persist(RID, "owner") is True
    assert path.read_bytes() == before
    assert store_c.persist(RID, "owner") is False
    assert path.read_bytes() == before
    assert NativeContentStore(tmp_path).get(RID, "owner") == snapshot(4, "actual reply")


def test_cross_owner_persist_cannot_replace_same_request_id(tmp_path):
    original = NativeContentStore(tmp_path)
    another = NativeContentStore(tmp_path)
    assert original.publish(RID, "owner-one", snapshot(1, "owner-one"))
    assert another.publish(RID, "owner-two", snapshot(9, "other"))
    assert original.persist(RID, "owner-one")
    assert another.persist(RID, "owner-two") is False
    reader = NativeContentStore(tmp_path)
    assert reader.get(RID, "owner-one") == snapshot(1, "owner-one")
    assert reader.get(RID, "owner-two") is None


def test_two_independent_stores_race_but_disk_ends_at_maximum_revision(tmp_path):
    first = NativeContentStore(tmp_path)
    later = NativeContentStore(tmp_path)
    assert first.publish(RID, "owner", snapshot(8, "eight"))
    assert later.publish(RID, "owner", snapshot(9, "nine"))
    begin = threading.Barrier(3)
    finished = []
    def run(store):
        begin.wait(timeout=10)
        finished.append(store.persist(RID, "owner"))
    t1 = threading.Thread(target=run, args=(first,))
    t2 = threading.Thread(target=run, args=(later,))
    t1.start()
    t2.start()
    begin.wait(timeout=10)
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(finished) == 2 and True in finished
    assert NativeContentStore(tmp_path).get(RID, "owner") == snapshot(9, "nine")


def test_corrupt_disk_manifest_is_not_replaced_under_unproven_owner(tmp_path):
    first = NativeContentStore(tmp_path)
    later = NativeContentStore(tmp_path)
    assert first.publish(RID, "owner", snapshot(1, "old"))
    assert first.persist(RID, "owner")
    old_file = tmp_path / "native_content" / (RID + ".json")
    old_file.write_text("{broken json", encoding="utf-8")
    assert later.publish(RID, "owner", snapshot(2, "new"))
    assert later.persist(RID, "owner") is False
    assert old_file.read_text(encoding="utf-8") == "{broken json"


def test_written_manifest_is_owner_and_revision_scoped(tmp_path):
    store = NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snapshot(13, "terminal body"))
    assert store.persist(RID, "owner")
    path = tmp_path / "native_content" / (RID + ".json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest == {"owner": "owner", "snapshot": snapshot(13, "terminal body")}
    assert (tmp_path / "native_content" / ".persist.lock").stat().st_mode & 0o777 == 0o600
