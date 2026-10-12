from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_hydrated_composer_match_without_unsafe_relaxed_content():
    result = subprocess.run(["node", "tests/composer_readiness_reconcile_v126.mjs"], cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_prompt_repaired_only_before_click_and_once():
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "promptRepairAttempts += 1" in source
    assert '"verified-draft-hydration-drift"' in source
    assert "current === active.promptComposer" in source or "active.promptComposer" in source
    assert "send_prompt_match: promptMatchEvidence" in source
    assert "send_expected_normalized_chars" in source
    assert "&& !isGenerating()" in source


def test_responses_400_error_category_without_prompt_content(caplog):
    import logging
    from app.responses_v108_patch import _error
    with caplog.at_level(logging.WARNING, logger="chat2api.responses_validation"):
        reply = _error("input must contain text", param="input")
    assert reply.status_code == 400
    assert "reason=input-empty-or-unsupported-shape" in caplog.text
    assert "token" not in caplog.text.lower()
