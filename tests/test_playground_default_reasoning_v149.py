from __future__ import annotations

from pathlib import Path
import subprocess

from app.v13_patch import _target_from_payload


ROOT = Path(__file__).resolve().parents[1]


def test_chat_playground_defaults_to_page_native_effort_not_medium() -> None:
    for filename in ("admin_playground_chat.js", "admin_playground_chat_v69.js"):
        source = (ROOT / "app" / filename).read_text(encoding="utf-8")
        assert '<option value="" selected>自动（页面默认）</option>' in source
        expected = ('if (!imageMode && model !== "gpt-5.5-mini" && $chat("pgChatReasoning").value)'
                    if filename == "admin_playground_chat_v69.js"
                    else 'if (model !== "gpt-5.5-mini" && $chat("pgChatReasoning").value)')
        assert expected in source
        if filename == "admin_playground_chat_v69.js":
            assert 'body.reasoning_effort = $chat("pgChatReasoning").value;' in source
            assert 'const imageMode = $chat("pgChatMode").value === "image"' in source
        assert '$chat("pgChatReasoning").value || "medium"' not in source
        result = subprocess.run(["node", "--check", str(ROOT / "app" / filename)],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_automated_playground_does_not_synthesize_medium_effort() -> None:
    source = (ROOT / "app/admin_playground_lifecycle.js").read_text(encoding="utf-8")
    assert '(document.getElementById("testReasoning")?.value || null)' in source
    assert '(document.getElementById("testReasoning")?.value || "medium")' not in source
    result = subprocess.run(["node", "--check", str(ROOT / "app/admin_playground_lifecycle.js")],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_api_model_contract_preserves_only_explicit_reasoning() -> None:
    native = _target_from_payload({"model": "gpt-5.6-sol"})
    assert native["reasoning_level"] is None
    assert native["reasoning_effort"] is None
    explicit = _target_from_payload({"model": "gpt-5.6-sol", "reasoning_effort": "medium"})
    assert explicit["reasoning_level"] == "medium"
    assert explicit["reasoning_effort"] == "medium"
    driver = (ROOT / "chrome_extension/content_page_driver_v22.js").read_text(encoding="utf-8")
    assert 'else if (requestedReasoning && !current.reasoning_trusted) code = "reasoning_state_untrusted";' in driver
