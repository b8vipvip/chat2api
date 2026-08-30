from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_runtime_versions_and_features() -> None:
    source = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.37"' in source
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.10"' in source
    for marker in (
        '"failed_route_quarantine": True',
        '"request_controller_lifecycle_guard": True',
        '"chatgpt_transient_retry": True',
        '"linux_worker_autoreload_self_heal": True',
        '"assistant_response_semantic_guard": True',
        '"assistant_response_semantic_recovery": True',
        '"model_capability_routing_guard": True',
        '"chatgpt_rate_limit_circuit_breaker": True',
        '"worker_window_reopen_loop_guard": True',
        '"playground_chat_running_records": True',
    ):
        assert marker in source


def test_manifest_loads_lifecycle_and_retry_overlays_in_order() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.10"
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_rate_limit_guard_v52.js") < scripts.index("content_request_v5.js")
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js")
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
    assert "消息发送超时" in source
    assert "message (?:send )?timed out" in source
    assert "上下文" in source and "maximum context" in source
    assert "plugin|connector" in source
    assert "transient_retry_same_request: true" in source
    assert "resetRecoveryClock" in source


def test_terminal_route_is_quarantined_before_request_reservation_release() -> None:
    source = (EXT / "background_route_quarantine_v50.js").read_text(encoding="utf-8")
    assert "workers.releaseRequest = function releaseRequestWithQuarantine" in source
    assert source.index("snapshotBeforeRelease(requestId)") < source.index("return baseReleaseRequest(requestId)")
    assert "route.inflight_request_id = snapshot.sentinel" in source
    assert "router.activeRequests.set(snapshot.sentinel" in source
    assert "completed-controller-did-not-settle-v50" in source
    assert "chat2api.lifecycle-status.v50" in source


def test_background_entry_loads_quarantine_after_worker_router() -> None:
    source = (EXT / "background_entry.js").read_text(encoding="utf-8")
    assert source.index('"browser_tabs.js"') < source.index('"background_rate_limit_guard_v52.js"')
    assert source.index('"conversation_workers_v25.js"') < source.index('"background_route_quarantine_v50.js"')
    assert source.index('"background_route_quarantine_v50.js"') < source.index('"background_request_recovery_v40.js"')


def test_runtime_preflight_requires_v50_and_v51_overlays() -> None:
    source = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (EXT / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (EXT / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    assert 'const REQUIRED_BUNDLE = "0.8.10"' in source
    assert '"content_rate_limit_guard_v52.js"' in source
    assert '"content_request_lifecycle_v50.js"' in source
    assert '"content_response_semantic_recovery_v51.js"' in source
    assert '"content_transient_retry_v50.js"' in source
    assert 'const REQUIRED_BUNDLE = "0.8.10"' in contract
    assert "rate_limit_guard_v52" in contract
    assert "request_lifecycle_v50" in contract
    assert "response_semantic_recovery_v51" in contract
    assert "transient_retry_v50" in contract
    assert 'bundle: "0.8.10"' in marker


def test_linux_autoreload_wrapper_self_repairs_missing_base_helper() -> None:
    source = (ROOT / "scripts" / "linux_extension_autoreload_v43.sh").read_text(encoding="utf-8")
    assert "repair_base_script()" in source
    assert "/bootstrap/linux-worker-bundle.json" in source
    assert "/bootstrap/linux-worker-bundle.tar.gz" in source
    assert "scripts/linux_extension_autoreload.sh" in source
    assert "sha256sum -c" in source
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/linux_extension_autoreload.sh" in dockerfile


def test_new_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        return
    for name in (
        "content_rate_limit_guard_v52.js",
        "background_rate_limit_guard_v52.js",
        "content_request_lifecycle_v50.js",
        "content_response_semantic_recovery_v51.js",
        "content_transient_retry_v50.js",
        "background_route_quarantine_v50.js",
        "background_runtime_preflight_v48.js",
        "content_runtime_contract_v48.js",
        "content_bundle_marker_v48.js",
    ):
        subprocess.run([node, "--check", str(EXT / name)], check=True, capture_output=True, text=True)