"""A 23MiB working reply must not lose all native layout evidence when the Worker disconnects.

The native safe-tree observer already exposes capture(active, final=False), but
request-v6 formerly only called capture(active, True) on chat.completed.
The change is a read-only, request-bound sidecar progress source; it does not
promote a partial DOM to an authoritative final reply.
"""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_verified_request_owned_progress_capture_cadence_v346():
    if not shutil.which("node"):
        return
    run=subprocess.run(["node","tests/native_progress_scope_cadence_v346.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stdout+"\n"+run.stderr
