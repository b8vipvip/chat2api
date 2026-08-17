import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
ADAPTER_NAME = "content_page_adapter_v22.js"
ADAPTER_KEY = "__CHAT2API_PAGE_ADAPTER_V22__"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_adapter_loads_before_historical_content_overlays():
    manifest = json.loads(read(EXT / "manifest.json"))
    content_scripts = next(item for item in manifest["content_scripts"] if ADAPTER_NAME in item.get("js", []))
    scripts = content_scripts["js"]
    assert scripts.index("content.js") < scripts.index(ADAPTER_NAME)
    assert scripts.index(ADAPTER_NAME) < scripts.index("content_model_v7.js")
    assert scripts.index(ADAPTER_NAME) < scripts.index("content_reasoning_v7.js")
    assert scripts.index(ADAPTER_NAME) < scripts.index("content_model_transition_v15.js")
    assert scripts.index(ADAPTER_NAME) < scripts.index("content_request_perf_v21.js")
    assert scripts.index(ADAPTER_NAME) < scripts.index("content_completion_fast_v21.js")


def test_page_adapter_is_passive_when_loaded():
    source = read(EXT / ADAPTER_NAME)
    assert 'const VERSION = "22.1.0"' in source
    assert "new MutationObserver" not in source
    assert "setInterval(" not in source
    assert "setTimeout(" not in source
    assert "chrome.runtime.onMessage" not in source
    assert ".click()" not in source


def test_page_adapter_separates_text_and_label_normalization():
    source = read(EXT / ADAPTER_NAME)
    assert "function normalize(value)" in source
    assert "function normalizeLabel(value)" in source
    normalize_body = source.split("function normalize(value)", 1)[1].split("function normalizeLabel(value)", 1)[0]
    label_body = source.split("function normalizeLabel(value)", 1)[1].split("function normalizedLower(value)", 1)[0]
    assert "✓" not in normalize_body
    assert "✓" in label_body


def test_page_adapter_exposes_shared_read_contract():
    source = read(EXT / ADAPTER_NAME)
    for name in (
        "visible",
        "labelOf",
        "composerRoot",
        "composer",
        "composerText",
        "sendButton",
        "buttonReady",
        "stopButton",
        "isGenerating",
        "isSendTarget",
        "dispatchEnter",
        "assistantNodes",
        "assistantIdentity",
        "assistantText",
        "familyFromText",
        "reasoningFromText",
        "modelReasoningControls",
        "modelReasoningControl",
        "modelControl",
        "reasoningControl",
        "reasoningEvidence",
        "modelFamilyEvidence",
        "openSurfaces",
        "reasoningSlider",
    ):
        assert f"    {name}," in source


def test_phase1_consumers_prefer_page_adapter_with_legacy_fallbacks():
    request_perf = read(EXT / "content_request_perf_v21.js")
    completion_fast = read(EXT / "content_completion_fast_v21.js")
    model_transition = read(EXT / "content_model_transition_v15.js")

    for source in (request_perf, completion_fast, model_transition):
        assert ADAPTER_KEY in source
        assert "globalThis.__CHAT2API_PAGE_ADAPTER_V22__ || null" in source

    for name in ("composer", "composerText", "sendButton", "buttonReady", "stopButton", "isSendTarget", "dispatchEnter"):
        assert f"adapter?.{name}" in request_perf

    for name in ("stopButton", "assistantNodes", "assistantIdentity", "assistantText"):
        assert f"adapter?.{name}" in completion_fast

    for name in ("visible", "labelOf", "familyFromText", "reasoningFromText", "composerRoot", "modelReasoningControl"):
        assert f"adapter?.{name}" in model_transition

    # Phase 1 intentionally keeps local selectors as rollback/fallback behavior.
    assert "button[data-testid='send-button']" in request_perf
    assert "[data-message-author-role='assistant']" in completion_fast
    assert "form[data-type='unified-composer']" in model_transition


def test_phase2_model_state_prefers_adapter_evidence_and_keeps_cache_ownership():
    source = read(EXT / "content_model_v7.js")
    assert ADAPTER_KEY in source
    for name in ("visible", "labelOf", "familyFromText", "reasoningFromText", "composerRoot", "reasoningEvidence", "modelFamilyEvidence"):
        assert f"adapter?.{name}" in source
    assert "new MutationObserver" in source
    assert 'source: "manual-family-choice"' in source
    assert 'source: "manual-reasoning-choice"' in source
    assert "passive-dom+trusted-session-cache" in source
    # Legacy detection remains available if the adapter is missing.
    assert "button[class*='composer-pill']" in source
    assert "combined composer pill" in source


def test_phase2_reasoning_uses_adapter_only_for_reads_not_write_strategy():
    source = read(EXT / "content_reasoning_v7.js")
    assert ADAPTER_KEY in source
    for name in ("visible", "labelOf", "composerRoot", "reasoningControl", "openSurfaces", "reasoningSlider"):
        assert f"adapter?.{name}" in source

    # The existing reasoning write algorithm remains feature-owned in phase 2.
    assert 'key(target, "M", "KeyM", { ctrlKey: true, shiftKey: true })' in source
    assert "setNativeRange" in source
    assert 'key(slider, "Home", "Home")' in source
    assert 'key(slider, "End", "End")' in source
    assert 'key(slider, "ArrowRight", "ArrowRight")' in source
    assert "pill.click()" in source
    assert "choice.click()" in source
    assert "reasoning-v7.2" in source


def test_adapter_model_evidence_preserves_ambiguity_and_public_reasoning_levels():
    source = read(EXT / ADAPTER_NAME)
    assert 'source: unique.length > 1 ? "ambiguous-dom" : "none"' in source
    assert '["instant", "medium", "high"].includes(item.reasoning)' in source
    assert 'auto: ["智能", "自动", "auto", "automatic"]' in source


def test_ci_checks_page_adapter_javascript_syntax():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    assert f"node --check chrome_extension/{ADAPTER_NAME}" in workflow


def test_page_adapter_contract_is_documented():
    doc = read(ROOT / "docs" / "PAGE_ADAPTER.md")
    assert ADAPTER_KEY in doc
    assert "22.1.0" in doc
    assert "content_request_perf_v21.js" in doc
    assert "content_completion_fast_v21.js" in doc
    assert "content_model_transition_v15.js" in doc
    assert "content_model_v7.js" in doc
    assert "content_reasoning_v7.js" in doc
    assert "modelFamilyEvidence" in doc
    assert "reasoningEvidence" in doc
