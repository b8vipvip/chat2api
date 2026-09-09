from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, CHROME_BRIDGE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def test_formal_release_v02269_versions_and_carried_notes_are_aligned() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert SERVER_RUNTIME_VERSION == "0.22.69"
    assert CHROME_BRIDGE_VERSION == "0.8.1"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.29"
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION
    assert "native_tool_stream_main_v63.js" in manifest["content_scripts"][0]["js"]
    assert "multimodal_main_v78.js" in manifest["content_scripts"][0]["js"]
    assert "content_native_tool_stream_v63.js" in manifest["content_scripts"][1]["js"]
    assert "content_multimodal_v78.js" in manifest["content_scripts"][1]["js"]
    assert "content_multimodal_settle_v84.js" in manifest["content_scripts"][1]["js"]
    assert "content_request_terminal_prompt_v88.js" in manifest["content_scripts"][1]["js"]
    assert "content_conversation_quota_failover_v95.js" in manifest["content_scripts"][1]["js"]
    assert "content_ui_hygiene_v31.js" in manifest["content_scripts"][1]["js"]
    background_entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"background_file_upload_quota_recycle_v96.js"' in background_entry
    # v0.22.69 carries the v0.22.67 user-console/billing release and v0.22.68
    # payment layer while adding the Responses protocol stack.
    assert "## v0.22.67" in changelog
    assert "### User console" in changelog
    assert "### Pricing and billing" in changelog
    assert "### Payments" in changelog
    assert "`价格配置`" in changelog
    assert "`支付配置`" in changelog


def test_formal_release_advertises_responses_bridge_and_carried_runtime_fixes() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["version"] == "0.8.1"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.29"
    assert payload["chrome_bridge"]["multimodal_revision"] == 85
    assert payload["features"]["multimodal_main_world_v78"] is True
    assert payload["features"]["multimodal_upload_ready_v84"] is True
    assert payload["features"]["model_capability_routing_v2"] is True
    assert payload["features"]["worker_window_fifo_manager_v88"] is True
    assert payload["features"]["worker_window_lifecycle_observer_v88"] is True
    assert payload["features"]["successful_terminal_monotonic_v88"] is True
    assert payload["features"]["long_prompt_fast_insert_v88"] is True
    assert payload["features"]["admin_window_manager_v88"] is True
    assert payload["features"]["request_id_window_correlation_v88"] is True
    assert payload["features"]["conversation_quota_failover_v95"] is True
    assert payload["features"]["file_upload_quota_terminal_recycle_v96"] is True
    assert payload["features"]["request_history_conversation_viewer_v97"] is True
    assert payload["features"]["server_update_direct_start_v97"] is True
    assert payload["features"]["worker_ui_hygiene_health_modal_v101"] is True
    assert payload["features"]["user_console_v104"] is True
    assert payload["features"]["user_account_api_key_isolation_v104"] is True
    assert payload["features"]["user_pricing_billing_v104"] is True
    assert payload["features"]["user_payment_zpay_v104"] is True
    assert payload["features"]["user_payment_channels_v106"] is True
    assert payload["features"]["user_payment_paypal_v106"] is True
    assert payload["features"]["user_payment_usdt_trc20_v106"] is True
    assert payload["features"]["user_payment_settlement_safety_v107"] is True
    assert payload["features"]["native_tool_stream_v63"] is True
    assert payload["features"]["responses_api_v108"] is True
    assert payload["features"]["responses_native_web_search_v108"] is True
    assert payload["features"]["responses_emulated_tools_v109"] is True
    assert payload["features"]["responses_function_call_output_v109"] is True
    assert payload["features"]["responses_owner_isolation_v109"] is True
    assert "multimodal-main-world-v78" in payload["server"]["feature_revision"]
    assert "window-manager-fifo-v88" in payload["server"]["feature_revision"]
    assert "success-terminal-monotonic-v88" in payload["server"]["feature_revision"]
    assert "long-prompt-fast-insert-v88" in payload["server"]["feature_revision"]
    assert "conversation-quota-failover-v95" in payload["server"]["feature_revision"]
    assert "file-upload-quota-terminal-recycle-v96" in payload["server"]["feature_revision"]
    assert "request-history-conversation-v97" in payload["server"]["feature_revision"]
    assert "server-update-direct-start-v97" in payload["server"]["feature_revision"]
    assert "diagnostic-runtime-truth-v100" in payload["server"]["feature_revision"]
    assert "health-promo-safe-dismiss-v101" in payload["server"]["feature_revision"]
    assert "release-v02266" in payload["server"]["feature_revision"]
    assert "user-console-v104" in payload["server"]["feature_revision"]
    assert "user-commerce-v104" in payload["server"]["feature_revision"]
    assert "pricing-v104" in payload["server"]["feature_revision"]
    assert "payment-v104" in payload["server"]["feature_revision"]
    assert "release-v02267" in payload["server"]["feature_revision"]
    assert "payment-channels-v106" in payload["server"]["feature_revision"]
    assert "payment-settlement-safety-v107" in payload["server"]["feature_revision"]
    assert "release-v02268" in payload["server"]["feature_revision"]
    assert "native-responses-v108" in payload["server"]["feature_revision"]
    assert "emulated-responses-tools-v109" in payload["server"]["feature_revision"]
    assert "release-v02269" in payload["server"]["feature_revision"]
