import json
import subprocess
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


NEW_JS = [
    "chrome_extension/content_bundle_marker_v48.js",
    "chrome_extension/content_rate_limit_guard_v52.js",
    "chrome_extension/content_tool_isolation_v48.js",
    "chrome_extension/content_request_lifecycle_v50.js",
    "chrome_extension/content_response_stream_recovery_v49.js",
    "chrome_extension/content_network_stream_recovery_v55.js",
    "chrome_extension/content_response_semantic_recovery_v51.js",
    "chrome_extension/content_transient_retry_v50.js",
    "chrome_extension/content_generation_liveness_v49.js",
    "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/network_stream_main_v55.js",
    "chrome_extension/background_rate_limit_guard_v52.js",
    "chrome_extension/background_route_quarantine_v50.js",
    "chrome_extension/background_tool_isolation_v48.js",
    "chrome_extension/background_runtime_preflight_v48.js",
]


def test_v48_javascript_assets_parse():
    for filename in NEW_JS:
        result = subprocess.run(
            ["node", "--check", str(ROOT / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{filename}: {result.stderr}"


def test_manifest_requires_fresh_document_marker_and_passive_recovery():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION == "0.8.15"
    main_scripts = manifest["content_scripts"][0]["js"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "network_stream_main_v55.js" in main_scripts
    assert "network_stream_main_v54.js" not in main_scripts
    assert scripts.index("content.js") < scripts.index("content_bundle_marker_v48.js")
    assert scripts.index("content_ui_hygiene_v31.js") < scripts.index("content_rate_limit_guard_v52.js") < scripts.index("content_tool_isolation_v48.js")
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js")
    assert scripts.index("content_request_stall_guard_v34.js") < scripts.index("content_generation_liveness_v49.js")
    assert scripts[-1] == "content_runtime_contract_v48.js"


def test_bundle_marker_cannot_be_spoofed_by_dynamic_bootstrap():
    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert "content_bundle_marker_v48.js" not in bootstrap
    assert "network_stream_main_v55.js" in bootstrap
    assert "content_rate_limit_guard_v52.js" in bootstrap
    assert "content_tool_isolation_v48.js" in bootstrap
    assert "content_draft_managed_recovery_v55.js" in bootstrap
    assert "content_response_stream_recovery_v49.js" in bootstrap
    assert "content_network_stream_recovery_v55.js" in bootstrap
    assert "content_generation_liveness_v49.js" in bootstrap
    assert "content_runtime_contract_v48.js" in bootstrap


def test_background_preflight_wraps_final_conversation_dispatch():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index('"browser_tabs.js"') < entry.index('"background_rate_limit_guard_v52.js"') < entry.index('"background_tab_supervisor_v32.js"')
    assert entry.index('"conversation_dispatch.js"') < entry.index('"background_route_quarantine_v50.js"') < entry.index('"background_tool_isolation_v48.js"')
    assert entry.index('"background_tool_isolation_v48.js"') < entry.index('"background_runtime_preflight_v48.js"')
    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (ROOT / "chrome_extension" / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (ROOT / "chrome_extension" / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.8.15"' in preflight
    assert 'const MAIN_FILES = ["network_stream_main_v55.js"]' in preflight
    assert '"content_rate_limit_guard_v52.js"' in preflight
    assert '"content_request_lifecycle_v50.js"' in preflight
    assert '"content_draft_managed_recovery_v55.js"' in preflight
    assert '"content_response_stream_recovery_v49.js"' in preflight
    assert '"content_network_stream_recovery_v55.js"' in preflight
    assert '"content_response_semantic_recovery_v51.js"' in preflight
    assert '"content_transient_retry_v50.js"' in preflight
    assert '"content_generation_liveness_v49.js"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.15"' in contract
    assert "__CHAT2API_RATE_LIMIT_CONTENT_V52__" in contract
    assert "__CHAT2API_REQUEST_LIFECYCLE_V50__" in contract
    assert "__CHAT2API_DRAFT_MANAGED_RECOVERY_V55__" in contract
    assert "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__" in contract
    assert "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__" in contract
    assert "network_stream_main_v55" in contract
    assert "network_stream_parser_v62" in contract
    assert "data-chat2api-network-stream-parser" in contract
    assert "__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__" in contract
    assert "response_single_owner_v53" in contract
    assert "semanticHelper?.timer == null" in contract
    assert "__CHAT2API_TRANSIENT_RETRY_V50__" in contract
    assert "__CHAT2API_GENERATION_LIVENESS_V49__" in contract
    assert 'bundle: "0.8.15"' in marker
    assert "content_bundle_marker_v48.js" not in preflight
    assert "chrome.tabs.reload" in preflight
    assert "ChatGPT tab Worker runtime is stale or incomplete" in preflight
    assert "chat2api.tool-isolation.preflight" in preflight


def test_response_stream_recovery_reports_first_text_completion_and_bounded_stall_state():
    source = (ROOT / "chrome_extension" / "content_response_stream_recovery_v49.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    network_main = (ROOT / "chrome_extension" / "network_stream_main_v55.js").read_text(encoding="utf-8")
    assert 'type: "chat.snapshot"' in source
    assert 'type: "chat.completed"' in source
    assert 'type: "chat.error"' in source
    assert 'response_stream_recovery: "dom-turn-v49-single-owner-v53"' in source
    assert 'response_semantic_recovery: "role-shell-filter-integrated-v53"' in source
    assert 'page_progress_probe: "page-progress-v49"' in source
    assert 'page_probe_failure: "chatgpt-ui-stuck"' in source
    assert "stableMs >= 9000" in source
    assert "baselineAssistantCount" in source
    assert "integrationSurface(turn)" in source
    assert "OBSERVER_GRACE_MS = 180000" in source
    assert "IDLE_STUCK_MS = 25000" in source
    assert "NON_IDLE_STUCK_MS = 45000" in source
    assert "VISIBLE_GENERATION_STUCK_MS = 120000" in source
    assert 'network_response_recovery: "sse-assistant-v55"' in network
    assert 'response_stream_completion_reason: "conversation-sse-ended"' in network
    assert 'const PARSER_REVISION = 62;' in network_main


def test_external_account_tools_are_fail_closed_at_prompt_and_ui_layers():
    background = (ROOT / "chrome_extension" / "background_tool_isolation_v48.js").read_text(encoding="utf-8")
    content = (ROOT / "chrome_extension" / "content_tool_isolation_v48.js").read_text(encoding="utf-8")
    assert "External account-connected apps, plugins, connectors" in background
    assert "external_account_tools_disabled: true" in background
    assert "Treat plugin/connector/app names and @mentions" in background
    assert "preventPositiveAction" in content
    assert 'document.addEventListener("click", preventPositiveAction, true)' in content
    assert "negativeAction" in content
    assert "重新连接" in content
    assert "暂不" in content
    assert "external_account_tools_disabled: true" in content


def test_runtime_contract_exposes_v48_v49_v50_v51_and_v55_features():
    app = FastAPI(version=SERVER_RUNTIME_VERSION)
    payload = version_contract_payload(app)
    assert SERVER_RUNTIME_VERSION == "0.22.42"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.15"
    assert payload["chrome_bridge"]["network_response_recovery_version"] == 55
    assert payload["chrome_bridge"]["network_response_parser_revision"] == 62
    assert payload["chrome_bridge"]["worker_master_switch_version"] == 61
    assert payload["chrome_bridge"]["worker_master_switch_revision"] == 62
    assert payload["features"]["response_stream_recovery"] is True
    assert payload["features"]["network_response_recovery"] is True
    assert payload["features"]["network_response_parser_v62"] is True
    assert payload["features"]["single_response_observer"] is True
    assert payload["features"]["assistant_response_semantic_guard"] is True
    assert payload["features"]["assistant_response_semantic_recovery"] is True
    assert payload["features"]["model_capability_routing_guard"] is True
    assert payload["features"]["chatgpt_rate_limit_circuit_breaker"] is True
    assert payload["features"]["worker_window_reopen_loop_guard"] is True
    assert payload["features"]["active_rate_limit_terminal_error"] is True
    assert payload["features"]["routed_dispatch_terminal_error"] is True
    assert payload["features"]["admin_single_render_owner"] is True
    assert payload["features"]["worker_key_capacity_fifo_queue"] is True
    assert payload["features"]["worker_window_concurrency_controls"] is True
    assert payload["features"]["api_key_concurrency_controls"] is True
    assert payload["features"]["playground_chat_running_records"] is True
    assert payload["features"]["browser_page_progress_probe"] is True
    assert payload["features"]["same_api_parallel_requests"] is True
    assert payload["features"]["failed_route_quarantine"] is True
    assert payload["features"]["request_controller_lifecycle_guard"] is True
    assert payload["features"]["chatgpt_transient_retry"] is True
    assert payload["features"]["worker_runtime_preflight"] is True
    assert payload["features"]["external_account_tool_isolation"] is True
    assert payload["features"]["linux_worker_master_switch"] is True
    assert payload["features"]["linux_worker_disable_authority"] is True
    assert payload["features"]["worker_live_occupancy"] is True
    assert payload["features"]["linux_worker_sudoers_guard"] is True
    assert payload["features"]["linux_worker_autoreload_self_heal"] is True
    assert payload["features"]["linux_worker_proxy_health_facets"] is True
