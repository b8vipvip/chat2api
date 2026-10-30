from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_v255_compact_roleless_serializer_retry():
    result=subprocess.run(["node","--test","tests/compact_roleless_serializer_v255.mjs"],
                          cwd=ROOT,capture_output=True,text=True,timeout=25,check=False)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
