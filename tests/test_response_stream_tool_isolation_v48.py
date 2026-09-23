import json
import subprocess
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]

NEW_JS = [
    "chrome_extension/content_bundle_marker_v48.js", "chrome_extension/content_bundle_marker_v71.js",
    "chrome_extension/content_rate_limit_guard_v52.js", "chrome_extension/content_tool_isolation_v48.js",
    "chrome_extension/content_request_lifecycle_v50.js", "chrome_extension/content_rich_response_v69.js",
    "chrome_extension/content_request_v6.js", "chrome_extension/content_network_stream_recovery_v55.js",
    "chrome_extension/content_native_tool_stream_v63.js", "chrome_extension/native_tool_stream_main_v63.js",
    "chrome_extension/content_response_semantic_recovery_v51.js", "chrome_extension/content_transient_retry_v50.js",
    "chrome_extension/content_generation_liveness_v49.js", "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/content_runtime_contract_v71.js", "chrome_extension/network_stream_main_v55.js",
    "chrome_extension/background_rate_limit_guard_v52.js", "chrome_extension/background_tool_isolation_v48.js",
    "chrome_extension/background_runtime_preflight_v48.js", "chrome_extension/background_route_close_terminal_v91.js",
]


def test_v48_javascript_assets_parse():
    for filename in NEW_JS:
        result = subprocess.run(["node", "--check", str(ROOT / filename)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"{filename}: {result.stderr}"


def test_manifest_requires_request_v6_terminal_owner_and_passive_recovery():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION == "0.22.95"
    main_scripts = manifest["content_scripts"][0]["js"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "network_stream_main_v55.js" in main_scripts
    assert "native_tool_stream_main_v63.js" in main_scripts
    assert main_scripts.index("network_stream_main_v55.js") < main_scripts.index("native_tool_stream_main_v63.js")
    assert scripts.index("content_request_v5.js") < scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_native_tool_stream_v63.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js")
    for retired in ("content_response_stream_recovery_v49.js", "content_response_stream_recovery_v69.js", "content_terminal_integrity_v89.js"):
        assert retired not in scripts
    assert scripts.index("content_runtime_contract_v48.js") < scripts.index("content_runtime_contract_v71.js")
    assert scripts[-1] == "content_runtime_contract_v71.js"


def test_dynamic_bootstrap_cannot_resurrect_retired_terminal_owners():
    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert "content_bundle_marker_v48.js" not in bootstrap
    for token in ("network_stream_main_v55.js","native_tool_stream_main_v63.js","content_rate_limit_guard_v52.js","content_tool_isolation_v48.js","content_draft_managed_recovery_v55.js","content_rich_response_v69.js","content_request_v6.js","content_network_stream_recovery_v55.js","content_native_tool_stream_v63.js","content_generation_liveness_v49.js","content_runtime_contract_v48.js","content_runtime_contract_v71.js"):
        assert token in bootstrap
    for retired in ("content_response_stream_recovery_v49.js", "content_response_stream_recovery_v69.js", "content_terminal_integrity_v89.js"):
        assert retired not in bootstrap


def test_background_preflight_requires_canonical_terminal_path_only():
    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    legacy_contract = (ROOT / "chrome_extension" / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    contract = (ROOT / "chrome_extension" / "content_runtime_contract_v71.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.22.95"' in preflight
    for token in ('"content_request_v6.js"','"content_network_stream_recovery_v55.js"','"content_response_semantic_recovery_v51.js"','"content_runtime_contract_v71.js"'):
        assert token in preflight
    assert '"content_response_stream_recovery_v49.js"' not in preflight
    assert '"content_response_stream_recovery_v69.js"' not in preflight
    assert 'REQUIRED_BUNDLE = "0.22.95"' in legacy_contract
    assert 'REQUIRED_BUNDLE = "0.22.95"' in contract
    assert 'response_terminal_owner: "request-v6"' in contract
    assert "__CHAT2API_REQUEST_CONTENT_V6__" in contract
    assert "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__" in contract
    assert "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__" not in contract
    assert "__CHAT2API_RESPONSE_STREAM_RECOVERY_V69__" not in contract


def test_network_stream_recovery_is_evidence_only():
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    network_main = (ROOT / "chrome_extension" / "network_stream_main_v55.js").read_text(encoding="utf-8")
    assert 'owner: "network-stream-evidence-v56"' in network
    assert 'network_response_recovery: "evidence-only-v56"' in network
    assert 'network_terminal_authority: "request-v6"' in network
    assert 'type: "chat.snapshot"' in network
    assert 'type: "chat.completed"' not in network
    assert 'active.cancelled = true' not in network
    assert 'const PARSER_REVISION = 63;' in network_main


def test_runtime_contract_still_exposes_supported_features():
    app = FastAPI(version=SERVER_RUNTIME_VERSION)
    payload = version_contract_payload(app)
    assert payload["server"]["runtime_version"] == SERVER_RUNTIME_VERSION
    assert payload["chrome_bridge"]["bundle_version"] == "0.22.95"
    assert payload["features"]["network_response_recovery"] is True
    assert payload["features"]["single_response_observer"] is True
    assert payload["features"]["worker_content_runtime_epoch_v71"] is True
