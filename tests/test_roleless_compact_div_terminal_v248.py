from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_compact_div_v248_request_scoped_completion():
    result = subprocess.run(
        ["node", "tests/roleless_compact_div_terminal_v248.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
