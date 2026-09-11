from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.capacity_scheduler_v58 import PATCH_ID as SCHEDULER_PATCH_ID
from app.responses_tool_stream_v118_patch import PATCH_REVISION, _normalize_nested_custom_call_v118
from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def _exec_catalog() -> list[dict[str, object]]:
    return [{"type": "custom", "namespace": "functions", "name": "exec"}]


def test_v118_accepts_exact_namespace_qualified_nested_exec_identity() -> None:
    call = {
        "namespace": "functions",
        "name": "exec",
        "arguments": {
            "name": "functions.exec",
            "input": "const r = await tools.collaboration__spawn_agent({task_name:'x',message:'ok'}); text(r);",
        },
    }
    normalized = _normalize_nested_custom_call_v118(call, _exec_catalog())
    assert normalized["namespace"] == "functions"
    assert normalized["name"] == "exec"
    assert "arguments" not in normalized
    assert normalized["input"].startswith("const r = await tools.collaboration__spawn_agent")
    assert PATCH_REVISION == 118


def test_v118_still_rejects_a_different_nested_custom_tool() -> None:
    call = {
        "namespace": "functions",
        "name": "exec",
        "arguments": {"name": "functions.other", "input": "text('bad');"},
    }
    with pytest.raises(ValueError, match="does not match outer tool"):
        _normalize_nested_custom_call_v118(call, _exec_catalog())


def test_worker_bundle_and_runtime_are_v02275_and_0831() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert SERVER_RUNTIME_VERSION == "0.22.77"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.32"
    assert manifest["version"] == "0.8.32"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["capacity_scheduler_v58"] is True
    assert payload["features"]["server_side_same_api_fifo_v58"] is True
    assert payload["features"]["worker_single_route_authority_v30"] is True
    assert payload["features"]["unexpected_route_close_terminal_v91"] is True
    assert payload["features"]["linux_worker_device_console_v122"] is True
    assert payload["chrome_bridge"]["route_close_terminal_revision"] == 91
    assert payload["features"]["responses_tool_stream_v118"] is True
    assert payload["features"]["same_api_parallel_requests"] is False
    assert payload["features"]["browser_side_same_api_queue"] is False
    assert payload["features"]["worker_strict_api_fifo_v29"] is False
    assert "release-v02275" in payload["server"]["feature_revision"]
    assert "linux-device-worker-console-v122" in payload["server"]["feature_revision"]
    assert SCHEDULER_PATCH_ID == "server-single-authority-scheduler-v58"


def test_background_entry_has_one_request_route_authority_and_no_browser_fifo() -> None:
    source = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert source.index('"background_route_close_terminal_v91.js"') < source.index('"conversation_routing.js"')
    assert source.index('"conversation_routing.js"') < source.index('"conversation_dispatch.js"')
    assert '"background_window_observer_v90.js"' in source
    for retired in (
        "conversation_warm_pool_v2.js",
        "background_reserve_pool_v29.js",
        "background_tab_supervisor_v32.js",
        "conversation_workers_v25.js",
        "conversation_workers_v27.js",
        "conversation_workers_v28.js",
        "conversation_dispatch_v29.js",
        "background_route_quarantine_v50.js",
        "background_request_recovery_v40.js",
        "background_window_manager_v88.js",
        "background_routed_window_cap_v111.js",
    ):
        assert f'"{retired}"' not in source, retired


def test_router_owns_window_lifecycle_dispatch_is_transport_only_and_v91_only_reports_terminal() -> None:
    router = (ROOT / "chrome_extension" / "conversation_routing.js").read_text(encoding="utf-8")
    dispatch = (ROOT / "chrome_extension" / "conversation_dispatch.js").read_text(encoding="utf-8")
    terminal = (ROOT / "chrome_extension" / "background_route_close_terminal_v91.js").read_text(encoding="utf-8")
    observer = (ROOT / "chrome_extension" / "background_window_observer_v90.js").read_text(encoding="utf-8")
    assert "single-route-window-authority-v30" in router
    assert "browser_side_same_api_queue: false" in router
    assert "state.failRequest = failRequest" in router
    assert "router.failRequest" in dispatch
    assert "chrome.windows.remove" not in dispatch
    assert "terminal-report-only-v91" in terminal
    assert "trySendSocket" in terminal
    assert "chrome.windows.create" not in terminal
    assert "chrome.windows.remove" not in terminal
    assert "decision_authority: false" in observer
    assert "chrome.windows.create" not in observer
    assert "chrome.windows.remove" not in observer


def test_legacy_v28_v29_sources_are_retained_only_as_non_production_history() -> None:
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    old_worker = (ROOT / "chrome_extension" / "conversation_workers_v28.js").read_text(encoding="utf-8")
    old_fifo = (ROOT / "chrome_extension" / "conversation_dispatch_v29.js").read_text(encoding="utf-8")
    assert "per-logical-api-strict-worker1-v28" in old_worker
    assert "per-logical-api-terminal-fifo-v29" in old_fifo
    assert '"conversation_workers_v28.js"' not in entry
    assert '"conversation_dispatch_v29.js"' not in entry