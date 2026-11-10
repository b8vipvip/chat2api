"""Upstream stream EOF is not an image tool terminal; no invented images.

A real three-image request in the 2026-11-10 exported logs went failed
~20 seconds after the first short stream despite having no real assistant
end-turn or image handoff. File tasks already wait after early page AbortError.
"""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_image_generation_quiet_stream_grace_is_bounded_and_same_request():
    if not shutil.which("node"):
        return
    result=subprocess.run(["node","tests/unverified_image_tool_grace_v344.mjs"],
       cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
