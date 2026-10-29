from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_native_long_editor_txn_does_not_duplicate_synthetic_input():
    result=subprocess.run(["node","tests/native_editor_long_input_once_v251.mjs"],
                          cwd=ROOT,capture_output=True,text=True,timeout=20,check=False)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
