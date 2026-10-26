from pathlib import Path

from app.worker_probe_store import _sanitize_facts

ROOT = Path(__file__).resolve().parents[1]


def test_file_preview_probe_fields_survive_sanitization():
    facts = {
        "download_buttons": 4,
        "download_button_url_attr_count": 0,
        "download_button_click_only_count": 4,
        "file_card_candidates_count": 0,
        "file_preview_control_count": 3,
        "file_preview_xlsx_count": 1,
        "file_preview_pptx_count": 1,
        "file_preview_json_count": 1,
        "file_preview_zip_count": 0,
        "file_preview_conversation_link_count": 3,
        "file_preview_click_only_count": 0,
        "download_link_https_count": 3,
        "secret_signed_url": "must-never-export",
        "filename": "private.xlsx",
    }
    safe = _sanitize_facts(facts)
    assert safe["download_button_click_only_count"] == 4
    assert safe["file_preview_control_count"] == 3
    assert safe["file_preview_zip_count"] == 0
    assert safe["file_preview_conversation_link_count"] == 3
    assert "secret_signed_url" not in safe
    assert "filename" not in safe


def test_worker_probe_has_structural_preview_metrics_without_new_terminal_authority():
    source = (ROOT / "chrome_extension/content_probe_supervisor_v1.js").read_text()
    assert "file_preview_xlsx_count: previewExtCount" in source
    assert "download_button_click_only_count" in source
    assert "chat.completed" not in source
    assert "image.completed" not in source
