from __future__ import annotations

import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_occupancy_prefers_fresh_verified_managed_windows() -> None:
    source = text("app/admin_worker_presentation_v66.js")
    expected = "[metadata.reserve_window_all_chatgpt_windows, metadata.reserve_window_total]"
    assert expected in source  # explicit legacy fallback remains for old servers
    assert "WINDOW_TRUTH_REVISION = 89" in source
    assert "liveVerified" in source
    assert "physical: authoritative && worker?.live_verified === true" in source
    assert "旧版遥测（未实时核验）" in source
    assert 'data-chat2api-live-window-count="1"' in source


def test_reserve_status_always_reports_all_chatgpt_window_count() -> None:
    source = text("chrome_extension/background_reserve_pool_v29.js")
    assert "reserve_window_all_chatgpt_windows" in source
    assert "snapshot.live instanceof Set ? snapshot.live.size : snapshot.total" in source


def test_initialization_tab_is_compacted_into_a_worker_window() -> None:
    source = text("chrome_extension/background_window_truth_v83.js")
    entry = text("chrome_extension/background_entry.js")
    assert '"background_window_truth_v83.js"' in entry
    assert "chrome.tabs.move(initTabId" in source
    assert "liveChatGptWindowIds" in source
    assert "reserve_window_all_chatgpt_windows" in source
    assert "chat2apiInitializationCompactedAtV83" in source
    assert "if (candidates.includes(initWindowId))" in source
    assert 'reason: "already-shared"' in source
    assert "reserve_window_initialization_shared" in source


def test_v02255_runtime_and_worker_bundle_contract() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.58"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.27"
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert manifest["version"] == "0.8.27"
    assert "physical-window-truth-v83" in text("app/runtime_contract.py")
