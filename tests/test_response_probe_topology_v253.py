from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_rich_topology_probe_is_read_only():
    proc=subprocess.run(["node","--test","tests/response_probe_topology_v253.mjs"],
                        cwd=ROOT,capture_output=True,text=True,timeout=20,check=False)
    assert proc.returncode==0,proc.stdout+"\n"+proc.stderr
