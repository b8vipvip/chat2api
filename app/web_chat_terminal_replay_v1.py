"""Strict acceptance and safe projection for replayed Worker terminal outbox events."""
from __future__ import annotations

from typing import Any
import time


_TERMINAL_TYPES = {"chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"}


def is_replayed_mode1_terminal(
    telemetry_row: Any, message: Any, client_id: str,
) -> bool:
    if not isinstance(telemetry_row, dict) or not isinstance(message, dict):
        return False
    diagnostics = message.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    message_type = str(message.get("type") or "")
    row_diagnostics = telemetry_row.get("diagnostics")
    row_diagnostics = row_diagnostics if isinstance(row_diagnostics, dict) else {}
    row_status = str(telemetry_row.get("status") or "").lower()
    recoverable_state = (
        row_status == "recovering"
        or (
            row_status == "stalled"
            and row_diagnostics.get("web_chat_request_recovery") in {
                "broker-state-lost-after-restart-grace-expired",
                "broker-state-lost-during-replay",
            }
        )
    )
    return bool(
        message_type in _TERMINAL_TYPES
        and diagnostics.get("worker_transport_replayed") is True
        and diagnostics.get("worker_transport_recovery") == "transport-outbox-v47"
        and telemetry_row.get("web_chat_mode") is True
        and recoverable_state
        and str(telemetry_row.get("client_id") or "") == str(client_id or "")
        and str(telemetry_row.get("api_key_id") or "")
        and str(telemetry_row.get("api_key_id") or "") != "master"
    )


def is_already_finalized_worker_terminal(
    telemetry_row: Any, client_id: str, terminal_type: str = "",
) -> bool:
    """ACK only an idempotent duplicate owned by the same Worker."""
    if not isinstance(telemetry_row, dict):
        return False
    if str(telemetry_row.get("client_id") or "") != str(client_id or ""):
        return False
    status = str(telemetry_row.get("status") or "").lower()
    if telemetry_row.get("web_chat_mode") is True:
        key_id = str(telemetry_row.get("api_key_id") or "")
        if not key_id or key_id == "master":
            return False
        diagnostics = telemetry_row.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        replay_type = str(diagnostics.get("web_chat_terminal_replay_type") or "")
        if status == "stalled":
            # A replay that was already persisted as provisional/stalled must
            # still be ACKed if the Worker missed the first ACK. Match the
            # exact terminal type so a different event cannot close its outbox.
            return bool(
                diagnostics.get("web_chat_request_recovery")
                == "worker-terminal-replayed-after-broker-restart"
                and replay_type
                and (not terminal_type or replay_type == terminal_type)
            )
        if status not in {"completed", "error", "cancelled"}:
            return False
        if replay_type:
            return not terminal_type or replay_type == terminal_type
        if terminal_type:
            allowed = {
                "completed": {"chat.completed", "image.completed"},
                "error": {"chat.error", "image.error"},
                "cancelled": {"chat.cancelled", "image.cancelled"},
            }.get(status, set())
            return terminal_type in allowed
        return True
    if status not in {"completed", "error", "cancelled"}:
        return False
    return True


def replayed_terminal_projection(
    telemetry_row: dict[str, Any], message: dict[str, Any],
) -> dict[str, Any]:
    message_type = str(message.get("type") or "")
    status = (
        "stalled" if message_type in {"chat.completed", "image.completed"}
        else "cancelled" if message_type in {"chat.cancelled", "image.cancelled"}
        else "error"
    )
    candidate = message.get("text")
    candidate_text = (
        candidate[:50_000]
        if isinstance(candidate, str) and message_type == "chat.completed" else ""
    )
    diagnostics = telemetry_row.get("diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    diagnostics.update({
        "web_chat_request_recovery": "worker-terminal-replayed-after-broker-restart",
        "web_chat_terminal_replay_type": message_type,
        "web_chat_terminal_replay_reconciled": (
            "provisional-only" if message_type == "chat.completed"
            else "media-terminal-only" if message_type == "image.completed"
            else "terminal-status"
        ),
        "web_chat_terminal_replay_received_at": time.time(),
        "worker_transport_replayed": True,
    })
    error = (
        "Worker terminal was replayed after Broker restart; final media archival and usage reconciliation were not rerun"
        if message_type in {"chat.completed", "image.completed"}
        else str(message.get("error") or message.get("reason") or "Worker request failed")[:500]
    )
    return {
        "status": status,
        "response_text": candidate_text,
        "response_text_available": False,
        "error": error,
        "diagnostics": diagnostics,
    }
