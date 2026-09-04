from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_disable_guard_blocks_refill_and_closes_late_window() -> None:
    source = text("chrome_extension/background_worker_disabled_window_guard_v86.js")
    entry = text("chrome_extension/background_entry.js")
    assert '"background_worker_disabled_window_guard_v86.js"' in entry
    assert 'STORAGE_KEY = "chat2apiWorkerMasterDisabledV61"' in source
    assert "Worker is disabled; managed window refill is blocked" in source
    assert "if (state.disabled && Number.isInteger(created?.id))" in source
    assert "await chrome.windows.remove(created.id)" in source
    assert "base(meta)" in source


def test_successful_terminal_never_recycles_affinity_window() -> None:
    source = text("chrome_extension/background_route_quarantine_v50.js")
    start = source.index("async function settleCompletedRoute")
    end = source.index("chrome.runtime.onMessage.addListener", start)
    completed = source[start:end]
    assert "resetFailedRoute(" not in completed
    assert "clearSentinel(snapshot)" in completed
    assert 'action: "preserved"' in completed
    assert "completed_controller_still_active" in completed
    assert "completed_settle_timeout_ms" in source
    assert "ERROR_RECYCLE_DELAY_MS" in source


def test_runtime_preflight_has_current_bundle_fast_path_before_heal() -> None:
    source = text("chrome_extension/background_runtime_preflight_v48.js")
    preflight = source[source.index("async function preflight"):]
    assert "fast_path_hits" in source
    assert 'mode: "current-fast-path-v86"' in source
    assert 'mode: "hot-repair-v86"' in source
    assert 'mode: "reload-repair-v86"' in source
    first_contract = preflight.index("result = await contract(tabId)")
    current_check = preflight.index("if (current(result))")
    first_heal = preflight.index("result = await heal(tabId)")
    assert first_contract < current_check < first_heal


def test_prompt_viewer_recovers_request_id_from_current_row_renderer() -> None:
    source = text("app/admin_prompt_config_v75.js")
    assert 'tr.getAttribute("onclick")' in source
    assert "function promptColumnIndex()" in source
    assert "function ensurePromptCell(tr, index)" in source
    assert 'button.textContent = "查看提示词"' in source
    assert "window.showRequestPromptV72(requestId)" in source
    assert "event.stopPropagation()" in source
    assert 'new MutationObserver(schedule).observe(body, { childList: true, subtree: true, attributes: true })' in source


def test_modified_v86_javascript_parses() -> None:
    paths = [
        "app/admin_prompt_config_v75.js",
        "chrome_extension/background_worker_disabled_window_guard_v86.js",
        "chrome_extension/background_route_quarantine_v50.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
    ]
    for path in paths:
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_v02254_runtime_contract_and_worker_bundle_are_aligned() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert SERVER_RUNTIME_VERSION == "0.22.54"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.24"
    assert manifest["version"] == "0.8.24"
    for path in [
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/background_runtime_preflight_v48.js",
    ]:
        assert "0.8.24" in text(path), path
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["worker_disabled_window_guard_v86"] is True
    assert payload["features"]["successful_route_preservation_v86"] is True
    assert payload["features"]["runtime_preflight_fast_path_v86"] is True
    assert payload["features"]["request_prompt_viewer_repair_v86"] is True
