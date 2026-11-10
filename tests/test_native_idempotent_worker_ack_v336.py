"""Lost Worker ACK retries must not break the native content state machine.

The server may already have committed a byte-identical revision when the
Worker retransmits the same snapshot or complete chunk set. Retrying those
same bytes must ACK, but conflicting versions/owners must fail closed.
"""
import base64
import hashlib
import json

from app.native_content_v1 import NativeContentStore, NativeChunkAssembler


RID="req_duplicateworkerackv336"


def snap(rev, text):
    return {"version":1,"revision":rev,
            "root":{"tag":"p","text":text}}


def encoded_chunk(s):
    raw=json.dumps(s,ensure_ascii=False).encode("utf-8")
    assert len(raw)<64*1024
    return {"revision":s["revision"],"index":0,"count":1,
            "byte_length":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
            "data":base64.b64encode(raw).decode("ascii")}


def test_same_worker_exact_revision_replay_is_idempotent(tmp_path):
    store=NativeContentStore(tmp_path)
    assert store.publish(RID,"owner",snap(4,"complete answer")) is True
    assert store.publish(RID,"owner",snap(4,"complete answer")) is True
    assert store.get(RID,"owner")==snap(4,"complete answer")
    assert store.publish(RID,"owner",snap(4,"completely different")) is False
    assert store.publish(RID,"owner",snap(3,"stale")) is False
    assert store.get(RID,"owner")==snap(4,"complete answer")
    assert store.publish(RID,"owner",snap(5,"new terminal layout")) is True


def test_replay_across_server_process_after_durable_commit(tmp_path):
    old=NativeContentStore(tmp_path)
    assert old.publish(RID,"owner",snap(8,"archived"))
    assert old.persist(RID,"owner")
    restarted=NativeContentStore(tmp_path)
    assert restarted.publish(RID,"owner",snap(8,"archived")) is True
    assert restarted.publish(RID,"owner",snap(8,"different")) is False
    assert restarted.publish(RID,"owner",snap(7,"stale writer")) is False
    assert restarted.persist(RID,"owner") is True
    assert NativeContentStore(tmp_path).get(RID,"owner")==snap(8,"archived")
    assert restarted.publish(RID,"owner",snap(9,"future")) is True


def test_stale_local_writer_rejected_before_native_ack_on_newer_durable(tmp_path):
    old,newer=NativeContentStore(tmp_path),NativeContentStore(tmp_path)
    assert old.publish(RID,"owner",snap(1,"old"))
    assert newer.publish(RID,"owner",snap(2,"new"))
    assert newer.persist(RID,"owner")
    assert old.publish(RID,"owner",snap(1,"old")) is False
    assert old.publish(RID,"owner",snap(2,"new")) is True
    # A cache older than the durable manifest cannot claim another
    # process's rev2 as its own newly persisted content.
    assert old.persist(RID,"owner") is False
    assert NativeContentStore(tmp_path).get(RID,"owner")==snap(2,"new")


def test_different_owner_or_corrupt_disk_is_never_acknowledged(tmp_path):
    first=NativeContentStore(tmp_path)
    assert first.publish(RID,"owner-one",snap(2,"private"))
    assert first.persist(RID,"owner-one")
    other=NativeContentStore(tmp_path)
    assert other.publish(RID,"owner-two",snap(4,"other")) is False
    assert other.get(RID,"owner-two") is None
    with (first.root/(RID+".json")).open("w",encoding="utf-8") as f:
        f.write("{invalid existing manifest")
    another=NativeContentStore(tmp_path)
    assert another.publish(RID,"owner-one",snap(3,"maybe newer")) is False


def test_repeated_complete_chunk_upload_ack_without_duplicate_layout(tmp_path):
    store=NativeContentStore(tmp_path)
    chunks=NativeChunkAssembler(store)
    source=snap(3,"full body")
    packet=encoded_chunk(source)
    assert chunks.ingest(RID,"owner",packet) is True
    assert chunks.progress(RID,"owner")["state"]=="none"
    # This second complete retransmission is a byte-identical Worker retry.
    assert chunks.ingest(RID,"owner",packet) is True
    assert store.persist(RID,"owner") is True
    after_restart=NativeChunkAssembler(NativeContentStore(tmp_path))
    assert after_restart.ingest(RID,"owner",packet) is True
    bad=encoded_chunk(snap(3,"bad body"))
    assert after_restart.ingest(RID,"owner",bad) is False
    assert NativeContentStore(tmp_path).get(RID,"owner")==source
