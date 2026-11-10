"""A download-side cloned Fetch reader must never pin file handoff.

The browser's native download remains untouched. On a clone size cap, reader
cancel may wait for the page's other tee branch indefinitely, so it cannot
block request-bound capture or retries of the exact verified file.
"""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_verified_download_clone_eof_and_failed_clone_cleanup():
    if not shutil.which("node"):
        return
    run=subprocess.run(
        ["node","tests/native_file_clone_cancel_v343.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=25,
    )
    assert run.returncode==0,run.stdout+"\n"+run.stderr


def test_file_probe_cannot_claim_request_terminal_or_await_tee_cancel():
    source=(ROOT/"chrome_extension/native_file_main_v210.js").read_text()
    assert "let size = 0, nativeEof = false;" in source
    assert "const cancellation=reader.cancel?.();" in source
    assert "cancellation?.catch?.(()=>{});" in source
    assert "await reader.cancel()" not in source
    assert "if (!nativeEof)" in source
    assert "reader.releaseLock?.();" in source
    assert "chat.completed" not in source
