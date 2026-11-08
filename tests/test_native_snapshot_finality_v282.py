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
    row={"status":"running","client_id":"ext_lease_a","diagnostics":{}}
    broker=SimpleNamespace(terminal_event_type="chat.completed",
                           client_id="ext_lease_a", diagnostics=dict(BAD))
    effective=native_terminal_view(row,broker)
    assert effective["status"]=="completed"
    assert native_snapshot_finality(effective,SNAP)=="rejected-final"
    assert row["status"]=="running"  # presentation must not mutate telemetry


def test_other_worker_terminal_cannot_override_request_row():
    from types import SimpleNamespace
    from app.native_snapshot_finality_v282 import native_terminal_view
    row={"status":"running","client_id":"ext_lease_a","diagnostics":{}}
    unrelated=SimpleNamespace(terminal_event_type="chat.completed",
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
