"""Request-native DOM evidence is request-bound, strictly sanitized and private."""
import json
import os
from pathlib import Path
import pytest
from app.native_dom_evidence_v1 import NativeDomEvidenceStore, validate_evidence

RID="req_0123456789abcdef"
def example():
    return {"schema":"request-native-dom-evidence-v1","request_id":RID,
            "captured_at":"2026-10-31T03:50:00Z",
            "scope":"roleless_reply_siblings","boundary":"end_of_reply_parent",
            "complete":True,"counts":{"nodes":2,"text_chars":5,"images":0,
                                       "grids":0,"flex":0,"tables":0,"headings":0,"links":0},
            "root":{"tag":"reply","children":[{"tag":"p",
                     "layout":{"x":0,"y":0,"w":100,"h":20,"display":"block",
                               "flow":"","columns":"","gap":0,"font":16,"weight":400,"align":"left"},
                     "children":[{"tag":"#text","text":"Hello"}]}]}}

def test_admin_evidence_is_saved_per_owner_with_private_file_mode(tmp_path):
    store=NativeDomEvidenceStore(tmp_path)
    assert store.save(RID,"principal-A",example()) is True
    assert store.get(RID,"principal-A")["root"]["children"][0]["tag"]=="p"
    assert store.get(RID,"principal-B") is None
    assert store.save(RID,"principal-B",example()) is False
    assert store.summary(RID,"principal-B")["available"] is False
    assert (os.stat(store.root).st_mode & 0o777)==0o700
    assert (os.stat(store.root/(RID+".json")).st_mode & 0o777)==0o600

@pytest.mark.parametrize("mutate",[
    lambda x:x.update({"request_id":"req_anotherbadid"}),
    lambda x:x.update({"complete":True,"scope":"candidate_only"}),
    lambda x:x["root"]["children"][0].update({"href":"https://secret.example/token"}),
    lambda x:x["root"]["children"][0]["layout"].update({"columns":"url(https://evil)"}),
    lambda x:x["root"]["children"][0].update({"tag":"iframe"}),
    lambda x:x["root"]["children"][0]["children"][0].update({"text":"x"*120001}),
])
def test_malformed_or_private_html_fields_rejected(mutate):
    value=example()
    mutate(value)
    with pytest.raises(ValueError):
        validate_evidence(value,RID)

def test_partial_capture_must_admit_incomplete(tmp_path):
    partial=example()
    partial["complete"]=False
    partial["scope"]="candidate_only"
    partial["boundary"]="candidate_partial_unverified"
    partial["root"]=None
    assert NativeDomEvidenceStore(tmp_path).save(RID,"owner",partial)
    assert NativeDomEvidenceStore(tmp_path).summary(RID,"owner")["complete"] is False

def test_expired_evidence_removed(tmp_path,monkeypatch):
    store=NativeDomEvidenceStore(tmp_path)
    assert store.save(RID,"owner",example())
    path=store.root/(RID+".json")
    old=__import__("time").time()-8*86400
    os.utime(path,(old,old))
    assert store.get(RID,"owner") is None
    assert not path.exists()

def test_worker_capture_is_read_only_and_registered():
    root=Path(__file__).resolve().parents[1]
    source=(root/"chrome_extension"/"content_native_dom_evidence_v1.js").read_text()
    manifest=(root/"chrome_extension"/"manifest.json").read_text()
    controller=(root/"chrome_extension"/"content_request_v6.js").read_text()
    assert "content_native_dom_evidence_v1.js" in manifest
    assert "chat.native.dom_evidence" in source
    assert "baselineRolelessHeadings" in source
    assert "candidate_partial_unverified" in source
    assert "before_terminal" in controller
    assert "await globalThis.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__?.capture?" in controller
    assert "innerHTML" not in source and "document.cookie" not in source
    assert "type:\"chat.completed\"" not in source
    assert "type:\"chat.error\"" not in source


def test_stale_native_dom_evidence_pruned_on_bounded_write_cadence(tmp_path):
    store=NativeDomEvidenceStore(tmp_path)
    first="req_event00000001"
    for i in range(16):
        rid=f"req_event{i+1:08d}"
        report=example();report["request_id"]=rid
        assert store.save(rid,"owner",report)
        if i==0:
            import time
            stale=time.time()-8*86400
            os.utime(store.root/(first+".json"),(stale,stale))
    assert not (store.root/(first+".json")).exists(), "stale data cannot accumulate without admin export"


def test_normal_request_log_exports_also_include_full_bounded_evidence():
    src=(Path(__file__).resolve().parents[1]/"app"/"v8_patch.py").read_text()
    assert '"worker_native_dom_reply_tree": native_dom_reply' in src
    assert '"worker_native_dom_evidence"' in src
    assert 'native_dom_evidence.get(request_id' in src
