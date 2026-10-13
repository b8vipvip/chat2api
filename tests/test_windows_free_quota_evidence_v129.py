from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_chinese_chatgpt_quota_notice_is_classified_before_post_click_timeout():
    outcome = subprocess.run(["node", "tests/windows_free_quota_evidence_v129.mjs"],
                             cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_only_canonical_request_controller_can_turn_quota_evidence_into_terminal():
    detector = (ROOT / "chrome_extension/content_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    controller = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert 'type: "chat.error"' not in detector
    assert "upstreamRateLimitFailure(active)" in controller
    assert "result?.upstreamRateLimited" in controller
    assert "if (active.rateLimitTerminalError) return { upstreamRateLimited: true }" in controller
    assert "throw upstreamRateLimitFailure(active)" in controller
