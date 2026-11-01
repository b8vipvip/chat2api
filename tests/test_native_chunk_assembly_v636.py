"""End-to-end bounded native chunk reassembly, no partial tree promotion."""
import base64
import hashlib
import json

import pytest
from app.native_content_v1 import NativeContentStore, NativeChunkAssembler


def large_snapshot():
    return {"version": 1, "revision": 17, "root": {"tag": "div",
        "children": [{"tag": "div", "children": [
            {"tag": "p", "text": f"block {group}:{index}",
             "style": {"fontSize": "16px", "color": "#123456",
                       "marginBottom": "12px", "lineHeight": "24px"}}
            for index in range(140)]} for group in range(125)]}}


def packets(snapshot):
    raw=json.dumps(snapshot,ensure_ascii=False,separators=(",",":")).encode()
    assert 900000 < len(raw) < 6*1024*1024, len(raw)
    sha=hashlib.sha256(raw).hexdigest()
    chunks=[raw[n:n+65536] for n in range(0,len(raw),65536)]
    return [{"revision":snapshot["revision"],"index":i,"count":len(chunks),
        "byte_length":len(raw),"sha256":sha,
        "data":base64.b64encode(data).decode()} for i,data in enumerate(chunks)]


def test_large_snapshot_reconstructs_exactly_and_never_publishes_partial(tmp_path):
    store=NativeContentStore(tmp_path)
    rid="req_chunked17b4a888ff"
    assembler=NativeChunkAssembler(store)
    original=large_snapshot()
    pieces=packets(original)
    assert len(pieces)>10
    # Reverse the chunk ordering to prove byte reassembly cannot depend on
    # WebSocket scheduling. Half a file must never replace a previous revision.
    for piece in pieces[1:]:
        assert not assembler.ingest(rid,"owner",piece)
        assert store.get(rid,"owner") is None
    assert assembler.ingest(rid,"owner",pieces[0])
    assert store.get(rid,"owner")==original
    assert store.get(rid,"someone_else") is None
    assert store.persist(rid,"owner")
    assert NativeContentStore(tmp_path).get(rid,"owner")==original


def test_corrupt_or_foreign_or_oversized_chunk_fails_closed(tmp_path):
    store=NativeContentStore(tmp_path)
    assembler=NativeChunkAssembler(store)
    rid="req_chunkunsafe12345"
    chunks=packets(large_snapshot())
    assert not assembler.ingest(rid,"owner",chunks[0])
    with pytest.raises(ValueError,match="owner/revision"):
        assembler.ingest(rid,"other",chunks[1])
    corrupt=dict(chunks[0],data=base64.b64encode(b"x"*65536).decode())
    with pytest.raises(ValueError,match="Conflicting"):
        assembler.ingest(rid,"owner",corrupt)
    assert store.get(rid,"owner") is None
    with pytest.raises(ValueError,match="bounds"):
        assembler.ingest(rid,"owner",dict(chunks[0],count=99))
    with pytest.raises(ValueError,match="bounds"):
        assembler.ingest(rid,"owner",dict(chunks[0],byte_length=9*1024*1024))
    # All bytes can be present, but a mismatched digest is not an archive.
    bad=NativeChunkAssembler(store)
    forged=[dict(p,sha256="0"*64) for p in chunks]
    for part in forged[:-1]:
        assert not bad.ingest(rid,"owner",part)
    with pytest.raises(ValueError,match="SHA256"):
        bad.ingest(rid,"owner",forged[-1])
    assert store.get(rid,"owner") is None


def test_chunked_tree_does_not_accept_unsafe_html_or_url(tmp_path):
    store=NativeContentStore(tmp_path)
    assembler=NativeChunkAssembler(store)
    original=large_snapshot()
    original["root"]["children"][0]["children"][0]["onclick"]="alert(1)"
    for i,part in enumerate(packets(original)):
        if i+1==len(packets(original)):
            with pytest.raises(ValueError,match="unsafe"):
                assembler.ingest("req_untrustedlarge123","owner",part)
        else:
            assembler.ingest("req_untrustedlarge123","owner",part)
    assert store.get("req_untrustedlarge123","owner") is None


def test_worker_chunked_capture_node_regression():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(["node","tests/native_content_chunked_v636.mjs"],
                          cwd=root,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+"\n"+result.stderr


def test_pending_native_chunk_progress_contains_only_safe_counts(tmp_path):
    store=NativeContentStore(tmp_path)
    assembler=NativeChunkAssembler(store)
    req="req_chunkprogress0001"
    items=packets(large_snapshot())
    assert assembler.progress(req,"owner")=={"state":"none"}
    assert not assembler.ingest(req,"owner",items[0])
    progress=assembler.progress(req,"owner")
    assert progress["state"]=="incomplete"
    assert progress["received_chunks"]==1
    assert progress["expected_chunks"]==len(items)
    assert progress["total_bytes"]==items[0]["byte_length"]
    assert "sha256" not in str(progress)
    assert "data" not in progress
    assert assembler.progress(req,"other")=={"state":"none"}
    for item in items[1:]:
        assembler.ingest(req,"owner",item)
    assert assembler.progress(req,"owner")=={"state":"none"}


def test_media_terminal_integrity_node_regression():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    proc=subprocess.run(["node","tests/media_terminal_payload_integrity_v638.mjs"],
                        cwd=root,capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,proc.stdout+"\n"+proc.stderr
