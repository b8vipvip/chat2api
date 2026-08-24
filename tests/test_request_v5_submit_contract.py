from pathlib import Path


def test_late_submit_transition_suppresses_duplicate_resend():
    source = Path("chrome_extension/content_request_v5.js").read_text(encoding="utf-8")

    # Regression: after a real click, ChatGPT may clear/rebuild the composer only
    # after the first confirmation window. That transition must enter a settlement
    # watch instead of re-entering send-button readiness and clicking again.
    assert 'return promptPresent ? "retry" : "settle";' in source
    assert 'if (action === "settle")' in source
    assert 'settleAfterPromptLeftComposer(active, prompt, attempts)' in source
    assert 'submission_retry_suppressed: true' in source
    assert 'duplicate send was suppressed' in source


def test_submit_confirmation_accepts_late_generation_and_assistant_turn():
    source = Path("chrome_extension/content_request_v5.js").read_text(encoding="utf-8")

    assert 'if (generating) return { reason: "generating"' in source
    assert 'if (newAssistant) return { reason: "assistant-turn"' in source
    assert 'if (composerChars === 0) return { reason: "composer-cleared"' in source
    assert 'await waitAfterSend(active, prompt, "late", 20000)' in source
