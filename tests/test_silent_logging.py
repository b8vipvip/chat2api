import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_runtime_logs_persist_silently_without_automatic_downloads() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert "storage" in manifest["permissions"]
    assert "unlimitedStorage" in manifest["permissions"]
    assert "alarms" in manifest["permissions"]

    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'ACTIVE_RUNS_KEY = "chat2apiRuntimeActiveRunsV4"' in source
    assert 'RUN_INDEX_KEY = "chat2apiRuntimeRunIndexV4"' in source
    assert 'RUN_PART_PREFIX = "chat2apiRuntimeRunPartV4:"' in source
    assert "RUN_IDLE_MS = 120000" in source
    assert "TARGET_BYTES = 200 * 1024" in source
    assert "MAX_FINALIZED_RUNS = 64" in source
    assert "chrome.storage.local.set" in source
    assert "archiveCurrentPart" in source
    assert "exportStoredRuns" in source
    assert 'backend: "chrome.storage.local"' in source
    assert "silent_persistence: true" in source
    assert "automatic_downloads: false" in source
    assert "sessionized_by_api_key: true" in source
    assert "chrome.downloads.download" not in source


def test_rollover_archives_before_adding_a_line_that_would_split_a_part() -> None:
    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    threshold_at = source.index("run.current_bytes + lineBytes > TARGET_BYTES")
    archive_at = source.index("await archiveCurrentPart(run);", threshold_at)
    append_at = source.index("run.current_lines.push(line)", archive_at)
    assert threshold_at < archive_at < append_at
    assert "JSON.stringify(entry)" in source
    assert 'bytesOf(line + "\\n")' in source


def test_runtime_logs_are_sessionized_by_api_key_and_finalize_after_idle() -> None:
    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'message?.routing?.api_key_id || "unrouted"' in source
    assert 'message?.routing?.api_key_kind || "unknown"' in source
    assert '"request_start"' in source
    assert '"request_end"' in source
    assert "active_request_ids" in source
    assert "scheduleRunFinalize(run)" in source
    assert "chrome.alarms.create(alarmName(run.run_id), { when: run.idle_deadline })" in source
    assert 'finalizeRun(runId, "idle-120s")' in source
    assert "state.activeRuns.get(keyId)" in source


def test_popup_export_remains_explicit_user_action() -> None:
    popup = (EXTENSION / "popup_logging.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'download.addEventListener("click"' in popup
    assert 'type: "popup.logs.export"' in popup
    assert 'message?.type === "popup.logs.export"' in background
    assert "runs: exported.runs" in background
    assert "chunks: exported.chunks" in background
