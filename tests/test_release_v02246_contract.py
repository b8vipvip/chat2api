from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import (
    CHROME_BRIDGE_BUNDLE_VERSION,
    CHROME_BRIDGE_VERSION,
    SERVER_RUNTIME_VERSION,
    version_contract_payload,
)
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_formal_release_v02275_versions_and_worker_entry_are_aligned() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    entry = read("chrome_extension/background_entry.js")

    assert SERVER_RUNTIME_VERSION == "0.22.81"
    assert CHROME_BRIDGE_VERSION == "0.8.1"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.34"
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION
    assert '"background_route_close_terminal_v91.js"' in entry
    assert '"conversation_routing.js"' in entry
    assert '"conversation_dispatch.js"' in entry
    assert '"background_window_observer_v90.js"' in entry
    assert entry.index('"background_route_close_terminal_v91.js"') < entry.index('"conversation_routing.js"')

    # Retired browser-side decision owners must not be imported into production.
    for retired in (
        "conversation_warm_pool_v2.js",
        "background_external_warm_v28.js",
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
        assert f'"{retired}"' not in entry


def test_v02275_runtime_contract_advertises_only_active_authorities() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    features = payload["features"]
    bridge = payload["chrome_bridge"]

    assert bridge["bundle_version"] == "0.8.34"
    assert bridge["route_window_authority_revision"] == 30
    assert bridge["window_observer_revision"] == 90
    assert bridge["route_close_terminal_revision"] == 91
    assert features["capacity_scheduler_v58"] is True
    assert features["server_side_same_api_fifo_v58"] is True
    assert features["worker_single_route_authority_v30"] is True
    assert features["unexpected_route_close_terminal_v91"] is True
    assert features["window_observer_v90"] is True
    assert features["linux_worker_device_console_v122"] is False
    assert features["linux_worker_console_v123"] is False
    assert features["linux_worker_device_authority_v124"] is True
    assert features["linux_worker_extension_autopair_v124"] is True
    assert features["same_api_parallel_requests"] is False
    assert features["browser_side_same_api_queue"] is False
    assert features["speculative_worker_windows"] is False

    # Historical v57/v27/v28/v29/v88 owners remain code history only.
    assert features["worker_key_capacity_fifo_queue"] is False
    assert features["worker_sequential_affinity_v27"] is False
    assert features["worker_single_route_v28"] is False
    assert features["worker_strict_api_fifo_v29"] is False
    assert features["worker_window_fifo_manager_v88"] is False
    assert features["worker_window_lifecycle_observer_v88"] is False
    assert features["terminal_request_recovery"] is False
    assert features["failed_route_quarantine"] is False

    revision = payload["server"]["feature_revision"]
    for marker in (
        "capacity-scheduler-v58",
        "single-route-window-authority-v30",
        "window-observer-v90",
        "no-speculative-windows",
        "route-close-terminal-v91",
        "release-v02275",
        "linux-device-worker-console-v122",
    ):
        assert marker in revision


def test_v02275_bundle_markers_contracts_and_preflight_match_manifest() -> None:
    for path in (
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/background_runtime_preflight_v48.js",
    ):
        assert "0.8.34" in read(path), path
        assert "0.8.29" not in read(path), path


def test_carried_responses_and_multimodal_contracts_remain_enabled() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    features = payload["features"]
    for key in (
        "multimodal_main_world_v78",
        "multimodal_upload_ready_v84",
        "multimodal_safe_submit_v85",
        "model_capability_routing_v2",
        "responses_api_v108",
        "responses_emulated_tools_v109",
        "responses_tool_stream_v115",
        "responses_tool_stream_v116",
        "responses_tool_stream_v118",
        "native_tool_stream_v63",
        "worker_ui_hygiene_health_modal_v101",
    ):
        assert features[key] is True