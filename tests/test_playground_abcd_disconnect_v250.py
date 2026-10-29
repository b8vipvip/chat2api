from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v250_abcd_disconnected_stream_waits_for_real_terminal():
    result=subprocess.run(["node","tests/playground_abcd_disconnect_recovery_v250.mjs"],
                          cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stdout+"\n"+result.stderr
