from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, CHROME_BRIDGE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def test_formal_release_v02256_versions_and_notes_are_aligned() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert SERVER_RUNTIME_VERSION == "0.22.56"
    assert CHROME_BRIDGE_VERSION == "0.8.1"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.26"
    assert manifest["version"] == "0.8.26"
    assert "multimodal_main_v78.js" in manifest["content_scripts"][0]["js"]
    assert "content_multimodal_v78.js" in manifest["content_scripts"][1]["js"]
    assert "content_multimodal_settle_v84.js" in manifest["content_scripts"][1]["js"]
    assert "content_request_terminal_prompt_v88.js" in manifest["content_scripts"][1]["js"]
    assert (ROOT / "docs" / "releases" / "v0.22.46.md").is_file()


def test_formal_release_advertises_v88_window_and_terminal_repairs() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["version"] == "0.8.1"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.26"
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
    assert "multimodal-main-world-v78" in payload["server"]["feature_revision"]
    assert "window-manager-fifo-v88" in payload["server"]["feature_revision"]
    assert "success-terminal-monotonic-v88" in payload["server"]["feature_revision"]
    assert "long-prompt-fast-insert-v88" in payload["server"]["feature_revision"]