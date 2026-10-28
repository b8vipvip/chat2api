"""Worker-to-server generated chunk confirmation is not socket.send-only."""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_worker_chunk_persisted_ack():
    if not shutil.which("node"): return
    p=subprocess.run(["node","tests/worker_file_chunk_ack_v238.mjs"],
      cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stdout+"\n"+p.stderr
