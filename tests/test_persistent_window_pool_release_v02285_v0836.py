from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import (
    CHROME_BRIDGE_BUNDLE_VERSION,
    SERVER_RUNTIME_VERSION,
    version_contract_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02285_v0836_release_contract_publishes_persistent_pool() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))

    assert SERVER_RUNTIME_VERSION == "0.22.86"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.37"
    assert manifest["version"] == "0.8.37"
    assert "persistent prewarmed per-Worker window pool" in manifest["description"]
    assert payload["server"]["runtime_aligned"] is True
    assert "release-v02285" in payload["server"]["feature_revision"]
    assert payload["chrome_bridge"]["persistent_window_pool_revision"] == 132
    assert payload["chrome_bridge"]["persistent_window_pool_contract_revision"] == 135
    assert "release-v0836" in payload["chrome_bridge"]["build_revision"]
    assert payload["features"]["persistent_worker_window_pool_v132"] is True
    assert payload["features"]["persistent_worker_window_pool_canonical_v135"] is True


def test_v0836_bundle_requires_new_worker_installation_epoch() -> None:
    for path in (
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
    ):
        assert "0.8.37" in read(path), path

    entry = read("chrome_extension/background_entry.js")
    assert '"conversation_persistent_pool_v132.js"' in entry
    assert '"conversation_persistent_pool_guard_v133.js"' in entry
    assert '"background_window_authority_v133.js"' in entry


def test_v02285_keeps_physical_pool_idle_close_disabled() -> None:
    runtime = read("app/v21_13_patch.py")
    assert "PERSISTENT_WINDOW_IDLE_CLOSE_SECONDS = 0" in runtime
    assert 'PERSISTENT_WINDOW_POLICY = "persistent-prewarmed-total-window-pool-v132"' in runtime
    assert 'WINDOW_DECISION_AUTHORITY = "persistent-window-pool-v132"' in runtime
