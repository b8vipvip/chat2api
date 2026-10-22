"""Submission-evidence VM regression for the 0.22.176 successful image request."""
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pre_click_prompt_verification_uses_the_single_request_v6_controller():
    result = subprocess.run(
        ["node", "tests/submit_click_match_v189.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
