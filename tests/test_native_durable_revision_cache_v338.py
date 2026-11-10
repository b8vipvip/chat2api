"""v338: revision-safe cross-process reads cannot parse multi-MiB JSON 4/s.

Native SSE polls every 250 ms. Cache only an authenticated, validated durable
tree, keyed by filesystem identity; never mistake stale cache for new content.
"""
import json
import os
import time
from pathlib import Path

from app.native_content_v1 import NativeContentStore


RID="req_nativecachev338123"


def snap(rev,word):
    return {"version":1,"revision":rev,
            "root":{"tag":"div","children":[{"tag":"p","text":word}]}}


def install(store,rid,rev,word,owner="owner"):
    assert store.publish(rid,owner,snap(rev,word))
    assert store.persist(rid,owner)


def test_many_sse_polls_stat_only_and_validate_durable_once(tmp_path,monkeypatch):
    source=NativeContentStore(tmp_path)
    install(source,RID,1,"first")
    reader=NativeContentStore(tmp_path)
    real=Path.read_text
    reads=[]
    def observed(path,*args,**kwargs):
        if path.name==RID+".json":reads.append(1)
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",observed)
    for _ in range(28):
        assert reader.get(RID,"owner")==snap(1,"first")
    assert len(reads)==1,"28 SSE polls should decode the unchanged manifest once"
    assert list(reader._disk_cache)==[RID]


def test_atomic_replace_from_second_process_invalidates_cached_revision(tmp_path,monkeypatch):
    source=NativeContentStore(tmp_path)
    install(source,RID,1,"first")
    reader=NativeContentStore(tmp_path)
    assert reader.get(RID,"owner")==snap(1,"first")
    sibling=NativeContentStore(tmp_path)
    install(sibling,RID,2,"second")
    # Count only this reader's cache invalidation reloads, not the
    # independent writer's legitimate manifest reads during publication.
    real=Path.read_text
    reads=[]
    def observed(path,*args,**kwargs):
        if path.name==RID+".json":reads.append(1)
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",observed)
    for _ in range(16):
        assert reader.get(RID,"owner")==snap(2,"second")
    assert len(reads)==1,"atomic sibling replace must trigger exactly one reread"
    assert reader._disk_cache[RID][2]["revision"]==2


def test_cache_still_rejects_persistent_owner_change(tmp_path):
    source=NativeContentStore(tmp_path)
    install(source,RID,1,"first","owner-A")
    reader=NativeContentStore(tmp_path)
    assert reader.get(RID,"owner-A")==snap(1,"first")
    # Simulate an unrelated durable file for the SAME request ID, to make
    # sure the private per-worker cache never authorizes the old owner.
    path=source.root/(RID+".json")
    tmp=path.with_suffix(".new")
    tmp.write_text(json.dumps({"owner":"owner-B","snapshot":snap(3,"private B")}))
    os.replace(tmp,path)
    assert reader.get(RID,"owner-A") is None
    assert reader.get(RID,"owner-B")==snap(3,"private B")


def test_in_place_same_length_mutation_with_original_mtime_is_detected(tmp_path):
    source=NativeContentStore(tmp_path)
    install(source,RID,1,"first")
    reader=NativeContentStore(tmp_path)
    assert reader.get(RID,"owner")==snap(1,"first")
    path=source.root/(RID+".json")
    old=path.stat()
    time.sleep(0.01)  # ensure a distinct ctime on coarse filesystems
    raw=path.read_text()
    assert "first" in raw
    path.write_text(raw.replace("first","other"),encoding="utf-8")
    os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
    now=path.stat()
    assert now.st_size==old.st_size and now.st_mtime_ns==old.st_mtime_ns
    assert now.st_ctime_ns!=old.st_ctime_ns
    assert reader.get(RID,"owner")==snap(1,"other")
    assert reader._disk_cache[RID][2]["root"]["children"][0]["text"]=="other"


def test_bounded_lru_cache_keeps_at_most_three_validated_layouts(tmp_path):
    writer=NativeContentStore(tmp_path)
    reader=NativeContentStore(tmp_path)
    ids=[f"req_cachebounded338{i:03d}" for i in range(5)]
    for i,rid in enumerate(ids):
        install(writer,rid,1,str(i))
        assert reader.get(rid,"owner")==snap(1,str(i))
        assert len(reader._disk_cache)<=3
    assert list(reader._disk_cache)==ids[-3:]
    assert reader.get(ids[0],"owner")==snap(1,"0")
    assert list(reader._disk_cache)==ids[-2:]+[ids[0]]
