from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_default_action_reuses_the_only_admin_model_configuration():
    ui = (ROOT/"app"/"admin_text_model_tasks_v147.js").read_text(encoding="utf-8")
    backend = (ROOT/"app"/"worker_text_model_management_v146_patch.py").read_text(encoding="utf-8")
    old_ui = (ROOT/"app"/"admin_text_model_management_v146.js").read_text(encoding="utf-8")
    assert 'button[data-model-default]' in ui
    assert 'button("设为默认","data-model-default",row.id)' in ui
    assert '（默认）' in ui
    assert 'models.legacy_minimum_model' in ui
    assert 'api("/api/admin/text-model-minimum"' in ui
    assert '"/api/admin/text-model-minimum"' in backend
    assert "textModelMinimumSaveV146" not in old_ui

def test_model_confirmation_uses_single_passive_live_document_reader():
    background = (ROOT/"chrome_extension"/"background_model_switch_v149.js").read_text(encoding="utf-8")
    content = (ROOT/"chrome_extension"/"content_model_library_v145.js").read_text(encoding="utf-8")
    assert "inspectFreshSelection(tabId)" in background
    assert "chrome.scripting.executeScript" in background
    assert 'type: "chat2api.model.inspect.v149"' not in background
    assert "state.inspectSelectedModel = () => inspectModelSelectionV149();" in content
    assert "selected_in_menu: selectedInMenu || null" in content
    assert "live-menu-selected-row" in content
    assert "inspectSelectedModel?.()" in background
    assert "inspectPassiveSelection" not in background

def test_reasoning_controller_preserves_live_evidence_until_page_driver_checks():
    source = (ROOT/"chrome_extension"/"content_reasoning_v7.js").read_text(encoding="utf-8")
    assert "const checked = attachDriverVerification(data, requested);" in source
    assert 'key(document, "Escape", "Escape");' in source
    assert "if (await inspectAppliedReasoning(requested))" in source
    assert "if (result) {" in source
    assert "The Page Driver remains the single" in source
