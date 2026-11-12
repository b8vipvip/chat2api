"""Regression for v0.22.259 request 3a882: old rev12 survived failed final capture."""
import asyncio
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.native_snapshot_finality_v282 import native_snapshot_finality

RID="req_3a882bb5690442c48a9df887a0fd7ad9"
SNAP={"version":1,"revision":12,"root":{"tag":"p","text":"Partial"}}
BAD={"native_snapshot_received_revision":12,"native_capture_revision":12,
     "native_snapshot_persisted":True,"native_capture_failure":"structural-max-children",
     "native_structural_failure":"max-children","native_chunk_complete":False}
GOOD={"native_snapshot_terminal_complete":True,"native_capture_revision":12,
      "native_snapshot_persisted":True,"native_final_capture_committed":True,
      "native_chunk_complete":True}

def test_real_history_v259_persisted_incremental_does_not_prove_finality():
    assert native_snapshot_finality({"status":"completed","diagnostics":BAD},SNAP)=="rejected-final"

def test_positive_final_proof_and_missing_legacy_data_are_distinct():
    assert native_snapshot_finality({"status":"completed","diagnostics":GOOD},SNAP)=="verified-final"
    assert native_snapshot_finality({"status":"completed","diagnostics":{}},SNAP)=="unverified-final"
    assert native_snapshot_finality({"status":"running","diagnostics":BAD},SNAP)=="live-provisional"

def test_revision_mismatch_and_failed_terminal_reject():
    assert native_snapshot_finality({"status":"completed","diagnostics":{
        **GOOD,"native_capture_revision":13}},SNAP)=="rejected-final"
    assert native_snapshot_finality({"status":"completed","diagnostics":{
        **GOOD,"native_snapshot_terminal_complete":False}},SNAP)=="rejected-final"
    for status in ("error","cancelled","stalled"):
        assert native_snapshot_finality({"status":status,"diagnostics":{}},SNAP)=="rejected-final"

