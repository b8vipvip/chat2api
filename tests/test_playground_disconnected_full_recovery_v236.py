"""The Playground UI must not turn an HTTP disconnect into a Worker terminal."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_playground_frontend_recovers_full_broker_terminal_without_replay():
    if not shutil.which("node"):
        return
    result = subprocess.run(
        ["node", "tests/playground_disconnected_full_recovery_v236.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
