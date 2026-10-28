from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def test_router_failure_grace_and_sse_abort_v237():
    if not shutil.which("node"):
        return
    p=subprocess.run(["node","tests/router_failure_grace_and_sse_abort_v237.mjs"],
      cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stdout+"\n"+p.stderr
