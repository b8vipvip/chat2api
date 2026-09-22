from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return ROOT.joinpath(path).read_text(encoding="utf-8")


def test_v02293_v0842_release_seals_single_window_lifecycle_authority() -> None:
    runtime = text("app/runtime_contract.py")
    manifest = text("chrome_extension/manifest.json")
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    guard = text("chrome_extension/conversation_persistent_pool_guard_v133.js")
    observer = text("chrome_extension/background_window_observer_v90.js")
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))

    assert SERVER_RUNTIME_VERSION == "0.22.94"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.22.94"
    assert '"version": "0.22.94"' in manifest
    assert "single-window-lifecycle-authority-v137-release-v02293" in runtime
    assert "single-window-lifecycle-authority-v137-release-v0842" in runtime
    assert payload["chrome_bridge"]["persistent_window_pool_revision"] == 132
    assert payload["chrome_bridge"]["persistent_window_pool_contract_revision"] == 135

    assert "await readiness.snapshot()" in pool
    assert "await readiness.readyForPrewarm()" not in pool

    assert 'reason: "delegated-to-persistent-window-pool-v137"' in guard
    assert 'await pool.reconcile(`login-state:${String(login?.state || "unknown")}`)' in guard
    assert "await chrome.windows.remove" not in guard
    assert "chrome.windows.create" not in guard
    assert "route.window_id = null" not in guard

    assert "decision_authority: false" in observer
