from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_reasoning_one_write_live_inspection_regression():
    run = subprocess.run(
        ["node", str(ROOT / "tests" / "reasoning_live_reinspect_v169.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert run.returncode == 0, run.stdout + run.stderr

def test_reasoning_controller_does_not_run_two_selectors_after_first_candidate():
    source = (ROOT / "chrome_extension" / "content_reasoning_v7.js").read_text()
    assert "const result = await chooseClickFallback(requested);" in source
    assert "inspectAppliedReasoning(requested)" in source
    assert "result = await chooseClickFallback(requested);" in source
    assert "The Page Driver remains the single" in source
