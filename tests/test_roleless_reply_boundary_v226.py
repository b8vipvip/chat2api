"""Verify roleless ChatGPT reply guards with the real v6 source, not copies."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v226_roleless_transcript_and_no_prompt_leak() -> None:
    scripts = (
        "tests/request_prompt_echo_boundary_v212.mjs",
        "tests/multiturn_prompt_boundary_v214.mjs",
        "tests/roleless_transcript_boundary_v226.mjs",
    )
    for script in scripts:
        result = subprocess.run(
            ["node", str(ROOT / script)], cwd=ROOT,
            capture_output=True, text=True, timeout=25,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}\n{result.stdout}"

def test_v226_worker_egress_stays_single_owner() -> None:
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "assistantOnlyReplyText(active, event.text)" in source
    assert 'if (isRenderedHeading) return { text: "", stripped: false, pending: true };' in source
    assert 'response_boundary_evidence_v226:' in source
    assert "empty-rendered-user-heading-v226" in source
    assert "three-ordered-echo-fingerprints-v226" in source
    assert "Native ChatGPT elapsed-time badges are NOT assistant responses." in source
