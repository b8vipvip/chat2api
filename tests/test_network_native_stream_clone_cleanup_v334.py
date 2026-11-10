"""The native ChatGPT Fetch still owns its AbortSignal and response bytes.

Only the evidence clone may be cancelled after a parser/read exception. The
full upstream SSE must never be marked complete by a clone error.
"""
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_observer_clone_cancellation_is_not_native_fetch_cancellation():
    if not shutil.which("node"):
        return
    run=subprocess.run(
        ["node","tests/network_native_stream_clone_cleanup_v334.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=30,
    )
    assert run.returncode==0,run.stdout+"\n"+run.stderr


def test_native_network_observer_preserves_original_signal_and_sanitizes_errors():
    source=(ROOT/"chrome_extension/network_stream_main_v55.js").read_text()
    assert "nativeFetch(input, init)" in source
    assert "const cleanup = reader.cancel?.();" in source
    assert "cleanup?.catch?.(() => {});" in source
    assert "if (!nativeEof)" in source
    assert '"network-observer-" + stage + "-" + safeName' in source
