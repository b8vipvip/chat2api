from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_runtime_versions_and_features() -> None:
    source = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.94"' in source
    assert '"network_response_recovery": True' in source
    assert '"single_response_observer": True' in source
    assert '"worker_content_runtime_epoch_v71": True' in source


def test_manifest_loads_lifecycle_and_retry_overlays_in_order() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.22.94"
    assert "network_stream_main_v55.js" in manifest["content_scripts"][0]["js"]
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_rate_limit_guard_v52.js") < scripts.index("content_request_v5.js") < scripts.index("content_request_v6.js")
    assert scripts.index("content_request_v6.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js")
    assert scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js")
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert scripts.index("content_transient_retry_v50.js") < scripts.index("content_runtime_contract_v48.js")


def test_busy_tab_is_rejected_explicitly_instead_of_silent_promise_failure() -> None:
    source = (EXT / "content_request_lifecycle_v50.js").read_text(encoding="utf-8")
    assert "Target ChatGPT tab is still finalizing the previous request" in source
    assert "request_lifecycle_busy_rejected" in source
    assert "chat2api.lifecycle-status.v50" in source
    assert "removeListener(oldListener)" in source


def test_transient_retry_is_bounded_and_excludes_non_retryable_states() -> None:
    source = (EXT / "content_transient_retry_v50.js").read_text(encoding="utf-8")
    assert "const MAX_RETRIES = 2" in source
    assert "transient_retry_same_request: true" in source
    assert "resetRecoveryClock" in source


def test_background_entry_retires_quarantine_and_uses_v30_router() -> None:
    source = (EXT / "background_entry.js").read_text(encoding="utf-8")
    assert source.index('"browser_tabs.js"') < source.index('"background_rate_limit_guard_v52.js"')
    assert '"conversation_workers_v25.js"' not in source
    assert '"background_route_quarantine_v50.js"' not in source
    assert '"background_request_recovery_v40.js"' not in source
    assert source.index('"background_route_close_terminal_v91.js"') < source.index('"conversation_routing.js"')
    assert source.index('"conversation_routing.js"') < source.index('"conversation_dispatch.js"')


def test_runtime_preflight_requires_request_v6_and_evidence_overlays() -> None:
    source = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (EXT / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (EXT / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    assert 'const REQUIRED_BUNDLE = "0.22.94"' in source
    assert 'const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"]' in source
    for token in ('"content_request_v6.js"','"content_rate_limit_guard_v52.js"','"content_request_lifecycle_v50.js"','"content_draft_managed_recovery_v55.js"','"content_network_stream_recovery_v55.js"','"content_response_semantic_recovery_v51.js"','"content_transient_retry_v50.js"'):
        assert token in source
    assert '"content_response_stream_recovery_v69.js"' not in source
    assert 'const REQUIRED_BUNDLE = "0.22.94"' in contract
    for token in ("request_v6","rate_limit_guard_v52","request_lifecycle_v50","draft_managed_recovery_v55","network_stream_recovery_v55","network_stream_main_v55","response_semantic_recovery_v51","semanticHelper?.timer == null","transient_retry_v50"):
        assert token in contract
    assert 'response_single_owner_v53' not in contract
    assert 'bundle: "0.22.94"' in marker
