from __future__ import annotations

"""Compatibility import for the Responses tool-stream normalizer.

v112 made Codex 0.149 output_item.done payloads minimal. v113 repaired malformed
raw custom-tool input and canonicalized response.completed items. v114 additionally
unwraps the real model-produced ``arguments: {name, input}`` custom-tool shape so
Codex receives raw JavaScript rather than serialized JSON. Keep this module name
because older installers import it.
"""

from typing import Any

from fastapi import FastAPI

from .responses_tool_stream_v114_patch import (
    PATCH_REVISION,
    _canonical_done_item,
    _canonical_response_tool_items,
    _mark_tool_follow_up,
    _normalize_nested_custom_call,
    _pending_item,
    _recover_single_custom_tool_envelope,
    _restore_tool_namespaces,
    _tool_argument_events,
    install_responses_tool_stream_v114_patch,
)


def install_responses_tool_stream_v112_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v112_installed", False) and getattr(
        app.state, "responses_tool_stream_v114_installed", False
    ):
        return app
    install_responses_tool_stream_v114_patch(app)
    app.state.responses_tool_stream_v112_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_canonical_done_item",
    "_canonical_response_tool_items",
    "_mark_tool_follow_up",
    "_normalize_nested_custom_call",
    "_pending_item",
    "_recover_single_custom_tool_envelope",
    "_restore_tool_namespaces",
    "_tool_argument_events",
    "install_responses_tool_stream_v112_patch",
]
