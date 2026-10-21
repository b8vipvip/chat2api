"""Regression: media generation launched through text Chat Completions."""

from app.chat_media_deadline_v171 import chat_timeout_window, worker_chat_timeout


def test_default_chat_remains_normal_without_verified_handoff():
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False) == 300
    assert worker_chat_timeout(300, automatic_extension=True) == 600


def test_correlated_handoff_extends_only_same_request_to_600_seconds():
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=True) == 600
    assert chat_timeout_window(450, automatic_extension=True, media_handoff_proven=True) == 600
    assert chat_timeout_window(720, automatic_extension=True, media_handoff_proven=True) == 720


def test_explicit_deadline_is_not_overridden_even_for_image_handoff():
    for seconds in (60, 300, 600, 900):
        assert chat_timeout_window(seconds, automatic_extension=False, media_handoff_proven=True) == seconds
        assert worker_chat_timeout(seconds, automatic_extension=False) == seconds


def test_media_diagnostic_is_evidence_not_a_terminal_result():
    # The helper never examines media payloads, guesses from canvas counts or
    # alters a status. The server must see the exact handoff boolean.
    diagnostics = {"canvas_nodes": 2, "media_handoff_marker": False}
    assert chat_timeout_window(300, automatic_extension=True,
                               media_handoff_proven=diagnostics.get("media_handoff_marker") is True) == 300
    diagnostics["media_handoff_marker"] = True
    assert chat_timeout_window(300, automatic_extension=True,
                               media_handoff_proven=diagnostics.get("media_handoff_marker") is True) == 600
