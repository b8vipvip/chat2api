"""Structural diagnostics must remain observational, never terminal authority."""
from pathlib import Path


def test_roleless_reply_mismatch_probe_is_present_and_privacy_safe():
    source = (Path(__file__).resolve().parents[1] / "chrome_extension"
              / "content_request_v6.js").read_text(encoding="utf-8")
    begin = source.index("function shortAbortGateEvidence(active, current)")
    end = source.index("function renderedNetworkCorrelatedTurn(active)", begin)
    observer = source[begin:end]
    for key in (
        "response_recovery_roleless_pair_same_parent",
        "response_recovery_roleless_answer_sibling_tag",
        "response_recovery_roleless_answer_sibling_chars",
        "response_recovery_roleless_answer_sibling_visible",
        "response_recovery_roleless_answer_sibling_source_overlap",
        "response_recovery_roleless_fresh_action_after_heading",
    ):
        assert key in observer
    assert "...rolelessStructure" in observer
    # No captured prompt, answer, HTML, or side-effecting completion is emitted.
    assert "response_recovery_roleless_answer_sibling_text:" not in observer
    assert "chat.completed" not in observer
    assert "chat.error" not in observer
