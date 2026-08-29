from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from fastapi import FastAPI


PATCH_ID = "response-semantic-guard-v1"
_UI_ROLE_ONLY = re.compile(
    r"^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]?\s*$",
    re.IGNORECASE,
)
_UI_ROLE_PREFIX = re.compile(
    r"^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]\s*",
    re.IGNORECASE,
)


def _compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sanitize_assistant_text(value: object) -> tuple[str, bool]:
    """Remove ChatGPT page role chrome without accepting it as model output.

    Modern ChatGPT conversation turns can expose an accessibility heading such as
    ``ChatGPT said:`` before the actual markdown body exists. Browser DOM capture
    must never treat that heading as the model answer.
    """
    raw = str(value or "").strip()
    compact = _compact(raw)
    if not compact:
        return "", False
    if _UI_ROLE_ONLY.fullmatch(compact):
        return "", True
    cleaned = _UI_ROLE_PREFIX.sub("", raw, count=1).strip()
    if cleaned != raw:
        return cleaned, True
    return raw, False


def install_response_semantic_guard_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "response_semantic_guard_patch_installed", False):
        return app
    app.state.response_semantic_guard_patch_installed = True

    broker = app.state.broker
    previous_publish: Callable[[str, dict[str, Any]], Awaitable[bool]] = broker.publish

    async def publish_with_semantic_guard(request_id: str, event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "")
        if event_type not in {"chat.delta", "chat.snapshot", "chat.completed"}:
            return await previous_publish(request_id, event)

        state = broker.requests.get(request_id)
        field = "delta" if event_type == "chat.delta" else "text"
        original = str(event.get(field) or "")
        cleaned = original
        filtered = False

        if event_type == "chat.delta":
            # A role heading is only meaningful as UI chrome at the beginning of
            # a response. Once real response text exists, ordinary deltas pass
            # through unchanged except for an exact role-only shell.
            compact = _compact(original)
            if _UI_ROLE_ONLY.fullmatch(compact):
                cleaned, filtered = "", True
            elif not str(getattr(state, "text", "") or ""):
                cleaned, filtered = sanitize_assistant_text(original)
        else:
            cleaned, filtered = sanitize_assistant_text(original)

        if filtered and state is not None:
            count = int(state.diagnostics.get("assistant_ui_boilerplate_filtered_count") or 0) + 1
            state.diagnostics.update(
                {
                    "response_semantic_guard": PATCH_ID,
                    "assistant_ui_boilerplate_filtered": True,
                    "assistant_ui_boilerplate_filtered_count": count,
                    "assistant_ui_boilerplate_last_event": event_type,
                }
            )

        if not cleaned:
            if filtered:
                # Do not complete/release the request. A newer browser recovery
                # path may still observe the real answer moments later.
                return True
            return await previous_publish(request_id, event)

        if cleaned != original:
            event = dict(event)
            event[field] = cleaned
            diagnostics = dict(event.get("diagnostics") or {})
            diagnostics.update(
                {
                    "response_semantic_guard": PATCH_ID,
                    "assistant_ui_boilerplate_stripped": True,
                }
            )
            event["diagnostics"] = diagnostics

        return await previous_publish(request_id, event)

    broker.publish = publish_with_semantic_guard
    return app
