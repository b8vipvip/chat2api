import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_request_v5_never_accepts_history_hydration_as_submission() -> None:
    source = (EXTENSION / "content_request_v5.js").read_text(encoding="utf-8")
    assert 'request_controller: "request-v5"' in source
    assert '"confirmed-before-click"' not in source
    assert "users > active.beforeUsers" not in source
    assert "refreshAssistantBaseline(active)" in source
    assert "historical_hydration_ignored: true" in source
    assert "promptStillPresent(prompt)" in source
    assert "ready.button.click()" in source
    assert 'reason: `${reasonPrefix}-${result.reason}`' in source
    assert 'reason: "composer-cleared"' in source


def test_request_v5_is_loaded_after_v4() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_request_v5.js" in scripts
    assert scripts.index("content_request_v4.js") < scripts.index("content_request_v5.js")

    bootstrap = (EXTENSION / "content_bootstrap.js").read_text(encoding="utf-8")
    assert '"content_request_v5.js"' in bootstrap
    assert bootstrap.index('"content_request_v4.js"') < bootstrap.index('"content_request_v5.js"')


def test_voice_v4_annotation_does_not_rewrite_observed_aria_label() -> None:
    source = (EXTENSION / "content_voice_fix_v4.js").read_text(encoding="utf-8")
    assert 'annotation_strategy: "dataset-only"' in source
    assert 'button.dataset.chat2apiVoiceTrigger = "v4"' in source
    assert 'setAttribute("aria-label"' not in source
    assert "new MutationObserver" not in source
    assert "activeUntil" not in source


def test_voice_conversation_waits_for_transport_before_playing_prepared_audio() -> None:
    source = (EXTENSION / "voice_main.js").read_text(encoding="utf-8")
    assert "waitForRemoteTrackBeforeInput" in source
    assert "while (!state.remoteTrackSeen" in source
    assert "Voice transport did not become ready before prepared input playback" in source
    wait_at = source.index("const transportWaitMs = await waitForRemoteTrackBeforeInput")
    start_at = source.index("source.start();", wait_at)
    assert wait_at < start_at
    assert "transport_ready: true" in source
    assert "transport_wait_ms: transportWaitMs" in source


def test_runtime_log_export_uses_beijing_time_as_canonical_clock() -> None:
    popup = (EXTENSION / "popup_logging.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert "function beijingIso" in popup
    assert 'canonical_timezone: "Asia/Shanghai"' in popup
    assert "utc_offset_minutes: 480" in popup
    assert "generated_at_local" not in popup
    assert "entry.at_local" not in popup
    assert 'timezone: "Asia/Shanghai"' in background
    assert 'timestamp_timezone: "Asia/Shanghai"' in background
    assert "+08:00" in background
    assert "new Date().toISOString()" not in background
