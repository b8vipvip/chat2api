from app.web_chat_terminal_replay_v1 import (
    is_replayed_mode1_terminal,
    replayed_terminal_projection,
)


def test_only_owner_bound_mode1_terminal_outbox_replay_is_accepted():
    row = {
        "api_key_id": "key-mode1",
        "client_id": "worker-a",
        "web_chat_mode": True,
        "status": "recovering",
    }
    message = {
        "type": "chat.completed",
        "request_id": "req_replay_123",
        "text": "recovered answer",
        "diagnostics": {
            "worker_transport_replayed": True,
            "worker_transport_recovery": "transport-outbox-v47",
        },
    }
    assert is_replayed_mode1_terminal(row, message, "worker-a")
    assert not is_replayed_mode1_terminal(row, message, "worker-b")
    assert not is_replayed_mode1_terminal({**row, "web_chat_mode": False}, message, "worker-a")
    assert not is_replayed_mode1_terminal({**row, "status": "stalled"}, message, "worker-a")
    assert not is_replayed_mode1_terminal({**row, "api_key_id": "master"}, message, "worker-a")
    assert not is_replayed_mode1_terminal(
        row, {**message, "diagnostics": {"worker_transport_replayed": True}}, "worker-a",
    )
    assert not is_replayed_mode1_terminal(
        row, {**message, "type": "image.completed"}, "worker-a",
    )


def test_replayed_completion_is_preserved_as_provisional_not_false_success():
    row = {
        "api_key_id": "key-mode1",
        "client_id": "worker-a",
        "web_chat_mode": True,
        "status": "recovering",
        "diagnostics": {"web_chat_request_recovery": "awaiting-worker-terminal-replay"},
    }
    message = {
        "type": "chat.completed",
        "text": "A" * 60_000,
        "diagnostics": {
            "worker_transport_replayed": True,
            "worker_transport_recovery": "transport-outbox-v47",
        },
    }
    projection = replayed_terminal_projection(row, message)
    assert projection["status"] == "stalled"
    assert projection["response_text"] == "A" * 50_000
    assert projection["response_text_available"] is False
    assert "archival" in projection["error"]
    assert projection["diagnostics"]["web_chat_terminal_replay_reconciled"] == "provisional-only"


def test_replayed_worker_error_and_cancel_keep_their_terminal_semantics():
    row = {"diagnostics": {"existing": True}}
    error = replayed_terminal_projection(row, {"type": "chat.error", "error": "worker failed"})
    cancelled = replayed_terminal_projection(row, {"type": "chat.cancelled", "reason": "user stopped"})
    assert error["status"] == "error"
    assert error["error"] == "worker failed"
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "user stopped"
    assert error["response_text"] == ""
    assert cancelled["response_text"] == ""
