from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02291_v0840_responses_integrity_release_contract() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))

    assert SERVER_RUNTIME_VERSION == "0.22.91"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.40"
    assert manifest["version"] == "0.8.40"
    assert "responses-terminal-integrity-v110-worker-terminal-integrity-v89-release-v02291" in payload["server"]["feature_revision"]
    assert "responses-terminal-integrity-v89-release-v0840" in payload["chrome_bridge"]["build_revision"]
    assert payload["features"]["responses_terminal_integrity_v110"] is True
    assert payload["features"]["worker_terminal_integrity_v89"] is True


def test_v0840_seals_terminal_integrity_across_all_worker_activation_paths() -> None:
    manifest = read("chrome_extension/manifest.json")
    bootstrap = read("chrome_extension/content_bootstrap.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract48 = read("chrome_extension/content_runtime_contract_v48.js")
    contract71 = read("chrome_extension/content_runtime_contract_v71.js")

    assert "content_terminal_integrity_v89.js" in manifest
    assert "content_terminal_integrity_v89.js" in bootstrap
    assert "content_terminal_integrity_v89.js" in preflight
    assert "terminal_integrity_v89" in contract48
    assert "terminal_integrity_v89" in contract71
    for path in (
        "chrome_extension/content_bundle_marker_v48.js",
        "chrome_extension/content_bundle_marker_v71.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/background_runtime_preflight_v48.js",
    ):
        assert "0.8.40" in read(path), path


def test_v02291_keeps_responses_v110_transport_integrity_active() -> None:
    package = read("app/__init__.py")
    integrity = read("app/responses_protocol_integrity_v110_patch.py")
    assert "responses_protocol_integrity_v110_patch" in package
    assert "chat2api_tool_bridge_integrity" in integrity
    assert "v110" in integrity
    assert "exact marker" in integrity.lower()
