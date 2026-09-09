from __future__ import annotations

"""Compatibility import for the Responses tool-stream normalizer.

v112 made Codex 0.149 output_item.done payloads minimal. v113 keeps that
contract, recovers malformed raw custom-tool bridge input, restores missing
namespaces from the request catalog, and canonicalizes response.completed
tool items too. Keep this module name because older installers import it.
"""

from typing import Any

from fastapi import FastAPI

from .responses_tool_stream_v113_patch import (
    PATCH_REVISION,
    _canonical_done_item,
    _canonical_response_tool_items,
    _mark_tool_follow_up,
    _pending_item,
    _recover_single_custom_tool_envelope,
    _restore_tool_namespaces,
    _tool_argument_events,
    install_responses_tool_stream_v113_patch,
)


def install_responses_tool_stream_v112_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v112_installed", False) and getattr(
        app.state, "responses_tool_stream_v113_installed", False
    ):
        return app
    install_responses_tool_stream_v113_patch(app)
    app.state.responses_tool_stream_v112_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_canonical_done_item",
    "_canonical_response_tool_items",
    "_mark_tool_follow_up",
    "_pending_item",
    "_recover_single_custom_tool_envelope",
    "_restore_tool_namespaces",
    "_tool_argument_events",
    "install_responses_tool_stream_v112_patch",
]
