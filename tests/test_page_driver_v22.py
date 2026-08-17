import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
DRIVER = "content_page_driver_v22.js"
DRIVER_KEY = "__CHAT2API_PAGE_DRIVER_V22__"
VM_CONTRACT = "tests/page_driver_dispatch_key_v22.mjs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_driver_loads_after_adapter_before_reasoning_controller():
    manifest = json.loads(read(EXT / "manifest.json"))
    scripts = next(item["js"] for item in manifest["content_scripts"] if DRIVER in item.get("js", []))
    assert scripts.index("content_page_adapter_v22.js") < scripts.index(DRIVER)
    assert scripts.index(DRIVER) < scripts.index("content_reasoning_v7.js")


def test_page_driver_bootstrap_recovers_existing_tabs_in_same_order():
    bootstrap = read(EXT / "content_bootstrap.js")
    adapter_pos = bootstrap.index('"content_page_adapter_v22.js"')
    driver_pos = bootstrap.index(f'"{DRIVER}"')
    model_pos = bootstrap.index('"content_model_v7.js"')
    reasoning_pos = bootstrap.index('"content_reasoning_v7.js"')
    assert adapter_pos < driver_pos < model_pos < reasoning_pos
    assert "chrome.scripting.executeScript" in bootstrap


def test_page_driver_v22_phase7_adds_only_explicit_keyboard_write_primitive():
    source = read(EXT / DRIVER)
    assert 'const VERSION = "22.3.0"' in source
    assert DRIVER_KEY in source
    assert "function dispatchKey(target, name, code = name, extra = {})" in source
    assert 'new KeyboardEvent("keydown", init)' in source
    assert 'new KeyboardEvent("keyup", init)' in source
    assert "return true;" in source
    assert "    dispatchKey," in source

    # The Driver still has no autonomous orchestration or higher-level writes.
    assert "new MutationObserver" not in source
    assert "setInterval(" not in source
    assert "setTimeout(" not in source
    assert ".click()" not in source
    assert "HTMLInputElement.prototype" not in source
    assert 'message?.type !== "chat2api.page.verify.v22"' in source


def test_page_driver_merges_dom_evidence_with_trusted_model_cache():
    source = read(EXT / DRIVER)
    assert 'const CACHE_KEY = "chat2api:model-state:v2"' in source
    assert "modelFamilyEvidence" in source
    assert "reasoningEvidence" in source
    assert "dirty_family" in source
    assert "dirty_reasoning" in source
    assert '"session-cache"' in source
    assert "family_trusted" in source
    assert "reasoning_trusted" in source


def test_page_driver_exposes_structured_verification_codes():
    source = read(EXT / DRIVER)
    for code in (
        "model_state_untrusted",
        "model_mismatch",
        "reasoning_state_untrusted",
        "reasoning_mismatch",
        "reasoning_selection_failed",
        "reasoning_control_not_found",
        "reasoning_menu_open_failed",
        "reasoning_level_unavailable",
        "reasoning_local_verification_failed",
    ):
        assert code in source
    for method in (
        "dispatchKey",
        "currentState",
        "verifyState",
        "verifyReasoning",
        "classifyReasoningError",
        "attachVerification",
    ):
        assert f"    {method}," in source
    assert 'controller: "page-driver-v22.3"' in source


def test_reasoning_controller_prefers_driver_key_dispatch_but_keeps_exact_local_fallback():
    source = read(EXT / "content_reasoning_v7.js")
    assert DRIVER_KEY in source
    assert "pageDriver?.dispatchKey" in source
    assert "pageDriver.dispatchKey(target, name, code, extra)" in source
    assert 'target.dispatchEvent(new KeyboardEvent("keydown", init));' in source
    assert 'target.dispatchEvent(new KeyboardEvent("keyup", init));' in source
    assert source.index("pageDriver.dispatchKey(target, name, code, extra)") < source.index('target.dispatchEvent(new KeyboardEvent("keydown", init));')

    # High-level reasoning behavior stays feature-owned and unchanged.
    for token in (
        'key(target, "M", "KeyM", { ctrlKey: true, shiftKey: true })',
        'key(pill, "Enter", "Enter")',
        'key(pill, " ", "Space")',
        'key(slider, "Home", "Home")',
        'key(slider, "End", "End")',
        'key(slider, "ArrowRight", "ArrowRight")',
        "setNativeRange",
        "chooseClickFallback",
        "pill.click()",
        "choice.click()",
    ):
        assert token in source


def test_reasoning_controller_attaches_driver_diagnostics_without_replacing_write_strategy():
    source = read(EXT / "content_reasoning_v7.js")
    assert DRIVER_KEY in source
    assert "attachDriverVerification" in source
    assert "classifyDriverError" in source
    # The established controller identity remains a compatibility contract.
    assert 'controller: "reasoning-v7.2"' in source
    assert "page_driver_version" in source
    assert "verification: classified.verification" in source


def test_model_router_propagates_structured_reasoning_diagnostics_without_weakening_probe_gate():
    source = read(EXT / "model_routing_v2.js")
    for token in (
        "reasoningRoutingError",
        "reasoning_selection_failed",
        "reasoning_error_code",
        "reasoning_controller_diagnostics",
        "reasoning_verification",
        "reasoning_page_driver_version",
        "reasoning_verification_warning",
        "lastModelDiagnostics: errorDiagnostics",
        'type: "chat.diagnostics"',
        'type: "chat.error"',
        "code: errorCode",
        "diagnostics: errorDiagnostics",
    ):
        assert token in source

    # The final passive probe remains the authoritative request gate.
    assert "const afterResponse = await probeState(tab.id, model, reasoning);" in source
    assert "if (!after.family_match)" in source
    assert "if (reasoning && !after.reasoning_match)" in source
    assert 'code: "model_verification_failed"' in source
    assert 'code: "reasoning_verification_failed"' in source


def test_page_driver_vm_contract_executes_and_is_required_by_ci():
    contract = read(ROOT / VM_CONTRACT)
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")

    for token in (
        'from "node:vm"',
        "vm.runInContext(source, sandbox",
        'constructedEvents.length, 0',
        'runtimeListeners.length, 1',
        'driver.dispatchKey(null, "Escape")',
        'driver.dispatchKey({}, "Escape")',
        'driver.dispatchKey(target, "M", "KeyM"',
        'ctrlKey: true',
        'shiftKey: true',
        '["keydown", "keyup"]',
        'driver.dispatchKey(defaultCodeTarget, "Escape")',
    ):
        assert token in contract

    assert "- name: Page Driver VM contract" in workflow
    assert f"run: node {VM_CONTRACT}" in workflow


def test_ci_checks_page_driver_javascript_syntax():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    assert f"node --check chrome_extension/{DRIVER}" in workflow
