import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_manifest_loads_hybrid_and_multimodal_controllers() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.5"
    scripts = [name for block in manifest["content_scripts"] for name in block["js"]]
    assert "voice_main.js" in scripts
    assert "voice_main_v2.js" not in scripts
    assert "content_request_v2.js" in scripts
    assert "content_request_v3.js" in scripts
    assert "content_model_v5.js" in scripts
    assert "content_model_v6.js" in scripts
    assert "content_multimodal.js" in scripts
    assert "content_guard.js" in scripts
    assert "content_image.js" in scripts
    assert "content_voice.js" in scripts
    assert "content_voice_v2.js" in scripts
    assert not any("dictation" in name for name in scripts)


def test_request_router_preflights_before_model_selection_and_attachments() -> None:
    source = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "chat2api.request.preflight" in source
    assert "content_request_v3.js" in source
    assert "chat2api.model.probe.v6" in source
    assert "chat2api.model.commit.v6" in source
    assert "chat2api.model.prepare.v5" in source
    assert 'DEFAULT_MODEL_IDS = new Set(["default", "chatgpt-web", ""])' in source
    assert "state-match-zero-op" in source
    assert 'type:"chat.diagnostics"' in source
    assert "chat2api.attach.prepare" in source
    handler = source[source.index("handleServerMessage = async function handleRequestDrivenModelRouting"):]
    preflight_at = handler.index("preflightRequest(tab.id,message)")
    model_at = handler.index("prepareRequestedModel(tab,requestedModel)")
    attachments_at = handler.index("prepareAttachments(tab.id,message.attachments||[])")
    assert preflight_at < model_at < attachments_at


def test_request_controller_waits_for_send_and_v3_recovers_stale_drafts() -> None:
    v2 = (EXTENSION / "content_request_v2.js").read_text(encoding="utf-8")
    v3 = (EXTENSION / "content_request_v3.js").read_text(encoding="utf-8")
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


def test_image_router_reuses_bound_tab_confirms_v2_and_does_not_focus_window() -> None:
    routing = (EXTENSION / "image_routing.js").read_text(encoding="utf-8")
    controller = (EXTENSION / "content_image.js").read_text(encoding="utf-8")
    assert 'IMAGES_URL = "https://chatgpt.com/images/"' in routing
    assert 'message.type !== "image.request"' in routing
    assert "chat2api.image.request.v2" in routing
    assert "chat2api.image.ping.v2" in routing
    assert "chat2api.attach.prepare" in routing
    assert "reuse-bound-tab" in routing
    assert "restoreImageSession" in routing
    assert "imageRestorePromise" in routing
    assert "chrome.tabs.create" not in routing
    assert "chrome.windows.update" not in routing
    assert 'image_window_focus_strategy: "tab-active-only"' in routing
    assert "__CHAT2API_IMAGE_CONTROLLER_V2__" in controller
    assert "submitAndConfirm" in controller
    assert "submission_confirmed: true" in controller
    assert "baselineNodes" in controller
    assert "newly-created generated image node" in controller
    assert "prompt submission was not confirmed" in controller


def test_browser_fallback_creates_one_inactive_window_tab() -> None:
    source = (EXTENSION / "browser_tabs.js").read_text(encoding="utf-8")
    assert "chrome.windows.create" in source
    assert 'focused: false' in source
    assert 'automationWindowStrategy: "single-tab-window"' in source
    assert "chrome.tabs.create" not in source


def test_v6_state_cache_invalidates_on_manual_composer_control() -> None:
    source = (EXTENSION / "content_model_v6.js").read_text(encoding="utf-8")
    assert "sessionStorage" in source
    assert "manual-composer-control" in source
    assert "cache_trusted" in source
    assert "zero_op" in source
    assert "state_detect_ms" in source


def test_v5_picker_is_composer_scoped_and_waits_for_readiness() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert "form[data-type='unified-composer']" in source
    assert "composerPill" in source
    assert "waitForComposerReady" in source
    assert "READY_TIMEOUT_MS = 30000" in source
    assert "rejectedButton" in source


def test_v5_reasoning_uses_shortcut_slider_and_text_fallbacks() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert 'code: "KeyM"' in source
    assert "ctrlKey: true" in source
    assert "shiftKey: true" in source
    assert "REASONING_POSITIONS" in source
    assert "visibleSlider" in source
    assert "setSliderPosition" in source
    assert "思考强度" in source


def test_api_request_default_model_is_zero_touch() -> None:
    source = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert 'model: str = "default"' in source
    router = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "default-no-ui" in router
