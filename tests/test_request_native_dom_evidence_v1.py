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
    lambda x:x["root"]["children"][0]["children"][0].update({"text":"x"*192001}),
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

def test_formal_graphics_and_verified_assistant_id_report_persists(tmp_path):
    """The real v1 Worker now emits four graphics counters on every report.

    Previously the server rejected all such captures as Invalid counts, so
    request ZIP exports lost the exact SVG/Canvas diagnostics we needed.
    """
    report = example()
    report["scope"] = "verified_native_assistant_selection"
    report["boundary"] = "fresh_request_assistant_message_id"
    report["counts"].update({"svg": 1, "svg_large": 1,
                             "canvas": 1, "canvas_large": 0})
    # Current worker counts actual svg/canvas as DOM nodes in the evidence
    # tree (the sanitized tag on each is an inert div).
    report["counts"]["nodes"] = 5
    store = NativeDomEvidenceStore(tmp_path)
    assert store.save(RID, "principal-A", report)
    loaded = store.get(RID, "principal-A")
    assert loaded["scope"] == "verified_native_assistant_selection"
    assert loaded["counts"]["svg_large"] == 1
    assert loaded["counts"]["canvas"] == 1
    assert store.summary(RID, "principal-A")["available"] is True
    assert store.summary(RID, "principal-B")["available"] is False


@pytest.mark.parametrize("bad_counts", [
    {"svg": 1},  # mixed old/new partial schemas must fail closed
    {"svg": 2, "svg_large": 3, "canvas": 0, "canvas_large": 0},
    {"svg": 0, "svg_large": 0, "canvas": 0, "canvas_large": True},
    {"svg": -1, "svg_large": 0, "canvas": 0, "canvas_large": 0},
    {"svg": 30001, "svg_large": 0, "canvas": 0, "canvas_large": 0},
    {"svg": 0, "svg_large": 0, "canvas": 0, "canvas_large": 0, "untrusted": 7},
])
def test_formal_graphics_counts_reject_invalid_or_unbounded_values(bad_counts):
    report = example()
    report["counts"].update(bad_counts)
    with pytest.raises(ValueError, match="counts|Capture exceeds"):
        validate_evidence(report, RID)


def test_verified_assistant_id_scope_cannot_override_user_boundary():
    report = example()
    report["scope"] = "verified_native_assistant_selection"
    assert validate_evidence(report, RID)["complete"] is True
    report["scope"] = "unverified_native_assistant_selection"
    with pytest.raises(ValueError, match="reply scope"):
        validate_evidence(report, RID)
    report["scope"] = "candidate_only"
    with pytest.raises(ValueError, match="Partial candidate"):
        validate_evidence(report, RID)


def test_service_graphics_validator_matches_current_worker_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "chrome_extension" / "content_native_dom_evidence_v1.js").read_text()
    for field in ("svg", "svg_large", "canvas", "canvas_large"):
        assert field in source
    assert 'scope:"verified_native_assistant_selection"' in source
    report = example()
    report["counts"].update({"svg": 0, "svg_large": 0,
                             "canvas": 0, "canvas_large": 0})
    assert validate_evidence(report, RID)["complete"] is True




def test_v310_mermaid_large_opaque_geometry_has_bounded_private_schema(tmp_path):
    """A chart lost behind non-composed rendering must appear as uncertainty."""
    report = example()
    report["scope"] = "verified_native_assistant_selection"
    report["counts"].update({
        "svg": 0, "svg_large": 0,
        "canvas": 0, "canvas_large": 0,
        "opaque_large": 1, "opaque_large_max_w": 768,
        "opaque_large_max_h": 789,
    })
    assert validate_evidence(report, RID)["counts"]["opaque_large"] == 1
    store = NativeDomEvidenceStore(tmp_path)
    assert store.save(RID, "owner", report)
    assert store.summary(RID, "owner")["counts"]["opaque_large_max_h"] == 789
    # No page source URL, SVG bytes, iframe content, or CSS is retained.
    assert "iframe" not in str(store.get(RID, "owner"))


@pytest.mark.parametrize("overrides", [
    {"opaque_large": 1},  # Partial new schema is never accepted
    {"opaque_large": 1, "opaque_large_max_w": 768},
    {"opaque_large": 1, "opaque_large_max_w": 0, "opaque_large_max_h": 789},
    {"opaque_large": 1, "opaque_large_max_w": 768, "opaque_large_max_h": 0},
    {"opaque_large": -1, "opaque_large_max_w": 768, "opaque_large_max_h": 789},
    {"opaque_large": 41, "opaque_large_max_w": 768, "opaque_large_max_h": 789},
    {"opaque_large": 0, "opaque_large_max_w": 768, "opaque_large_max_h": 0},
])
def test_v310_opaque_geometry_cannot_accept_partial_or_invented_proof(overrides):
    report = example()
    report["counts"].update({
        "svg": 0, "svg_large": 0, "canvas": 0, "canvas_large": 0,
    })
    report["counts"].update(overrides)
    with pytest.raises(ValueError, match="counts|opaque"):
        validate_evidence(report, RID)


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


def test_browser_dom_capture_vm_includes_all_verified_sibling_cards():
    import subprocess
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(
        ["node", "tests/native_dom_evidence_v1.mjs"],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_verified_container_evidence_is_allowed_with_strict_owner_binding():
    evidence=example()
    evidence["scope"]="verified_reply_container"
    evidence["boundary"]="same_request_verified_finish_action"
    assert validate_evidence(evidence,RID)["complete"] is True



def test_3a882b_private_evidence_accepts_8000_nodes_without_urls():
    from app.native_dom_evidence_v1 import MAX_NODES
    assert MAX_NODES == 30000
    evidence=example()
    cell=evidence["root"]["children"][0]
    evidence["root"]["children"]=[dict(cell) for _ in range(4000)]
    evidence["counts"]["nodes"]=8001
    evidence["counts"]["text_chars"]=20000
    validated=validate_evidence(evidence,RID)
    assert validated["complete"] is True
    assert len(validated["root"]["children"])==4000


def test_9d090b_private_button_only_retains_safe_raster_geometry():
    evidence=example()
    layout=evidence["root"]["children"][0]["layout"]
    evidence["root"]["children"]=[{"tag":"figure","layout":layout,"children":[{
        "tag":"img","layout":layout,"image":{"natural_width":800,
            "natural_height":600,"loaded":True,"source_type":"https"}}]}]
    evidence["counts"].update({"nodes":3,"text_chars":0,"images":1})
    assert validate_evidence(evidence,RID)["counts"]["images"]==1
    # Any action label, src or event handler is still rejected server-side.
    evidence["root"]["children"][0]["onclick"]="download()"
    with pytest.raises(ValueError,match="Unsafe tree node"):
        validate_evidence(evidence,RID)
