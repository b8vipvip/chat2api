"""General diagnostic failure classifications must preserve evidence boundaries."""
from scripts import native_page_diagnostic_v1 as diagnostic


def test_visible_unassigned_content_has_specific_cause():
    probe = {"history_content_visible_unassigned": True,
             "history_load_timeout": False,
             "capture_error": "no-verified-assistant-boundary",
             "main_visible_text_chars": 11281,
             "main_longest_child_paragraphs": 260,
             "main_visible_tables": 1}
    assert diagnostic.failure_reason(probe) == (
        "history_visible_content_without_safe_reply_boundary")


def test_tab_disappearance_precedes_generic_timeout():
    assert diagnostic.failure_reason({
        "navigation_route": "target-not-visible",
        "history_load_timeout": True,
    }) == "history_diagnostic_tab_disappeared"


def test_generic_timeout_remains_fail_closed():
    assert diagnostic.failure_reason({
        "history_load_timeout": True,
        "main_visible_text_chars": 0,
    }) == "history_load_stability_timeout_no_complete_evidence"
