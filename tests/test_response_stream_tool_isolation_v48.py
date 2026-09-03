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
    "chrome_extension/content_request_v6.js", "chrome_extension/content_response_stream_recovery_v49.js",
    "chrome_extension/content_response_stream_recovery_v69.js", "chrome_extension/content_network_stream_recovery_v55.js",
    "chrome_extension/content_response_semantic_recovery_v51.js", "chrome_extension/content_transient_retry_v50.js",
    "chrome_extension/content_generation_liveness_v49.js", "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/content_runtime_contract_v71.js", "chrome_extension/network_stream_main_v55.js",
    "chrome_extension/background_rate_limit_guard_v52.js", "chrome_extension/background_route_quarantine_v50.js",
    "chrome_extension/background_tool_isolation_v48.js", "chrome_extension/background_runtime_preflight_v48.js",
]


def test_v48_javascript_assets_parse():
    for filename in NEW_JS:
        result = subprocess.run(["node", "--check", str(ROOT / filename)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"{filename}: {result.stderr}"


def test_manifest_requires_fresh_document_marker_and_passive_recovery():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION == "0.8.20"
    main_scripts = manifest["content_scripts"][0]["js"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "network_stream_main_v55.js" in main_scripts
    assert "network_stream_main_v54.js" not in main_scripts
    assert scripts.index("content.js") < scripts.index("content_bundle_marker_v48.js") < scripts.index("content_bundle_marker_v71.js")
    assert scripts.index("content_ui_hygiene_v31.js") < scripts.index("content_rate_limit_guard_v52.js") < scripts.index("content_tool_isolation_v48.js")
    assert scripts.index("content_request_v5.js") < scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_response_stream_recovery_v69.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js")
    assert scripts.index("content_request_stall_guard_v34.js") < scripts.index("content_generation_liveness_v49.js")
    assert scripts.index("content_runtime_contract_v48.js") < scripts.index("content_runtime_contract_v71.js")
    assert scripts[-1] == "content_runtime_contract_v71.js"


def test_bundle_marker_cannot_be_spoofed_by_dynamic_bootstrap():
    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert "content_bundle_marker_v48.js" not in bootstrap
    assert "content_bundle_marker_v71.js" in bootstrap
    for token in ("network_stream_main_v55.js","content_rate_limit_guard_v52.js","content_tool_isolation_v48.js","content_draft_managed_recovery_v55.js","content_rich_response_v69.js","content_request_v6.js","content_response_stream_recovery_v69.js","content_network_stream_recovery_v55.js","content_generation_liveness_v49.js","content_runtime_contract_v48.js","content_runtime_contract_v71.js"):
        assert token in bootstrap


def test_background_preflight_wraps_final_conversation_dispatch():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index('"browser_tabs.js"') < entry.index('"background_rate_limit_guard_v52.js"') < entry.index('"background_tab_supervisor_v32.js"')
    assert entry.index('"conversation_dispatch.js"') < entry.index('"background_route_quarantine_v50.js"') < entry.index('"background_tool_isolation_v48.js"')
    assert entry.index('"background_tool_isolation_v48.js"') < entry.index('"background_runtime_preflight_v48.js"')
    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    legacy_contract = (ROOT / "chrome_extension" / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    contract = (ROOT / "chrome_extension" / "content_runtime_contract_v71.js").read_text(encoding="utf-8")
    legacy_marker = (ROOT / "chrome_extension" / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    marker = (ROOT / "chrome_extension" / "content_bundle_marker_v71.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.8.20"' in preflight
    assert 'REQUIRED_REVISION = 71' in preflight
    assert 'const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"]' in preflight
    for token in ('"content_rate_limit_guard_v52.js"','"content_request_lifecycle_v50.js"','"content_draft_managed_recovery_v55.js"','"content_rich_response_v69.js"','"content_request_v6.js"','"content_response_stream_recovery_v69.js"','"content_network_stream_recovery_v55.js"','"content_response_semantic_recovery_v51.js"','"content_transient_retry_v50.js"','"content_generation_liveness_v49.js"','"content_bundle_marker_v71.js"','"content_runtime_contract_v71.js"'):
        assert token in preflight
    assert '"content_response_stream_recovery_v49.js"' not in preflight
    assert 'REQUIRED_BUNDLE = "0.8.20"' in legacy_contract
    assert 'REQUIRED_BUNDLE = "0.8.20"' in contract
    assert 'REQUIRED_REVISION = 71' in contract
    for token in ("__CHAT2API_RATE_LIMIT_CONTENT_V52__","__CHAT2API_REQUEST_LIFECYCLE_V50__","__CHAT2API_DRAFT_MANAGED_RECOVERY_V55__","__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__","__CHAT2API_RESPONSE_STREAM_RECOVERY_V69__","__CHAT2API_NETWORK_STREAM_RECOVERY_V55__","network_stream_main_v55","network_stream_parser_v62","data-chat2api-network-stream-parser","__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__","response_single_owner_v53","semanticHelper?.timer == null","__CHAT2API_TRANSIENT_RETRY_V50__","__CHAT2API_GENERATION_LIVENESS_V49__"):
        assert token in contract
    assert 'bundle: "0.8.20"' in legacy_marker
    assert 'bundle: "0.8.20"' in marker
    assert 'revision: 71' in marker
    assert "content_bundle_marker_v48.js" not in preflight
    assert "chrome.tabs.reload" in preflight
    assert "waitForReloadOrContract" in preflight
    assert "ChatGPT tab Worker runtime is stale or incomplete" in preflight
    assert "chat2api.tool-isolation.preflight" in preflight


def test_response_stream_recovery_reports_first_text_completion_and_bounded_stall_state():
    source = (ROOT / "chrome_extension" / "content_response_stream_recovery_v49.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    network_main = (ROOT / "chrome_extension" / "network_stream_main_v55.js").read_text(encoding="utf-8")
    for token in ('type: "chat.snapshot"','type: "chat.completed"','type: "chat.error"','response_stream_recovery: "dom-turn-v49-single-owner-v53"','response_semantic_recovery: "role-shell-filter-integrated-v53"','page_progress_probe: "page-progress-v49"','page_probe_failure: "chatgpt-ui-stuck"',"stableMs >= 9000","baselineAssistantCount","integrationSurface(turn)","OBSERVER_GRACE_MS = 180000","IDLE_STUCK_MS = 25000","NON_IDLE_STUCK_MS = 45000","VISIBLE_GENERATION_STUCK_MS = 120000"):
        assert token in source
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
    assert SERVER_RUNTIME_VERSION == "0.22.50"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.20"
    assert payload["chrome_bridge"]["network_response_recovery_version"] == 55
    assert payload["chrome_bridge"]["network_response_parser_revision"] == 62
    assert payload["chrome_bridge"]["worker_master_switch_version"] == 61
    assert payload["chrome_bridge"]["worker_master_switch_revision"] == 62
    for feature in ("response_stream_recovery","network_response_recovery","network_response_parser_v62","single_response_observer","assistant_response_semantic_guard","assistant_response_semantic_recovery","model_capability_routing_guard","chatgpt_rate_limit_circuit_breaker","worker_window_reopen_loop_guard","active_rate_limit_terminal_error","routed_dispatch_terminal_error","admin_single_render_owner","worker_key_capacity_fifo_queue","worker_window_concurrency_controls","api_key_concurrency_controls","playground_chat_running_records","browser_page_progress_probe","same_api_parallel_requests","failed_route_quarantine","request_controller_lifecycle_guard","chatgpt_transient_retry","worker_runtime_preflight","external_account_tool_isolation","linux_worker_master_switch","linux_worker_disable_authority","worker_live_occupancy","worker_device_name_column","worker_pairing_rename","worker_presentation_console_liveness_v65","worker_presentation_console_liveness_v66","worker_column_registry_v67","multimodal_upload_confirmation_v64","multimodal_upload_v68","api_key_console_v68","rich_response_v69","request_response_epoch_v69","worker_content_runtime_epoch_v71","linux_worker_sudoers_guard","linux_worker_autoreload_self_heal","linux_worker_proxy_health_facets"):
        assert payload["features"][feature] is True
