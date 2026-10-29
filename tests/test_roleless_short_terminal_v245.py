from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_roleless_short_terminal_nested_dom_v245():
    result = subprocess.run(
        ["node", "tests/roleless_short_terminal_v245.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
