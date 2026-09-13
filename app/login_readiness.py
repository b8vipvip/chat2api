from __future__ import annotations

from typing import Any


READY_STATES = frozenset({"ready", "logged_in", "authenticated"})


def login_readiness(metadata: Any) -> dict[str, Any]:
    """Normalize the fail-closed ChatGPT login admission signal.

    Transport/WebSocket online state only proves that the Worker bridge is alive.
    API traffic is safe only after the browser reports a logged-in ChatGPT page
    with a usable composer. Unknown/checking/login_required are never routable.
    """
    source = metadata if isinstance(metadata, dict) else {}
    bridge = source.get("bridge") if isinstance(source.get("bridge"), dict) else {}
    state = str(
        source.get("chatgpt_login_state")
        or bridge.get("login_state")
        or source.get("chatgpt_status")
        or "unknown"
    ).strip().lower()
    if "chatgpt_login_composer_ready" in source:
        composer_ready = source.get("chatgpt_login_composer_ready") is True
    else:
        composer_ready = bridge.get("composer_ready") is True
    checked_at = source.get("chatgpt_login_checked_at_ms")
    if checked_at is None:
        checked_at = bridge.get("login_checked_at_ms")
    try:
        checked_at_ms = max(0, int(checked_at or 0))
    except (TypeError, ValueError):
        checked_at_ms = 0
    ready = state in READY_STATES and composer_ready
    return {
        "state": state or "unknown",
        "composer_ready": composer_ready,
        "checked_at_ms": checked_at_ms,
        "ready": ready,
        "reason": "ready" if ready else (
            "login_required" if state == "login_required" else
            "composer_not_ready" if state in READY_STATES else
            state or "unknown"
        ),
    }


def chatgpt_routing_ready(metadata: Any) -> bool:
    return bool(login_readiness(metadata)["ready"])
