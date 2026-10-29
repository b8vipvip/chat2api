from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_roleless_plain_fallback_is_full_and_bound():
    p=subprocess.run(["node","--test","tests/roleless_plain_fallback_v252.mjs"],
                     cwd=ROOT,capture_output=True,text=True,timeout=20,check=False)
    assert p.returncode==0,p.stdout+"\n"+p.stderr
