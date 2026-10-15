from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_selected_reasoning_nested_marker_and_missing_live_evidence():
    result=subprocess.run(["node",str(ROOT/"tests"/"reasoning_nested_selection_v172.mjs")],
      cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

def test_verification_keeps_single_driver_authority():
    adapter=(ROOT/"chrome_extension"/"content_page_adapter_v22.js").read_text()
    reasoning=(ROOT/"chrome_extension"/"content_reasoning_v7.js").read_text()
    assert "function selectedReasoningRows(" in adapter
    assert "reasoningInspection" in adapter
    assert "const checked = attachDriverVerification(data, requested);" in reasoning
    assert "if (verified?.verification && verified.verification.ok !== true)" in reasoning
