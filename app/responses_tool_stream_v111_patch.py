from __future__ import annotations

"""Compatibility import for the Responses tool-stream normalizer.

v111 introduced streamed argument/input lifecycle events. v112 keeps that
lifecycle but makes the authoritative output_item.done payload match the exact
minimal shape used by Codex 0.149 and marks tool-call responses end_turn=false.
Keep the historical module/import name because the layered runtime installer and
older contract tests import it directly.
"""

from typing import Any

from fastapi import FastAPI

from .responses_tool_stream_v112_patch import (
    PATCH_REVISION,
    _canonical_done_item,
    _mark_tool_follow_up,
    _pending_item,
    _tool_argument_events,
    install_responses_tool_stream_v112_patch,
)


def install_responses_tool_stream_v111_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v111_installed", False) and getattr(
        app.state, "responses_tool_stream_v112_installed", False
    ):
        return app
    install_responses_tool_stream_v112_patch(app)
    app.state.responses_tool_stream_v111_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_canonical_done_item",
    "_mark_tool_follow_up",
    "_pending_item",
    "_tool_argument_events",
    "install_responses_tool_stream_v111_patch",
]
