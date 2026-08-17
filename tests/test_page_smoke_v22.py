import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
CONTENT_SMOKE = "content_page_smoke_v22.js"
BACKGROUND_SMOKE = "background_page_smoke_v22.js"
POPUP_SMOKE = "popup_page_smoke_v22.js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_smoke_loads_after_adapter_driver_and_state_controllers():
    manifest = json.loads(read(EXT / "manifest.json"))
    scripts = next(item["js"] for item in manifest["content_scripts"] if CONTENT_SMOKE in item.get("js", []))
    assert scripts.index("content_page_adapter_v22.js") < scripts.index("content_page_driver_v22.js")
    assert scripts.index("content_page_driver_v22.js") < scripts.index("content_model_v7.js")
    assert scripts.index("content_model_v7.js") < scripts.index("content_reasoning_v7.js")
    assert scripts.index("content_reasoning_v7.js") < scripts.index(CONTENT_SMOKE)


def test_existing_tab_bootstrap_restores_smoke_after_controllers():
    source = read(EXT / "content_bootstrap.js")
    adapter_at = source.index('"content_page_adapter_v22.js"')
    driver_at = source.index('"content_page_driver_v22.js"')
    model_at = source.index('"content_model_v7.js"')
    reasoning_at = source.index('"content_reasoning_v7.js"')
    smoke_at = source.index(f'"{CONTENT_SMOKE}"')
    assert adapter_at < driver_at < model_at < reasoning_at < smoke_at


def test_content_page_smoke_is_strictly_read_only_and_reports_contract_layers():
    source = read(EXT / CONTENT_SMOKE)
    assert 'const VERSION = "22.0.0"' in source
    assert "__CHAT2API_PAGE_ADAPTER_V22__" in source
    assert "__CHAT2API_PAGE_DRIVER_V22__" in source
    assert "__CHAT2API_MODEL_STATE_V7__" in source
    assert "__CHAT2API_REASONING_CONTROL_V7__" in source
    assert 'message?.type !== "chat2api.page.smoke.v22"' in source
    for field in (
        "adapter_loaded",
        "driver_loaded",
        "model_controller_loaded",
        "reasoning_controller_loaded",
        "composer_found",
        "composer_visible",
        "current_state",
        "verification",
        "family_evidence",
        "reasoning_evidence",
    ):
        assert field in source
    for forbidden in (
        ".click()",
        "KeyboardEvent",
        "dispatchEvent(",
        "new MutationObserver",
        "setInterval(",
        "setTimeout(",
    ):
        assert forbidden not in source


def test_background_smoke_runs_independent_final_passive_probe_without_writes():
    source = read(EXT / BACKGROUND_SMOKE)
    assert "runPageSmokeV22" in source
    assert "await ensureContent(tab.id)" in source
    assert 'type: "chat2api.page.smoke.v22"' in source
    assert 'type: "chat2api.model.probe.v7"' in source
    assert "family_match" in source
    assert "family_trusted" in source
    assert "reasoning_match" in source
    assert "reasoning_trusted" in source
    assert '"final_probe_mismatch"' in source
    assert "lastPageSmoke" in source
    assert 'message?.type !== "popup.pageSmoke"' in source
    # Free Mini / unknown-model routes must not inherit a stale paid-account
    # reasoning expectation from storage.
    assert 'const expectedReasoning = expectedModel ? canonicalReasoning(settings.currentReasoning) : "";' in source
    for forbidden in (
        "chat2api.model.prepare.v5",
        "chat2api.reasoning.prepare.v7",
        "prepareFamily",
        "prepareReasoning",
        ".click()",
        "KeyboardEvent",
    ):
        assert forbidden not in source


def test_background_smoke_loads_after_bootstrap_and_model_routing():
    source = read(EXT / "background_entry.js")
    bootstrap_at = source.index('"content_bootstrap.js"')
    routing_at = source.index('"model_routing_v2.js"')
    smoke_at = source.index(f'"{BACKGROUND_SMOKE}"')
    assert bootstrap_at < routing_at < smoke_at


def test_popup_exposes_read_only_smoke_diagnostics():
    html = read(EXT / "popup.html")
    script = read(EXT / POPUP_SMOKE)
    assert 'id="pageSmoke"' in html
    assert "运行页面 Smoke Test（只读）" in html
    assert f'<script src="{POPUP_SMOKE}"></script>' in html
    assert 'type: "popup.pageSmoke"' in script
    assert "Adapter" in script
    assert "Page Driver" in script
    assert "Model Controller" in script
    assert "Reasoning Controller" in script
    assert "最终 Probe" in script


def test_ci_checks_page_smoke_javascript_syntax():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    assert f"node --check chrome_extension/{CONTENT_SMOKE}" in workflow
    assert f"node --check chrome_extension/{BACKGROUND_SMOKE}" in workflow
    assert f"node --check chrome_extension/{POPUP_SMOKE}" in workflow


def test_page_smoke_documentation_describes_real_tab_read_only_boundary():
    doc = read(ROOT / "docs" / "PAGE_SMOKE.md")
    assert "真实 ChatGPT 标签页" in doc
    assert "chat2api.page.smoke.v22" in doc
    assert "chat2api.model.probe.v7" in doc
    assert "只读" in doc
    assert "不会切换模型" in doc
