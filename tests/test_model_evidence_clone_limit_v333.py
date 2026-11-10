"""Bounded MAIN-world SSE model observer must cancel ONLY its tee branch.

Long 2026-11-10 A responses can exceed 16 MB; the previous observer stopped
reading the clone after MAX_BUFFER with no cancel or releaseLock and reported
a fabricated EOF. This must not consume memory/backpressure indefinitely.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_bounded_model_evidence_clone_releases_reader_without_native_abort():
    if not shutil.which("node"):
        return
    check = subprocess.run(
        ["node", "tests/model_evidence_clone_limit_v333.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert check.returncode == 0, check.stdout + "\n" + check.stderr


def test_model_clone_limit_is_non_terminal_and_has_a_cleanup_observer():
    src = (ROOT / "chrome_extension/model_evidence_main_v144.js").read_text()
    assert 'phase: nativeEof ? "done" : "limit"' in src
    assert "const cancellation = reader.cancel();" in src
    assert "cancellation?.catch?.(() => {});" in src
    assert "try { reader.releaseLock(); } catch (_) {}" in src
    assert "emitEvidence(nativeEof);" in src
    content = (ROOT / "chrome_extension/content_model_evidence_v144.js").read_text()
    assert 'detail.phase === "limit"' in content
