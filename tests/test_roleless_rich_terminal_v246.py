from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v246_roleless_rich_terminal_complete_reply_scope():
    result = subprocess.run(
        ["node", "tests/roleless_rich_terminal_v246.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
