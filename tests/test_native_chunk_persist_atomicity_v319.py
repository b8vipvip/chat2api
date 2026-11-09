"""Request-scoped native transport invariants: immutable revision and atomic disk ACK.

None of these operations may determine or override request-v6/Broker terminal.
"""
import base64
import hashlib
import json
import threading
from pathlib import Path

import pytest

import app.native_content_v1 as native


RID = "req_chunk_atomicity_v319"


def snapshot(revision, text="hello"):
    return {"version": 1, "revision": revision,
            "root": {"tag": "p", "text": text}}


def chunk_envelopes(revision, *, text):
    raw = json.dumps(snapshot(revision, text), separators=(",", ":")).encode()
    count = (len(raw) + 65535) // 65536
    digest = hashlib.sha256(raw).hexdigest()
    return [
        {"revision": revision, "index": i, "count": count,
         "byte_length": len(raw), "sha256": digest,
         "data": base64.b64encode(raw[i * 65536:(i + 1) * 65536]).decode()}
        for i in range(count)
    ]


def test_conflicting_same_revision_sha_cannot_replace_acked_chunks(tmp_path):
    assembler = native.NativeChunkAssembler(native.NativeContentStore(tmp_path))
    first = chunk_envelopes(11, text="A" * 66000)
    changed = chunk_envelopes(11, text="B" * 66500)
    assert len(first) == len(changed) == 2
    assert assembler.ingest(RID, "owner", first[0]) is False
    with pytest.raises(ValueError, match="revision conflict"):
        assembler.ingest(RID, "owner", changed[0])
    observed = assembler.progress(RID, "owner")
    assert observed["state"] == "incomplete"
    assert observed["revision"] == 11
    assert observed["received_chunks"] == 1
    assert assembler.ingest(RID, "owner", first[1]) is True
    assert assembler.store.get(RID, "owner")["revision"] == 11
    assert assembler.store.get(RID, "owner")["root"]["text"] == "A" * 66000


def test_corrupt_retransmission_same_chunk_index_preserves_valid_partial_state(tmp_path):
    assembler = native.NativeChunkAssembler(native.NativeContentStore(tmp_path))
    chunks = chunk_envelopes(13, text="correct" * 10500)
    assert len(chunks) == 2
    assert assembler.ingest(RID, "owner", chunks[0]) is False
    original = base64.b64decode(chunks[0]["data"])
    corrupted = dict(chunks[0])
    corrupted["data"] = base64.b64encode(b"X" + original[1:]).decode()
    # This replay lies about its SHA256 while preserving all envelope fields.
    # Only the invalid replay is rejected; it MUST NOT erase the originally
    # ACKed chunk or force a new native revision.
    with pytest.raises(ValueError, match="Conflicting native chunk"):
        assembler.ingest(RID, "owner", corrupted)
    progress = assembler.progress(RID, "owner")
    assert progress["revision"] == 13
    assert progress["received_chunks"] == 1
    # Idempotent byte-exact retransmission is still permitted.
    assert assembler.ingest(RID, "owner", chunks[0]) is False
    assert assembler.ingest(RID, "owner", chunks[1]) is True
    assert assembler.store.get(RID, "owner")["root"]["text"] == "correct" * 10500


def test_genuinely_newer_revision_may_supersede_partial_old_one(tmp_path):
    assembler = native.NativeChunkAssembler(native.NativeContentStore(tmp_path))
    old = chunk_envelopes(11, text="A" * 66000)
    newer = chunk_envelopes(12, text="B" * 66500)
    assert assembler.ingest(RID, "owner", old[0]) is False
    assert assembler.ingest(RID, "owner", newer[0]) is False
    observed = assembler.progress(RID, "owner")
    assert observed["revision"] == 12
    assert observed["received_chunks"] == 1
    assert assembler.ingest(RID, "owner", newer[1]) is True
    assert assembler.store.get(RID, "owner")["revision"] == 12


def test_cross_owner_chunk_revision_cannot_override_ongoing_transfer(tmp_path):
    assembler = native.NativeChunkAssembler(native.NativeContentStore(tmp_path))
    old = chunk_envelopes(11, text="A" * 66000)
    newer = chunk_envelopes(12, text="B" * 66500)
    assert assembler.ingest(RID, "owner1", old[0]) is False
    with pytest.raises(ValueError, match="owner/revision conflict"):
        assembler.ingest(RID, "owner2", newer[0])
    assert assembler.progress(RID, "owner1")["received_chunks"] == 1
    assert assembler.progress(RID, "owner2") == {"state": "none"}


def test_concurrent_persist_never_shares_temp_or_overwrites_newer_revision(tmp_path, monkeypatch):
    store = native.NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snapshot(1, "first"))
    original_replace = native.os.replace
    first_entered = threading.Event()
    release_first = threading.Event()
    names = []
    attempts = []
    lock = threading.Lock()

    def delayed_replace(src, dst):
        with lock:
            names.append(Path(src).name)
            number = len(names)
        if number == 1:
            first_entered.set()
            assert release_first.wait(timeout=10)
        return original_replace(src, dst)

    monkeypatch.setattr(native.os, "replace", delayed_replace)

    def persist():
        try:
            attempts.append(store.persist(RID, "owner"))
        except Exception as exc:
            attempts.append(exc)

    t1 = threading.Thread(target=persist)
    t2 = threading.Thread(target=persist)
    t1.start()
    try:
        assert first_entered.wait(timeout=10)
        assert store.publish(RID, "owner", snapshot(2, "second"))
        t2.start()
    finally:
        release_first.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    assert attempts.count(True) == 2, attempts
    assert len(names) == 2 and names[0] != names[1], names
    assert all(n.endswith(".tmp") for n in names)
    assert not list((tmp_path / "native_content").glob("*.tmp"))
    saved = native.NativeContentStore(tmp_path).get(RID, "owner")
    assert saved == snapshot(2, "second")


def test_failed_disk_write_cleans_private_temp_and_keeps_existing_snapshot(tmp_path, monkeypatch):
    store = native.NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snapshot(1))
    assert store.persist(RID, "owner")
    assert store.publish(RID, "owner", snapshot(2))
    with monkeypatch.context() as patch:
        def fail_fsync(_):
            raise OSError("disk unavailable")
        patch.setattr(native.os, "fsync", fail_fsync)
        assert store.persist(RID, "owner") is False
    assert not list((tmp_path / "native_content").glob("*.tmp"))
    assert native.NativeContentStore(tmp_path).get(RID, "owner") == snapshot(1)


def test_retention_cleanup_failure_cannot_undo_committed_snapshot(tmp_path, monkeypatch):
    store = native.NativeContentStore(tmp_path)
    assert store.publish(RID, "owner", snapshot(1))
    store._persist_count = 31
    old_glob = Path.glob
    def fail_cleanup(path, pattern):
        if pattern == "*.json":
            raise OSError("retention scan failed")
        return old_glob(path, pattern)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "glob", fail_cleanup)
        assert store.persist(RID, "owner") is True
    assert native.NativeContentStore(tmp_path).get(RID, "owner") == snapshot(1)
