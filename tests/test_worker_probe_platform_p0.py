from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.worker_probe_store import WorkerProbeStore


def _app(tmp_path: Path):
    return create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    ))


def test_admin_policy_and_authentication(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        route = "/api/admin/worker-tools/catalog"
        assert client.get(route).status_code == 401
        assert client.get(route, headers={"Authorization": "Bearer invalid"}).status_code == 401
        headers = {"Authorization": "Bearer test-key"}
        catalog = client.get(route, headers=headers)
        assert catalog.status_code == 200
        tools = {tool["id"]: tool for tool in catalog.json()["tools"]}
        assert tools["media_delivery_v1"]["available"] is True
        assert tools["selector_compat_v1"]["available"] is True

        initial = client.get("/api/admin/worker-tools/policies?scope_id=global", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["policy"]["revision"] == 0
        body = {"expected_revision": 0, "tools": {
            "media_delivery_v1": {"enabled": True, "max_snapshots": 8},
            "conversation_dom_v1": {"enabled": True},
        }}
        changed = client.put("/api/admin/worker-tools/policies/global", headers=headers, json=body)
        assert changed.status_code == 200
        assert changed.json()["revision"] == 1
        assert client.put("/api/admin/worker-tools/policies/global", headers=headers, json=body).status_code == 409
        assert client.put("/api/admin/worker-tools/policies/global", headers=headers, json={
            "expected_revision": 1, "tools": {"future_probe_v2": {"enabled": True}}
        }).status_code == 422
        assert client.put("/api/admin/worker-tools/policies/global", headers=headers, json={
            "expected_revision": 1, "tools": {"media_delivery_v1": {"enabled": "yes"}}
        }).status_code == 422
        assert client.get("/api/admin/worker-tools/policies?scope_id=worker:ext_a12bcdef", headers=headers).status_code == 200

    reloaded = WorkerProbeStore(tmp_path)
    assert reloaded.policy("global")["revision"] == 1
    assert reloaded.effective("ext_a12bcdef")["tools"]["media_delivery_v1"]["enabled"]


def test_per_worker_disable_is_authoritative(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    async def setup():
        await store.put_policy("global", expected_revision=0,
                               tools={"media_delivery_v1": {"enabled": True}})
        await store.put_policy("worker:wrk_12345678", expected_revision=0,
                               tools={"media_delivery_v1": {"enabled": False}})
    asyncio.run(setup())
    assert store.effective("wrk_12345678")["tools"]["media_delivery_v1"]["enabled"] is False
    assert store.effective("wrk_99999999")["tools"]["media_delivery_v1"]["enabled"] is True
    with pytest.raises(PermissionError):
        asyncio.run(store.ingest(
            request_id="req_1234567890", client_id="ext_XYZ123456",
            worker_id="wrk_12345678",
            message={"tool_id": "media_delivery_v1", "stage": "media_handoff",
                     "sequence": 1, "classification": "handoff_without_visible_media",
                     "facts": {"assistant_turn_nodes": 0}},
        ))


def test_request_scoped_evidence_zip_and_no_terminal_mutation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    auth = {"Authorization": "Bearer test-key"}
    request_id = "req_0123456789abcdef"
    with TestClient(app) as client:
        store = app.state.worker_probes
        async def seed() -> None:
            await store.put_policy("global", expected_revision=0,
                                   tools={"media_delivery_v1": {"enabled": True}})
            await app.state.telemetry.upsert({
                "request_id": request_id, "status": "error",
                "requested_model": "gpt-6", "request_type": "text",
                "error": "Timed out waiting for ChatGPT",
            })
            await store.ingest(
                request_id=request_id, worker_id="wrk_12345678",
                client_id="ext_abcdef012345",
                message={"tool_id": "media_delivery_v1", "stage": "media_handoff",
                         "sequence": 1, "classification": "handoff_without_visible_media",
                         "facts": {
                             "main_found": True, "assistant_turn_nodes": 0,
                             "candidate_image_count": 0, "media_handoff_observed": True,
                             "private_url": "https://secret.example/image?token=secret",
                         }},
            )
        asyncio.run(seed())
        assert client.get(f"/api/admin/requests/{request_id}/probes").status_code == 401
        evidence = client.get(f"/api/admin/requests/{request_id}/probes", headers=auth)
        assert evidence.status_code == 200
        payload = evidence.json()
        assert payload["summary"]["event_count"] == 1
        assert payload["events"][0]["facts"]["assistant_turn_nodes"] == 0
        assert "private_url" not in payload["events"][0]["facts"]
        assert app.state.telemetry.get(request_id)["status"] == "error"
        archive = client.get(
            f"/api/admin/requests/{request_id}/probes/download", headers=auth
        )
        assert archive.status_code == 200
        assert archive.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as output:
            files = output.namelist()
            assert "manifest.json" in files
            assert "events/probe-timeline.jsonl" in files
            assert "probes/media-delivery.json" in files
            assert "probes/conversation-dom.json" in files
            assert "checksums.sha256" in files
            assert "secret.example" not in output.read("events/probe-timeline.jsonl").decode()
            assert json.loads(output.read("manifest.json"))["capture_status"] == "ok"
        assert app.state.telemetry.get(request_id)["status"] == "error"
        assert "private_url" not in (tmp_path / "worker_probes" / "events" /
                                      f"{request_id}.jsonl").read_text()


def test_disabled_worker_reports_no_fabricated_evidence(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    assert store.summary("req_12345678")["capture_status"] == "not_collected"
    assert store.summary("req_12345678")["last_facts"] is None
    with zipfile.ZipFile(io.BytesIO(store.archive("req_12345678"))) as output:
        missing = json.loads(output.read("probes/media-delivery.json"))
        assert missing["capture_status"] == "not_collected"
    with pytest.raises(ValueError):
        store.summary("../../etc/passwd")


def test_request_probe_ui_source_is_single_owner_and_no_arbitrary_js() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "worker_probe_ui_patch.py").read_text(encoding="utf-8")
    assert "workerToolsCatalog" in page and 'data-view="worker-tools"' in page
    assert "admin_module.ADMIN_HTML = html" in page
    store = (root / "app" / "worker_probe_store.py").read_text(encoding="utf-8")
    assert "Unexpected probe policy fields" in store
    assert '"media_delivery_v1"' in store
    assert "chat.completed" not in store and "chat.error" not in store

def test_dispatch_snapshot_survives_live_policy_toggle(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    worker_id = "wrk_test001"
    request_id = "imgreq_1234567890abc"
    async def run():
        await store.put_policy("global", expected_revision=0,
                               tools={"media_delivery_v1": {"enabled": True}})
        at_dispatch = store.effective(worker_id)
        await store.put_policy("global", expected_revision=1,
                               tools={"media_delivery_v1": {"enabled": False}})
        assert store.effective(worker_id)["tools"]["media_delivery_v1"]["enabled"] is False
        accepted = await store.ingest(
            request_id=request_id, worker_id=worker_id, client_id="ext_1234567890",
            effective_policy=at_dispatch,
            message={"tool_id": "media_delivery_v1", "stage": "before_terminal",
                     "sequence": 1, "classification": "completed_observed",
                     "facts": {"candidate_image_count": 1}},
        )
        assert accepted["accepted"]
        with pytest.raises(PermissionError):
            await store.ingest(
                request_id=request_id, worker_id="wrk_other01", client_id="ext_1234567890",
                effective_policy=at_dispatch,
                message={"tool_id": "media_delivery_v1", "stage": "before_terminal",
                         "sequence": 2, "classification": "completed_observed", "facts": {}},
            )
        assert store.summary(request_id)["event_count"] == 1
    asyncio.run(run())

def test_p1_network_model_and_selector_probe_exports_are_sanitized(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    request_id = "req_p1network0123456"
    async def run():
        await store.put_policy("global", expected_revision=0, tools={
            "network_evidence_v1": {"enabled": True},
            "model_tool_state_v1": {"enabled": True},
            "selector_compat_v1": {"enabled": True},
        })
        for sequence, tool_id in enumerate((
            "network_evidence_v1", "model_tool_state_v1", "selector_compat_v1",
        ), start=1):
            outcome = await store.ingest(
                request_id=request_id,
                worker_id="wrk_P1active01", client_id="ext_1234567890",
                message={
                    "tool_id": tool_id, "stage": "unresolved_after_threshold",
                    "sequence": sequence, "classification": "waiting_for_result",
                    "facts": {
                        "network_stream_seen": True, "network_stream_chunks": 13,
                        "network_stream_bytes": 4010, "network_stream_terminal_seen": False,
                        "model_picker_count": 2, "reasoning_selector_count": 1,
                        "selector_send_button_count": 1,
                        "secret_cookie": "session_token_DO_NOT_ARCHIVE",
                        "raw_html": "<html>PRIVATE</html>",
                        "signed_image_url": "https://example.test/image?token=PRIVATE",
                    },
                },
            )
            assert outcome["accepted"] is True
        assert store.summary(request_id)["event_count"] == 3
    asyncio.run(run())
    blob = store.archive(request_id)
    with zipfile.ZipFile(io.BytesIO(blob)) as output:
        for label in ("network-evidence", "model-tool-state", "selector-compat"):
            data = json.loads(output.read(f"probes/{label}.json"))
            assert data["capture_status"] == "ok"
            assert len(data["events"]) == 1
        lines = output.read("events/probe-timeline.jsonl").decode()
        for forbidden in ("session_token_DO_NOT_ARCHIVE", "PRIVATE", "raw_html",
                          "signed_image_url", "secret_cookie"):
            assert forbidden not in lines
        assert '"network_stream_chunks": 13' in lines


def test_p1_fact_schema_and_event_bound_are_documented(tmp_path: Path) -> None:
    store = WorkerProbeStore(tmp_path)
    assert len(store.catalog()) == 5
    assert all(item["available"] for item in store.catalog())
    source = (Path(__file__).resolve().parents[1] /
              "chrome_extension/content_probe_supervisor_v1.js").read_text(encoding="utf-8")
    assert "Math.floor(32 / enabled.length)" in source
    assert "network_stream_seen" in source and "network_stream_bytes" in source
    assert "model_picker_count" in source and "selector_composer_count" in source
    assert "getAttribute" in source
    assert "innerHTML" not in source
