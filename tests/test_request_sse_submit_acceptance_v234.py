"""v234 stream accepted but no assistant DOM: avoid false send failure."""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_same_request_stream_response_proves_submit_only():
    if not shutil.which("node"):
        return
    run=subprocess.run(["node","tests/request_sse_submit_acceptance_v234.mjs"],
        cwd=ROOT, text=True, capture_output=True,timeout=20)
    assert run.returncode==0,run.stdout+"\n"+run.stderr
