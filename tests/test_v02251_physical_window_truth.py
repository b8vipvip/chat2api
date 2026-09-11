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


def test_legacy_v83_compaction_is_not_loaded_and_v90_observer_is_read_only() -> None:
    legacy = text("chrome_extension/background_window_truth_v83.js")
    observer = text("chrome_extension/background_window_observer_v90.js")
    entry = text("chrome_extension/background_entry.js")

    # Keep the historical implementation inspectable, but do not let it own
    # production window lifecycle after the v30 single-authority cutover.
    assert "chrome.tabs.move(initTabId" in legacy
    assert "liveChatGptWindowIds" in legacy
    assert "chat2apiInitializationCompactedAtV83" in legacy
    assert '"background_window_truth_v83.js"' not in entry

    assert '"background_window_observer_v90.js"' in entry
    assert 'policy: "observe-only-single-route-authority-v90"' in observer
    assert "decision_authority: false" in observer
    assert "speculative_windows: false" in observer
    assert "chrome.windows.create" not in observer
    assert "chrome.windows.remove" not in observer


def test_physical_window_truth_runtime_and_worker_bundle_contract() -> None:
    assert SERVER_RUNTIME_VERSION
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.31"
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION
    runtime = text("app/runtime_contract.py")
    assert "physical-window-truth-v83" in runtime
    assert '"window_observer_v90": True' in runtime
    assert '"worker_physical_window_live_truth_v89": False' in runtime
