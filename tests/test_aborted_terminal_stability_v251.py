from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_short_only_aborted_sse_stability_and_rich_capture_diagnostics():
    result = subprocess.run(
        ["node", "tests/aborted_terminal_stability_v251.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
