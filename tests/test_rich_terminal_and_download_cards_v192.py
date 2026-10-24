"""The TXT terminal owner and file-card capture must preserve source trust."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_short_sse_preamble_is_not_rich_document_terminal():
    owner = read("chrome_extension/content_request_v6.js")
    assert "Number(active.networkTerminalBytes || 0) > 32768" in owner
    assert '"verified-short-sse-rich-dom-v192"' in owner
    assert 'source: "network-sse-short-unreconciled-v192"' in owner
    assert 'type: "chat.error", request_id: active.requestId' in owner
    assert '"verified-rendered-dom-baseline-v191"' in owner
    assert 'return { text: raw, source: "network-sse-short-unreconciled-v192" }' in owner
    assert "request-v6 must cross-check" in owner


def test_generated_file_card_urls_are_verified_before_archival():
    rich = read("chrome_extension/content_rich_response_v69.js")
    probe = read("chrome_extension/content_probe_supervisor_v1.js")
    server = read("app/generated_files_v196.py")
    assert 'button[data-download-url]' in rich
    assert 'getAttribute?.("data-file-url")' in rich
    assert 'getAttribute?.("data-href")' in rich
    assert 'const downloadable = recognizedFile(button);' in rich
    assert 'const link = document.createElement("a");' in rich
    assert "if (!trustedMediaSource(absolute)) return null" in rich
    assert 'await inlineGeneratedFiles(clone)' in rich
    assert 'Generated PDF signature mismatch' in server
    assert 'Generated ZIP/Office signature mismatch' in server
    assert 'download_button_click_only_count' in probe
    assert 'download_button_url_attr_count' in probe
    assert 'file_card_href_count' in probe
    assert 'file_card_button_count' in probe
    assert 'click(' not in probe
