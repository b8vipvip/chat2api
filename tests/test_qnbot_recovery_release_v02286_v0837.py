from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_v02286_v0837_qnbot_recovery_release_contract() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert SERVER_RUNTIME_VERSION == "0.22.93"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.42"
    assert manifest["version"] == "0.8.42"
    assert "release-v02286" in payload["server"]["feature_revision"]
    assert "release-v0837" in payload["chrome_bridge"]["build_revision"]
    assert payload["features"]["qnbot_stale_route_recovery_v136"] is True
    assert payload["features"]["request_id_namespace_v136"] is True

def test_v0837_worker_bundle_loads_route_recovery_v136() -> None:
    entry = read("chrome_extension/background_entry.js")
    recovery = read("chrome_extension/conversation_route_recovery_v136.js")
    assert "conversation_route_recovery_v136.js" in entry
    assert "server-authority-stale-inflight-v136" in recovery
    assert "server-cancel-control-v136" in recovery
    for path in (
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
    ):
        assert "0.8.42" in read(path), path

def test_v02286_installs_request_id_namespace_v136() -> None:
    entry = read("app/entry.py")
    namespace = read("app/request_id_namespace_v136_patch.py")
    assert "install_request_id_namespace_v136_patch(app)" in entry
    assert '"/v1/responses": ("resp_req_", "responses")' in namespace
    assert '"/v1/chat/completions": ("req_", "chat")' in namespace
    assert "x-chat2api-trace-id" in namespace.lower()
