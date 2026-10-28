"""Request-v6 P0 short-reply recovery gate is read-only and privacy bounded."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_roleless_short_abort_gate_evidence_is_observational():
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text()
    assert "function shortAbortGateEvidence(active, current)" in source
    assert "...shortAbortGateEvidence(active, current)" in source
    assert 'response_recovery_short_gate_revision: 233' in source
    if shutil.which("node"):
        result = subprocess.run(
            ["node", "tests/short_abort_gate_diagnostics_v233.mjs"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
