from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_only_outer_dispatch_publishes_model_preflight_terminal():
    model = source("chrome_extension/model_routing_v2.js")
    dispatch = source("chrome_extension/conversation_dispatch.js")
    route = source("chrome_extension/conversation_routing.js")
    assert 'type: "chat.error"' not in model
    assert "failure.routingDiagnostics = diagnostics;" in model
    assert "throw failure;" in model
    assert "error?.routingDiagnostics" in dispatch
    assert dispatch.index("router.failRequest(requestId") < dispatch.index("const sent = await trySendSocket(event)")
    assert 'state.failRequest = failRequest' in route


def test_reasoning_error_keeps_structured_stage_and_code():
    model = source("chrome_extension/model_routing_v2.js")
    assert 'failure.stage = "worker-reasoning-control"' in model
    assert 'response?.code || "reasoning_selection_failed"' in model
    assert "failure.verification = response?.diagnostics?.verification" in model


def test_model_and_reasoning_use_same_ui_picker_and_adapter_evidence():
    picker = source("chrome_extension/content_model_library_v145.js")
    reasoning = source("chrome_extension/content_reasoning_v7.js")
    adapter = source("chrome_extension/content_page_adapter_v22.js")
    assert "state.pickerControl = modelPicker" in picker
    assert "__CHAT2API_MODEL_LIBRARY_V145__?.pickerControl?.()" in reasoning
    assert 'document.querySelectorAll("header")' in adapter
    assert "modelReasoningControls()" in adapter
    # Missing/unknown effort controls must not be silently treated as applied.
    assert 'throw new Error("ChatGPT reasoning control was not found")' in reasoning
    assert "could not be verified after selection" in reasoning


def test_model_preflight_terminal_live_vm():
    import shutil
    import subprocess
    if not shutil.which("node"):
        return
    run = subprocess.run(["node", str(ROOT / "tests" / "single_model_preflight_terminal_v164.mjs")],
                         cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + "\\n" + run.stderr