def test_proven_stale_native_snapshot_get_and_sse_do_not_deliver_false_final(tmp_path):
    app=create_app(Settings(CHAT2API_API_KEY="test-key",CHAT2API_PAIRING_CODE="test-pair",
                            CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        async def seed():
            await app.state.telemetry.upsert({"request_id":RID,"api_key_id":"master",
                                              "status":"completed","diagnostics":BAD})
        asyncio.run(seed())
        assert app.state.native_content.publish(RID,"master",SNAP)
        auth={"Authorization":"Bearer test-key"}
        assert client.get(f"/v1/native-content/{RID}",headers=auth).status_code==404
        stream=client.get(f"/v1/native-content/{RID}/events",headers=auth)
        assert stream.status_code==200
        assert "event: native.snapshot" not in stream.text
        assert "event: native.end" in stream.text
        admin=client.get(f"/api/admin/requests/{RID}/native-content",headers=auth)
        assert admin.status_code==200
        assert admin.json()["snapshot"]["revision"]==12
        assert admin.json()["snapshot_finality"]=="rejected-final"
        assert app.state.telemetry.get(RID)["status"]=="completed"

def test_completed_but_unverified_legacy_native_snapshot_is_forensic_only(tmp_path):
    app=create_app(Settings(CHAT2API_API_KEY="test-key",CHAT2API_PAIRING_CODE="test-pair",
                            CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        async def seed():
            await app.state.telemetry.upsert({"request_id":RID,"api_key_id":"master",
                                              "status":"completed","diagnostics":{}})
        asyncio.run(seed())
        assert app.state.native_content.publish(RID,"master",SNAP)
        auth={"Authorization":"Bearer test-key"}
        assert client.get(f"/v1/native-content/{RID}",headers=auth).status_code==404
        events=client.get(f"/v1/native-content/{RID}/events",headers=auth)
        assert events.status_code==200
        assert "event: native.snapshot" not in events.text
        forensic=client.get(f"/api/admin/requests/{RID}/native-content",headers=auth)
        assert forensic.status_code==200
        assert forensic.json()["snapshot_finality"]=="unverified-final"
        assert forensic.json()["snapshot"]["revision"]==12


def test_v298_api_metadata_only_links_authoritatively_verified_native_reply():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/"app/main.py").read_text("utf-8")
    assert 'if finality_status not in {"live-provisional", "verified-final"}:' in source
    assert source.count(') == "verified-final"') >= 2
    assert ') != "rejected-final"' not in source


def test_verified_final_snapshot_still_delivered(tmp_path):
    app=create_app(Settings(CHAT2API_API_KEY="test-key",CHAT2API_PAIRING_CODE="test-pair",
                            CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        async def seed():
            await app.state.telemetry.upsert({"request_id":RID,"api_key_id":"master",
                                              "status":"completed","diagnostics":GOOD})
        asyncio.run(seed())
        assert app.state.native_content.publish(RID,"master",SNAP)
        auth={"Authorization":"Bearer test-key"}
        assert client.get(f"/v1/native-content/{RID}",headers=auth).json()["revision"]==12
        admin=client.get(f"/api/admin/requests/{RID}/native-content",headers=auth)
        assert admin.json()["snapshot_finality"]=="verified-final"

def test_admin_marks_stale_revision_forensic_only():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/"app/request_history_v94_patch.py").read_text()
    assert "finality==='rejected-final'" in src
    assert "不能作为最终回复交付" in src


def test_broker_terminal_while_row_running_blocks_stale_native_revision():
    from types import SimpleNamespace
    from app.native_snapshot_finality_v282 import native_terminal_view
    row={"request_id":RID,"status":"running","client_id":"ext_lease_a","diagnostics":{}}
    broker=SimpleNamespace(request_id=RID, terminal_event_type="chat.completed",
                           client_id="ext_lease_a", diagnostics=dict(BAD))
    effective=native_terminal_view(row,broker)
    assert effective["status"]=="completed"
    assert native_snapshot_finality(effective,SNAP)=="rejected-final"
    assert row["status"]=="running"  # presentation must not mutate telemetry


def test_other_worker_terminal_cannot_override_request_row():
    from types import SimpleNamespace
    from app.native_snapshot_finality_v282 import native_terminal_view
    row={"request_id":RID,"status":"running","client_id":"ext_lease_a","diagnostics":{}}
    unrelated=SimpleNamespace(request_id=RID, terminal_event_type="chat.completed",
                              client_id="ext_lease_other",diagnostics=dict(BAD))
    assert native_terminal_view(row,unrelated) is row
    assert native_snapshot_finality(row,SNAP)=="live-provisional"
    assert native_terminal_view({**row,"client_id":None},unrelated)["status"]=="running"


def test_endpoint_uses_same_worker_broker_truth_before_row_finalized():
    source=(__import__("pathlib").Path(__file__).resolve().parents[1]/"app/main.py").read_text()
    assert 'native_terminal_view(row, broker.requests.get(request_id))' in source


def test_live_api_route_refuses_broker_finished_but_history_still_running(tmp_path):
    from app.broker import RequestState
    app=create_app(Settings(CHAT2API_API_KEY="test-key",CHAT2API_PAIRING_CODE="test-pair",
                            CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        async def seed():
            await app.state.telemetry.upsert({"request_id":RID,"api_key_id":"master",
                                              "client_id":"ext_consistent_lease",
                                              "status":"running","diagnostics":{}})
        asyncio.run(seed())
        assert app.state.native_content.publish(RID,"master",SNAP)
        state=RequestState(request_id=RID,client_id="ext_consistent_lease")
        state.terminal_event_type="chat.completed"
        state.diagnostics.update(BAD)
        app.state.broker.requests[RID]=state
        auth={"Authorization":"Bearer test-key"}
        assert client.get(f"/v1/native-content/{RID}",headers=auth).status_code==404
        admin=client.get(f"/api/admin/requests/{RID}/native-content",headers=auth)
        assert admin.status_code==200
        assert admin.json()["snapshot_finality"]=="rejected-final"
        assert app.state.telemetry.get(RID)["status"]=="running"


def test_v297_no_false_1_to_1_when_full_editor_state_is_missing():
    assert native_snapshot_finality({
        "status": "completed", "diagnostics": {
            **GOOD, "native_code_editor_state_candidates": 1,
            "native_code_editor_state_resolved": 0,
        },
    }, SNAP) == "rejected-final"
    assert native_snapshot_finality({
        "status": "completed", "diagnostics": {
            **GOOD, "native_code_editor_state_candidates": 1,
            "native_code_editor_state_resolved": 1,
        },
    }, SNAP) == "rejected-final", "reading a CodeMirror doc is not SSE delivery"
    assert native_snapshot_finality({
        "status": "completed", "diagnostics": {
            **GOOD, "native_code_editor_state_candidates": 1,
            "native_code_editor_state_resolved": 1,
            "native_canonical_code_integrated": 1,
        },
    }, SNAP) == "verified-final"


def test_v297_unmapped_archived_images_cannot_prove_visual_1_to_1():
    row = {"status": "completed", "diagnostics": {
        **GOOD,
        "native_layout_summary": {
            "nodes": 6808, "tables": 25, "images": 0,
            "archived_assets": 2, "media_mapping_complete": False,
        },
    }}
    assert native_snapshot_finality(row, SNAP) == "rejected-final"
    row["diagnostics"]["native_layout_summary"]["media_mapping_complete"] = True
    # Slot order alone cannot prove the original image bytes.
    assert native_snapshot_finality(row, SNAP) == "rejected-final"
    row["diagnostics"]["native_image_identity_status"] = "verified-sha256"
    row["diagnostics"]["native_identity_matched_count"] = 2
    # Inconsistent 0 DOM image slots versus 2 matched slots is not proof,
    # even if an old caller set the ordinal completeness flag to True.
    assert native_snapshot_finality(row, SNAP) == "rejected-final"
    # Only a consistent same-request DOM count can promote a complete
    # byte-verified mapping. One cannot manufacture 2 absent images.
    row["diagnostics"]["native_layout_summary"]["images"] = 2
    assert native_snapshot_finality(row, SNAP) == "verified-final"


def test_v297_private_reply_table_parity_blocks_stale_native_revision():
    comparison = {
        "comparable": True, "private_capture_complete": True,
        "private_tables": 25, "native_tables": 20,
        "private_images_small": 2, "native_images": 0,
    }
    assert native_snapshot_finality({
        "status": "completed", "diagnostics": {
            **GOOD, "native_capture_comparison": comparison,
        },
    }, SNAP) == "rejected-final"
    assert native_snapshot_finality({
        "status": "completed", "diagnostics": {
            **GOOD, "native_capture_comparison": {
                **comparison, "native_tables": 25,
            },
        },
    }, SNAP) == "verified-final"
    # Decorative small favicon images are not a verified generated-image
    # identity; do not treat their absence as proof the rich output is wrong.
    assert comparison["private_images_small"] == 2


def test_v298_one_finality_authority_reports_conflicts_with_stable_reason():
    from app.native_snapshot_finality_v282 import native_finality_decision
    row = {"status": "completed", "diagnostics": {
        **GOOD, "native_final_capture_committed": False,
    }}
    original = dict(row["diagnostics"])
    verdict = native_finality_decision(row, SNAP)
    assert verdict == {
        "status": "rejected-final",
        "reason": "failed-proof-native_final_capture_committed",
        "authority": "server-native-finality-v298",
        "scope": "native-sidecar-only",
        "visually_verified": False,
    }
    assert native_snapshot_finality(row, SNAP) == verdict["status"]
    assert row["diagnostics"] == original
    assert row["status"] == "completed"

    row["diagnostics"] = {**GOOD}
    proved = native_finality_decision(row, SNAP)
    assert proved["status"] == "verified-final"
    assert proved["reason"] == "verified-terminal-revision"
    # Structural finality is NOT a pixel-perfect/media-byte identity claim.
    assert proved["visually_verified"] is False


def test_v298_missing_or_mismatched_private_media_proof_never_overrides_finality():
    from app.native_snapshot_finality_v282 import native_finality_decision
    row = {"status": "completed", "diagnostics": {
        **GOOD,
        "native_code_editor_state_candidates": 1,
        "native_code_editor_state_resolved": 0,
    }}
    assert native_finality_decision(row, SNAP)["reason"] == "incomplete-editor-state"
    row["diagnostics"]["native_code_editor_state_resolved"] = 1
    assert native_finality_decision(row, SNAP)["reason"] == "canonical-editor-code-not-delivered"
    row["diagnostics"]["native_canonical_code_integrated"] = 1
    row["diagnostics"]["native_layout_summary"] = {
        "archived_assets": 3, "media_mapping_complete": False,
    }
    assert native_finality_decision(row, SNAP)["reason"] == "unmapped-archived-media"
    row["diagnostics"]["native_layout_summary"]["media_mapping_complete"] = True
    assert native_finality_decision(row, SNAP)["reason"] == "unverified-media-byte-identity"
    row["diagnostics"]["native_image_identity_status"] = "verified-sha256"
    row["diagnostics"]["native_identity_matched_count"] = 3
    row["diagnostics"]["native_capture_comparison"] = {
        "comparable": True, "private_capture_complete": True,
        "private_tables": 16, "native_tables": 10,
    }
    assert native_finality_decision(row, SNAP)["reason"] == "same-request-table-loss"
    row["diagnostics"]["native_capture_comparison"]["native_tables"] = 16
    assert native_finality_decision(row, SNAP)["status"] == "verified-final"


def test_v300_canonical_source_not_delivered_never_claims_verified_final():
    from app.native_snapshot_finality_v282 import native_finality_decision
    row = {"status": "completed", "diagnostics": {
        **GOOD, "native_code_editor_state_candidates": 2,
        "native_code_editor_state_resolved": 2,
    }}
    assert native_finality_decision(row, SNAP)["reason"] == "canonical-editor-code-not-delivered"
    for invalid in (None, False, 0, 1, 3):
        row["diagnostics"]["native_canonical_code_integrated"] = invalid
        assert native_finality_decision(row, SNAP)["reason"] == "canonical-editor-code-not-delivered"
        assert row["status"] == "completed"
    row["diagnostics"]["native_canonical_code_integrated"] = 2
    assert native_finality_decision(row, SNAP)["status"] == "verified-final"
    row["diagnostics"]["native_code_editor_state_resolved"] = 3
    assert native_finality_decision(row, SNAP)["reason"] == "incomplete-editor-state"



def test_v303_dynamic_native_media_and_table_counts_follow_current_request_only():
    from app.native_snapshot_finality_v282 import native_finality_decision
    # A new request may have ZERO tables or images, or any allowed amount.
    # No result from a previous request is used as the expected count.
    for tables in (0, 1, 5, 17, 23):
        row={"status":"completed","diagnostics":{
            **GOOD,
            "native_capture_comparison":{
                "comparable":True,"private_capture_complete":True,
                "private_tables":tables,"native_tables":tables,
                "private_images_displayed_large":0,"native_images":0,
                "possible_missing_large_images":0,
            },
            "native_layout_summary":{"archived_assets":0,
                "images":0,"tables":tables,"media_mapping_complete":True},
        }}
        proof=native_finality_decision(row,SNAP)
        assert proof["status"]=="verified-final", (tables,proof)
        assert proof["visually_verified"] is False
        row["diagnostics"]["native_capture_comparison"]["native_tables"]=tables+1
        assert native_finality_decision(row,SNAP)["reason"]=="same-request-table-duplication"
    # A generated image present on the actual same-request page must be
    # retained even when the media archiver has recorded 0 assets.
    lost={"status":"completed","diagnostics":{
        **GOOD,"native_capture_comparison":{
            "comparable":True,"private_capture_complete":True,
            "private_tables":0,"native_tables":0,
            "private_images_displayed_large":1,
            "native_images":0,"possible_missing_large_images":1,
        },
        "native_layout_summary":{"archived_assets":0,"media_mapping_complete":True},
    }}
    assert native_finality_decision(lost,SNAP)["reason"]=="same-request-large-image-loss"
    lost["diagnostics"]["native_capture_comparison"].update({
        "private_images_displayed_large":0,"possible_missing_large_images":0})
    assert native_finality_decision(lost,SNAP)["status"]=="verified-final"



def test_v310_real_mermaid_visual_cannot_be_declared_complete_from_placeholder():
    # Sanitized same-request evidence: req_d4d49e65 had a 768x789 original
    # chart region, 19 matching text tables and full Python code, while the
    # canonical OpenAI Markdown included only the stand-in below.
    from app.native_snapshot_finality_v282 import (
        native_diagram_coverage, native_finality_decision,
    )
    base = {"status": "completed", "diagnostics": dict(GOOD)}
    missing = {**base, "response_text": (
        "## 十二、项目决策流程图\n\n"
        "以下 Mermaid 流程覆盖市场验证与审批。\n\n"
        "Mermaid diagram\n\n下一步方案。"
    )}
    assert native_diagram_coverage(missing) == "mermaid-placeholder-without-source"
    assert native_finality_decision(missing, SNAP) == {
        "status": "rejected-final",
        "reason": "mermaid-diagram-source-omitted",
        "authority": "server-native-finality-v298",
        "scope": "native-sidecar-only",
        "visually_verified": False,
    }
    # This is a READ-ONLY sidecar truth guard, not a second Broker terminal.
    assert missing["status"] == "completed"
    assert native_diagram_coverage({
        **base, "response_text": "## Merlin\n\nThe label Mermaid diagram was mentioned."
    }) == "no-proven-loss"
    # A real fenced Mermaid diagram elsewhere CANNOT deliver the missing
    # diagram represented by a different standalone placeholder in this same
    # request. The previous v310 rule incorrectly promoted this to verified.
    source = {**missing, "response_text": (
        missing["response_text"] +
        "\n\n```mermaid\nflowchart TD\n  A[Start] --> B[End]\n```"
    )}
    assert native_diagram_coverage(source) == "mermaid-placeholder-without-source"
    assert native_finality_decision(source, SNAP)["reason"] == "mermaid-diagram-source-omitted"
    # A real diagram with NO standalone placeholder has no proven gap.
    clean = {**base, "response_text": (
        "## 流程图\n\n```mermaid\nflowchart TD\n  A[Start] --> B[End]\n```"
    )}
    assert native_diagram_coverage(clean) == "no-proven-loss"
    assert native_finality_decision(clean, SNAP)["status"] == "verified-final"
    # A literal label inside an ordinary fenced code sample is *source text*,
    # not an omitted graphic, even if another fenced Mermaid chart is present.
    embedded = {**base, "response_text": (
        "示例标签：\n```text\nMermaid diagram\n```"
        "\n\n~~~mermaid\nflowchart TD\n A --> B\n~~~"
    )}
    assert native_diagram_coverage(embedded) == "no-proven-loss"
    assert native_finality_decision(embedded, SNAP)["status"] == "verified-final"
    # Indented four-space code content is not a standalone Markdown diagram.
    indented = {**base, "response_text": "    Mermaid diagram"}
    assert native_diagram_coverage(indented) == "no-proven-loss"
    # Ordinary human text, not an entire standalone missing visual element.
    assert native_diagram_coverage({
        **base, "response_text": "Please provide a Mermaid diagram in markdown."
    }) == "no-proven-loss"
    assert native_finality_decision({
        **missing, "status": "error"
    }, SNAP)["reason"] == "broker-request-not-completed"
