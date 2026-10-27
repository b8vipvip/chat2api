"""Regression: media generation launched through text Chat Completions."""

from app.chat_media_deadline_v171 import chat_timeout_window, worker_chat_timeout, long_form_text_requested


def test_default_chat_api_and_worker_share_same_600_second_lease():
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False) == 600
    assert worker_chat_timeout(300, automatic_extension=True) == 600
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False, stream=True) == 900
    assert worker_chat_timeout(300, automatic_extension=True, stream=True) == 900


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

def test_long_form_explicit_delivery_is_bounded_to_600_without_media():
    prompt = "[User]\n请撰写不少于 3,000 字的专业实施方案，所有正文直接在聊天中呈现。"
    assert long_form_text_requested(prompt)
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False,
                               long_text_requested=long_form_text_requested(prompt)) == 600
    assert not long_form_text_requested("请为我简单回答一个问题。")
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False,
                               long_text_requested=False) == 600


def test_explicit_timeout_still_takes_priority_over_long_document():
    assert chat_timeout_window(120, automatic_extension=False, media_handoff_proven=False,
                               long_text_requested=True) == 120
    assert chat_timeout_window(900, automatic_extension=True, media_handoff_proven=False,
                               long_text_requested=True) == 900


def test_worker_and_api_default_deadline_never_split_under_all_evidence_combinations():
    for stream in (False, True):
        for base in (60, 300, 450, 600, 900, 1200):
            expected = worker_chat_timeout(base, automatic_extension=True, stream=stream)
            for media in (False, True):
                for long_text in (False, True):
                    for progress in (False, True):
                        assert chat_timeout_window(
                            base, automatic_extension=True,
                            media_handoff_proven=media, long_text_requested=long_text,
                            stream_progress_proven=progress, stream=stream
                        ) == expected


def test_explicit_client_timeout_uses_one_lease_without_auto_extension():
    for stream in (False, True):
        for timeout in (45, 120, 300, 600, 900):
            assert chat_timeout_window(
                timeout, automatic_extension=False,
                media_handoff_proven=True, long_text_requested=True,
                stream_progress_proven=True, stream=stream,
            ) == worker_chat_timeout(timeout, automatic_extension=False, stream=stream)


def test_server_always_passes_stream_mode_to_deadline_and_emits_mismatch_diagnostics():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main = (root / "app/main.py").read_text()
    segment = main[main.index("    def chat_deadline_remaining("):main.index("    def archive_final_markdown(")]
    assert "stream=stream," in segment
    assert "server_worker_lease_aligned" in segment
    assert "deadline_policy_authority" in segment
