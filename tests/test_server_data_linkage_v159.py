"""Audit authoritative Worker, device and window data joins end-to-end."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from app.request_device_identity_patch import install_request_device_identity_patch
from app.telemetry import TelemetryStore
from app.worker_presentation_v64_patch import install_worker_presentation_v64_patch


ROOT = Path(__file__).resolve().parents[1]


class MultiplePairings:
    def __init__(self):
        self.items = {
            "pair_a": SimpleNamespace(pairing_id="pair_a", name="设备 A", bound_client_id="ext_a"),
            "pair_b": SimpleNamespace(pairing_id="pair_b", name="设备 B", bound_client_id="ext_b"),
        }

    def list_public(self):
        return [
            {"pairing_id":"pair_a","name":"设备 A","bound_client_id":"ext_a","bound_device_id":"physical_same"},
            {"pairing_id":"pair_b","name":"设备 B","bound_client_id":"ext_b","bound_device_id":"physical_same"},
        ]


def test_request_identity_never_guesses_between_two_pairings_on_one_physical_device(tmp_path):
    async def scenario():
        store=TelemetryStore(tmp_path)
        for client, name in [("ext_a",None),("ext_b",None),
                             ("ext_unknown",None),("ext_retired","历史已保存名称")]:
            await store.upsert({"request_id":"req_"+client,"client_id":client,"status":"completed",
                                "device_name":name,"device_code_id":"pair_old" if name else None})
        app=FastAPI()
        app.state.telemetry=store
        app.state.pairings=MultiplePairings()
        app.state.registry=SimpleNamespace(clients={
            key:SimpleNamespace(device_id="physical_same",pairing_id="")
            for key in ["ext_a","ext_b","ext_unknown","ext_retired"]
        })
        install_request_device_identity_patch(app)
        rows={row["request_id"]:row for row in store.query(limit=20)["data"]}
        assert rows["req_ext_a"]["device_name"]=="设备 A"
        assert rows["req_ext_a"]["device_code_id"]=="pair_a"
        assert rows["req_ext_b"]["device_name"]=="设备 B"
        assert rows["req_ext_b"]["device_code_id"]=="pair_b"
        assert rows["req_ext_unknown"]["device_name"] is None
        assert rows["req_ext_unknown"]["device_code_id"] is None
        assert rows["req_ext_retired"]["device_name"]=="历史已保存名称"
        assert rows["req_ext_retired"]["device_code_id"]=="pair_old"
    asyncio.run(scenario())



def test_duplicate_client_pairings_require_explicit_pairing_identity(tmp_path):
    async def scenario():
        store=TelemetryStore(tmp_path)
        await store.upsert({"request_id":"req_specific","client_id":"ext_shared","status":"completed"})
        await store.upsert({"request_id":"req_ambiguous","client_id":"ext_other","status":"completed"})
        class Pairings:
            def list_public(self):
                return [
                    {"pairing_id":"pair_a","name":"准确设备 A","bound_client_id":"ext_shared",
                     "bound_device_id":"same_device"},
                    {"pairing_id":"pair_b","name":"准确设备 B","bound_client_id":"ext_shared",
                     "bound_device_id":"same_device"},
                ]
        app=FastAPI()
        app.state.telemetry=store
        app.state.pairings=Pairings()
        app.state.registry=SimpleNamespace(clients={
            "ext_shared":SimpleNamespace(pairing_id="pair_a",device_id="same_device"),
            "ext_other":SimpleNamespace(pairing_id="",device_id="same_device"),
        })
        install_request_device_identity_patch(app)
        rows={r["request_id"]:r for r in store.query(limit=20)["data"]}
        assert rows["req_specific"]["device_name"]=="准确设备 A"
        assert rows["req_specific"]["device_code_id"]=="pair_a"
        assert rows["req_ambiguous"]["device_name"] is None
        assert rows["req_ambiguous"]["device_code_id"] is None
    asyncio.run(scenario())


def test_registry_summaries_resolve_linux_bridge_by_exact_active_worker():
    app=FastAPI()
    rows=[
        {"client_id":"ext_linux","metadata":{},"pairing_id":None},
        {"client_id":"ext_conflict","metadata":{},"pairing_id":None},
        {"client_id":"ext_revoked","metadata":{},"pairing_id":None},
    ]
    app.state.registry=SimpleNamespace(summaries=lambda:rows)
    app.state.pairings=MultiplePairings()
    app.state.admin_sessions=SimpleNamespace(authenticate=lambda _: True)
    workers=[
        {"worker_id":"wrk_live","extension_client_id":"ext_linux","metadata":{"device_name":"TX03"}},
        {"worker_id":"wrk_conflict_1","extension_client_id":"ext_conflict","metadata":{"device_name":"S1"}},
        {"worker_id":"wrk_conflict_2","extension_client_id":"ext_conflict","metadata":{"device_name":"S2"}},
        {"worker_id":"wrk_revoked","extension_client_id":"ext_revoked",
         "metadata":{"device_name":"Old"},"revoked_at":"2026-01-01"},
    ]
    app.state.linux_workers=SimpleNamespace(list_public=lambda:workers)
    install_worker_presentation_v64_patch(app)
    records={row["client_id"]:row for row in app.state.registry.summaries()}
    assert records["ext_linux"]["metadata"]["linux_worker_id"]=="wrk_live"
    assert records["ext_linux"]["device_name"]=="TX03"
    assert records["ext_conflict"]["metadata"].get("linux_worker_id") is None
    assert records["ext_conflict"]["metadata"]["linux_bridge_binding_conflict"] is True
    assert records["ext_conflict"]["device_name"] is None
    assert records["ext_revoked"]["metadata"].get("linux_worker_id") is None
    assert records["ext_revoked"]["device_name"] is None
    assert rows[0]["metadata"]=={}, "enrichment must never mutate original registry telemetry"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_js_placeholder_and_worker_status_contracts():
    run=subprocess.run(["node",str(ROOT/"tests/data_linkage_ui_v159.mjs")],
                       capture_output=True,text=True,timeout=20,check=False)
    assert run.returncode==0,run.stderr


def test_worker_data_association_source_guards():
    request=(ROOT/"app/request_device_identity_patch.py").read_text()
    registry=(ROOT/"app/worker_presentation_v64_patch.py").read_text()
    facade=(ROOT/"app/admin_worker_presentation_v66.js").read_text()
    linux=(ROOT/"app/admin_linux_device_authority_v124.js").read_text()
    assert 'by_device[device_id] = {}' in request
    assert 'historical_name = _canonical_label(result.get("device_name"))' in request
    assert 'not worker.get("revoked_at")' in request
    assert 'ambiguous_linux_clients' in registry
    assert '"linux_worker_id": linux_worker_id' in registry
    assert 'raw !== null && raw !== undefined' in facade
    assert 'Number(worker?.standby_window_count)' not in facade
    assert 'chatgpt(w,ext)' in linux
