from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_playground_sse_progress_survives_unmounted_admin_view():
    p=subprocess.run(["node","tests/playground_sse_ui_failure_isolation_v628.mjs"],
                     cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stdout+"\n"+p.stderr
