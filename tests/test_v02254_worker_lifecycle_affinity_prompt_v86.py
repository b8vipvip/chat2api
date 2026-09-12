from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_disable_guard_is_admission_only_and_never_closes_after_create() -> None:
    source = text("chrome_extension/background_worker_disabled_window_guard_v86.js")
    entry = text("chrome_extension/background_entry.js")
    assert '"background_worker_disabled_window_guard_v86.js"' in entry
    assert 'DISABLED_KEY = "chat2apiWorkerMasterDisabledV61"' in source
    assert "Worker is disabled; managed ChatGPT window creation is blocked by v86" in source
    assert source.count("if (await disabled())") == 1
    assert "chrome.windows.remove" not in source
    assert "return baseCreate(options, meta)" in source
    assert "lifecycle transition belongs to conversation_routing.js" in source


def test_worker_disable_guard_vm_contract() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "worker_disabled_window_guard_v86.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "worker disabled window guard v86 admission-only contract: ok" in result.stdout


def test_legacy_route_quarantine_source_is_not_a_production_owner() -> None:
    source = text("chrome_extension/background_route_quarantine_v50.js")
    entry = text("chrome_extension/background_entry.js")
    assert "async function settleCompletedRoute" in source
    assert '"background_route_quarantine_v50.js"' not in entry


def test_runtime_preflight_has_current_bundle_fast_path_before_heal() -> None:
    source = text("chrome_extension/background_runtime_preflight_v48.js")
    preflight = source[source.index("async function preflight"):]
    assert 'REQUIRED_BUNDLE = "0.8.35"' in source
    assert "fast_path_hits" in source
    assert "CONTRACT_TIMEOUT_MS = 700" in source
    assert "HOT_HEAL_BUDGET_MS = 2400" in source
    assert "RELOAD_BUDGET_MS = 3500" in source
    assert "FINAL_HEAL_BUDGET_MS = 1800" in source
    assert "Promise.race([request, timeout])" in source
    assert 'mode: "current-fast-path-v87"' in source
    assert '"hot-repair-v87"' in source
    assert '"reload-repair-v87"' in source
    first_contract = preflight.index("result = await contract(tabId)")
    current_check = preflight.index("if (current(result))")
    first_heal = preflight.index("result = await heal(tabId)")
    assert first_contract < current_check < first_heal


def test_conversation_viewer_action_is_owned_by_canonical_request_renderer() -> None:
    presentation = text("app/admin_prompt_config_v75.js")
    prompt_ui = text("app/admin_prompt_config_v72.js")
    requests = text("app/request_history_v94_patch.py")
    assert 'tr.getAttribute("onclick")' not in presentation
    assert "repairPromptCells" not in presentation
    assert "MutationObserver" not in presentation
    assert "function promptColumnIndex()" not in presentation
    assert "window.showRequestPromptV72 = showRequestPrompt" in prompt_ui
    assert "window.showRequestConversationV97=requestHistoryShowConversation" in requests
    assert "查看本次请求的提示词和生成回复" in requests
    assert "event.stopPropagation()" in requests
    assert "requestHistoryButton" in requests


def test_modified_v86_javascript_parses() -> None:
    paths = [
        "app/admin_prompt_config_v75.js",
        "chrome_extension/background_worker_disabled_window_guard_v86.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/content_request_v6.js",
    ]
    for path in paths:
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_worker_lifecycle_runtime_contract_and_worker_bundle_are_aligned() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert SERVER_RUNTIME_VERSION == "0.22.82"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.35"
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION
    for path in [
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/background_runtime_preflight_v48.js",
    ]:
        assert CHROME_BRIDGE_BUNDLE_VERSION in text(path), path
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["worker_disabled_window_guard_v86"] is True
    assert payload["features"]["worker_single_route_authority_v30"] is True
    assert payload["features"]["linux_worker_device_console_v122"] is False
    assert payload["features"]["speculative_worker_windows"] is False