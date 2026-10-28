import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_worker_generated_file_chunks_node_vm():
    if not shutil.which("node"):
        return
    p=subprocess.run(["node","tests/generated_file_chunks_v238.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=40)
    assert p.returncode==0,p.stdout+"\n"+p.stderr
