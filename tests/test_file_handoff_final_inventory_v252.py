from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_final_file_handoff_type_inventory_v252():
    r=subprocess.run(["node","--test","tests/file_handoff_final_inventory_v252.mjs"],
                     cwd=ROOT,capture_output=True,text=True,timeout=20,check=False)
    assert r.returncode==0,r.stdout+"\n"+r.stderr
