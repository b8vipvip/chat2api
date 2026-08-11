import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_runtime_logs_persist_silently_without_automatic_downloads() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert "storage" in manifest["permissions"]
    assert "unlimitedStorage" in manifest["permissions"]

    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'CURRENT_CHUNK_KEY = "chat2apiRuntimeChunkV3"' in source
    assert 'CHUNK_INDEX_KEY = "chat2apiRuntimeChunkIndexV3"' in source
    assert 'CHUNK_PREFIX = "chat2apiRuntimeChunkFileV3:"' in source
    assert "TARGET_BYTES = 200 * 1024" in source
    assert "MAX_ARCHIVED_CHUNKS = 64" in source
    assert "chrome.storage.local.set" in source
    assert "archiveCurrentChunk" in source
    assert "exportStoredChunks" in source
    assert 'backend: "chrome.storage.local"' in source
    assert "silent_persistence: true" in source
    assert "automatic_downloads: false" in source
    assert "chrome.downloads.download" not in source
    assert "saveChunkSnapshot" not in source


def test_rollover_archives_only_after_a_complete_jsonl_record() -> None:
    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    append_at = source.index("state.chunk.lines.push(line)")
    bytes_at = source.index('state.chunk.bytes += bytesOf(line + "\\n")')
    threshold_at = source.index("if (state.chunk.bytes >= TARGET_BYTES)")
    archive_at = source.index("await archiveCurrentChunk();", threshold_at)
    assert append_at < bytes_at < threshold_at < archive_at
    assert "no event is split" in source


def test_popup_export_remains_explicit_user_action() -> None:
    popup = (EXTENSION / "popup_logging.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'download.addEventListener("click"' in popup
    assert 'type: "popup.logs.export"' in popup
    assert 'message?.type === "popup.logs.export"' in background
    assert "chunks," in background
