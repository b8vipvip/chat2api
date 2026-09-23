from __future__ import annotations

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


def test_v02287_login_ready_release_contract() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert SERVER_RUNTIME_VERSION == "0.22.96"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.22.96"
    assert payload["server"]["runtime_aligned"] is True
    assert "chatgpt-login-ready-admission-v137" in payload["server"]["feature_revision"]
    assert "window-manager-filter-v137" in payload["server"]["feature_revision"]
    assert "release-v02287" in payload["server"]["feature_revision"]
    assert payload["features"]["chatgpt_login_ready_admission_v137"] is True
    assert payload["features"]["window_manager_login_ready_filter_v137"] is True


def test_v02287_is_server_only_and_keeps_worker_bundle_0837() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["bundle_version"] == "0.22.96"
    assert "release-v0837" in payload["chrome_bridge"]["build_revision"]
