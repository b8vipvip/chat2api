import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_manifest_loads_canonical_model_and_multimodal_controllers() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.7.2"
    assert "downloads" in manifest["permissions"]
    scripts = [name for block in manifest["content_scripts"] for name in block["js"]]
    assert "voice_main.js" in scripts
    assert "voice_main_v2.js" not in scripts
    assert "content_request_v2.js" in scripts
    assert "content_request_v3.js" in scripts
    assert "content_request_v4.js" in scripts
    assert "content_request_v5.js" in scripts
    assert "content_model_v5.js" in scripts
    assert "content_model_v7.js" in scripts
    assert "content_model_transition_v15.js" in scripts
    assert "content_reasoning_v7.js" in scripts
    assert "content_model_v6.js" not in scripts
    assert "content_multimodal.js" in scripts
    assert "content_multimodal_v4.js" in scripts
    assert "content_guard.js" in scripts
    assert "content_image.js" in scripts
    assert "content_image_v3.js" in scripts
    assert "content_voice.js" in scripts
    assert "content_voice_v2.js" in scripts
    assert "content_voice_fix_v3.js" in scripts
    assert "content_voice_fix_v4.js" in scripts
    assert "content_runtime_log.js" in scripts
    assert not any("dictation" in name for name in scripts)


def test_request_router_preflights_then_uses_passive_state_before_fallback_selection() -> None:
    source = (EXTENSION / "model_routing_v2.js").read_text(encoding="utf-8")
    assert "chat2api.request.preflight" in source
    assert "content_request_v5.js" in source
    assert "chat2api.model.probe.v7" in source
    assert "chat2api.model.commit.v7" in source
    assert "chat2api.model.prepare.v5" in source
    assert "chat2api.reasoning.prepare.v7" in source
    assert 'TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5"]' in source
    assert "passive-state-match-zero-op" in source
    assert "passive-no-ui-v7" in source
    assert 'model: "chatgpt-web"' in source  # internal bypass only, never public catalog
    assert 'type: "chat.diagnostics"' in source
    assert "chat2api.attach.prepare.v4" in source
    handler = source[source.index("handleServerMessage = async function handleCanonicalModelRouting"):]
    preflight_at = handler.index("preflightRequest(tab.id, message)")
    model_at = handler.index("prepareRequestedState(tab, requestedModel, requestedReasoning)")
    attachments_at = handler.index("prepareAttachments(tab.id, message.attachments || [])")
    assert preflight_at < model_at < attachments_at


def test_family_verification_false_negative_recovers_from_passive_composer_state() -> None:
    router = (EXTENSION / "model_routing_v2.js").read_text(encoding="utf-8")
    state = (EXTENSION / "content_model_v7.js").read_text(encoding="utf-8")
    transition = (EXTENSION / "content_model_transition_v15.js").read_text(encoding="utf-8")
    assert "waitForPassiveFamily" in router
    assert "family_verification_recovered" in router
    assert "family_original_error" in router
    assert "passive-recovery" in router
    assert "combined composer pill" in state
    assert 'text.endsWith(` ${needle}`)' in state
    assert "family-transition-inference-v15" in transition


def test_request_controller_waits_for_send_and_v3_recovers_stale_drafts() -> None:
    v2 = (EXTENSION / "content_request_v2.js").read_text(encoding="utf-8")
    v3 = (EXTENSION / "content_request_v3.js").read_text(encoding="utf-8")
    v4 = (EXTENSION / "content_request_v4.js").read_text(encoding="utf-8")
    v5 = (EXTENSION / "content_request_v5.js").read_text(encoding="utf-8")
    assert "waitForSendReady" in v2
    assert "buttonReady" in v2
    assert "ready-timeout" in v2
    assert "submission_confirmed" in v2
    assert "send_attempts" in v2
    assert "ChatGPT send action was not confirmed" in v2
    assert v2.index("await submitAndConfirm(active, composer)") < v2.index("await monitor(active)")
    assert "chat2api.request.preflight" in v3
    assert "cleanupPreviousAutomationDraft" in v3
    assert "removeAttachmentsByName" in v3
    assert "manual or unknown draft" in v3
    assert "enter-fallback" in v3
    assert "dispatchEnter" in v3
    assert "submittedEvidence" in v4
    assert "confirmed-before-click" in v4
    assert "historical_hydration_ignored: true" in v5
    assert 'request_controller: "request-v5"' in v5


def test_image_router_reuses_bound_tab_confirms_v2_and_does_not_focus_window() -> None:
    routing = (EXTENSION / "image_routing_v3.js").read_text(encoding="utf-8")
    controller = (EXTENSION / "content_image_v3.js").read_text(encoding="utf-8")
    assert 'message.type !== "image.request"' in routing
    assert "same-chat" in routing or "same_tab" in routing or "same-tab" in routing
    assert "chrome.windows.update" not in routing
    assert "content_image_v3.js" in routing
    assert "__CHAT2API_IMAGE_CONTROLLER_V3__" in controller
    assert "submitAndConfirm" in controller
    assert "baseline" in controller.lower()


def test_browser_fallback_creates_one_inactive_window_tab() -> None:
    source = (EXTENSION / "browser_tabs.js").read_text(encoding="utf-8")
    assert "chrome.windows.create" in source
    assert 'focused: false' in source
    assert 'automationWindowStrategy: "single-tab-window"' in source
    assert "chrome.tabs.create" not in source


def test_v7_state_detector_is_passive_and_tracks_only_canonical_models() -> None:
    source = (EXTENSION / "content_model_v7.js").read_text(encoding="utf-8")
    assert 'const FAMILIES = ["gpt-5.6-sol", "gpt-5.5"]' in source
    assert "sessionStorage" in source
    assert "passiveFamily" in source
    assert "passiveReasoning" in source
    assert "family_trusted" in source
    assert "reasoning_trusted" in source
    assert "zero_op" in source
    assert "chat2api.models.discover.v7" in source
    assert ".click()" not in source


def test_v7_reasoning_prefers_shortcut_nested_keyboard_and_range_before_click_fallback() -> None:
    source = (EXTENSION / "content_reasoning_v7.js").read_text(encoding="utf-8")
    assert 'openByShortcut' in source
    assert 'ctrlKey: true, shiftKey: true' in source
    assert "setNativeRange" in source
    assert "pill-enter-no-click" in source
    assert "choice-enter" in source
    assert "reasoningRow" in source
    assert "reasoning-row-enter" in source
    assert "chooseNoClick" in source
    assert "chooseClickFallback" in source
    assert "MAX_SLIDER_STEPS = 32" in source
    assert "slider-keyboard-adaptive" in source
    assert source.index("chooseNoClick(requested)") < source.index("chooseClickFallback(requested)")


def test_v5_picker_remains_scoped_fallback_for_model_family_changes() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert "form[data-type='unified-composer']" in source
    assert "composerPill" in source
    assert "waitForComposerReady" in source
    assert "READY_TIMEOUT_MS = 30000" in source
    assert "chooseFamily" in source


def test_api_default_text_model_is_canonical_not_browser_alias() -> None:
    source = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert 'model: str = "gpt-5.6-sol"' in source
    assert 'reasoning_effort: str | None' in source
    router = (EXTENSION / "model_routing_v2.js").read_text(encoding="utf-8")
    assert "default-no-ui" not in router
    assert "Unsupported text model" in router
