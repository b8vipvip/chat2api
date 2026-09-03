from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02253_versions_and_v85_safe_submit_are_shipped() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert SERVER_RUNTIME_VERSION == "0.22.53"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.23"
    assert manifest["version"] == "0.8.23"
    scripts = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")["js"]
    assert scripts.index("content_multimodal_settle_v84.js") < scripts.index("content_multimodal_settle_v85.js")
    assert scripts.index("content_multimodal_settle_v85.js") < scripts.index("content_multimodal_v68.js")
    source = text("chrome_extension/content_multimodal_settle_v85.js")
    assert "const REVISION = 85" in source
    assert '"::before"' in source and '"::after"' in source
    assert "animateTransform" in source
    assert "return 8000" in source
    assert "return 12000" in source
    assert "return 18000" in source
    assert "attachment_safe_submit_revision" in source
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["multimodal_revision"] == 85
    assert payload["features"]["multimodal_safe_submit_v85"] is True


def test_same_key_affinity_is_five_minutes_in_both_deadline_owners() -> None:
    routing = text("chrome_extension/conversation_routing.js")
    reserve = text("chrome_extension/background_reserve_pool_v29.js")
    assert "const IDLE_CLOSE_MS = 5 * 60 * 1000" in routing
    assert "const ROUTE_IDLE_CLOSE_MS = 5 * 60 * 1000" in reserve
    assert "max_turns: MAX_TURNS" in routing
    assert "max_text_chars: MAX_TEXT_CHARS" in routing
    assert "max_attachments: MAX_ATTACHMENTS" in routing


def test_tab_supervisor_never_counts_routed_affinity_window_as_spare_overflow() -> None:
    source = text("chrome_extension/background_tab_supervisor_v32.js")
    assert 'const spareKinds = new Set(["reserve", "warm", "external-warm"])' in source
    assert "const excess = Math.max(0, spareSurviving.length - target)" in source
    assert "candidates = spareSurviving" in source
    assert 'worker_target_semantics: "spares-only-v85"' in source


def test_managed_window_openings_share_a_fifteen_second_global_gate() -> None:
    gate = text("chrome_extension/background_window_open_stagger_v85.js")
    entry = text("chrome_extension/background_entry.js")
    assert "const MIN_INTERVAL_MS = 15 * 1000" in gate
    assert "state.tail.then(run, run)" in gate
    assert '"background_window_open_stagger_v85.js"' in entry
    for path in [
        "chrome_extension/conversation_routing.js",
        "chrome_extension/conversation_warm_pool_v2.js",
        "chrome_extension/background_reserve_pool_v29.js",
        "chrome_extension/background_external_warm_v28.js",
        "chrome_extension/background_tab_supervisor_v32.js",
    ]:
        source = text(path)
        assert "chat2apiCreateWindowStaggered" in source, path


def test_runtime_preflight_requires_v85() -> None:
    preflight = text("chrome_extension/background_runtime_preflight_v48.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")
    assert '"content_multimodal_settle_v85.js"' in preflight
    assert "result?.modules?.multimodal_v85" in preflight
    assert "multimodal revision 85" in preflight
    assert "multimodal_v85" in contract
    assert 'typeof multimodal?.waitForSafeSubmit === "function"' in contract
