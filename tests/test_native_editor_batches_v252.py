from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_large_native_editor_batched_prompt_fail_closed():
    completed=subprocess.run(["node","--test","tests/native_editor_batches_v252.mjs"],
                             cwd=ROOT,capture_output=True,text=True,timeout=25,check=False)
    assert completed.returncode==0, completed.stdout+"\n"+completed.stderr
