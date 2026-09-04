from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_worker_bundle_formally_seals_revisioned_content_epoch() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.24"
    main_scripts = manifest["content_scripts"][0]["js"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "multimodal_main_v78.js" in main_scripts
    assert "content_bundle_marker_v71.js" in scripts
    assert "content_runtime_contract_v71.js" in scripts
    assert scripts.index("content_multimodal_v78.js") < scripts.index("content_multimodal_v68.js")
    assert scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js")
    assert scripts.index("content_request_v6.js") < scripts.index("content_response_stream_recovery_v69.js")


def test_programmatic_bootstrap_matches_current_text_request_chain() -> None:
    bootstrap = (EXT / "content_bootstrap.js").read_text(encoding="utf-8")
    for name in (
        "multimodal_main_v78.js",
        "content_bundle_marker_v71.js",
        "content_multimodal_v78.js",
        "content_rich_response_v69.js",
        "content_request_v6.js",
        "content_response_stream_recovery_v69.js",
        "content_runtime_contract_v71.js",
    ):
        assert name in bootstrap
    assert 'world: "MAIN"' in bootstrap


def test_runtime_preflight_hot_heals_before_reload() -> None:
    preflight = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.8.24"' in preflight
    assert "REQUIRED_REVISION = 71" in preflight
    assert '"multimodal_main_v78.js"' in preflight
    assert 'world: "MAIN"' in preflight
    assert 'chat2api.runtime.contract.v71' in preflight
    assert "content_rich_response_v69.js" in preflight
    assert "content_request_v6.js" in preflight
    assert "content_response_stream_recovery_v69.js" in preflight
    assert "result?.modules?.multimodal_v78" in preflight
    assert "result?.modules?.multimodal_main_v78" in preflight
    assert "result = await heal(tabId)" in preflight
    assert "waitForReloadOrContract" in preflight
    assert "did not finish reloading for Worker runtime refresh" not in preflight


def test_v71_contract_preserves_full_v48_runtime_checks_and_current_text_epoch() -> None:
    contract = (EXT / "content_runtime_contract_v71.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.8.24"' in contract
    assert "REQUIRED_REVISION = 71" in contract
    for token in (
        "__CHAT2API_REQUEST_CONTENT_V6__",
        "__CHAT2API_RICH_RESPONSE_V69__",
        "__CHAT2API_RESPONSE_STREAM_RECOVERY_V69__",
        "__CHAT2API_MULTIMODAL_V4__",
        "multimodal_v78",
        "data-chat2api-multimodal-main-v78",
        "__CHAT2API_NETWORK_STREAM_RECOVERY_V55__",
        "data-chat2api-network-stream-parser",
        "__CHAT2API_TOOL_ISOLATION_V48__",
        "__CHAT2API_RATE_LIMIT_CONTENT_V52__",
        "removeListener(prior.listener)",
    ):
        assert token in contract


def test_runtime_preflight_v71_vm_regressions() -> None:
    completed = subprocess.run(
        ["node", str(ROOT / "tests" / "runtime_preflight_refresh_v71.mjs")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "runtime preflight v71 regression scenarios passed" in completed.stdout
