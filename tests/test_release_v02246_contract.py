from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def test_formal_release_v02246_versions_and_notes_are_aligned() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert SERVER_RUNTIME_VERSION == "0.22.46"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.18"
    assert manifest["version"] == "0.8.18"
    assert (ROOT / "docs" / "releases" / "v0.22.46.md").is_file()


def test_formal_release_advertises_both_vision_repairs() -> None:
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["multimodal_revision"] == 78
    assert payload["features"]["multimodal_main_world_v78"] is True
    assert payload["features"]["model_capability_routing_v2"] is True
    assert "multimodal-main-world-v78" in payload["server"]["feature_revision"]
    assert "model-capability-routing-v2" in payload["server"]["feature_revision"]
