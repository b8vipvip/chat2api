"""f7c8 media handoff must not terminate an unfinished native ChatGPT answer."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_real_worker_media_handoff_terminal_guard():
    result=subprocess.run(
        ["node","tests/media_handoff_page_terminal_f7c8.mjs"],
        cwd=ROOT, capture_output=True, text=True,timeout=20,
    )
    assert result.returncode==0, result.stdout+"\n"+result.stderr
