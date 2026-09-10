from __future__ import annotations

"""Compatibility import for the Responses tool-stream normalizer.

v112 made Codex 0.149 output_item.done payloads minimal. v113 repaired malformed
raw custom-tool input and canonicalized response.completed items. v114 unwraps
model-produced ``arguments: {name, input}`` custom-tool payloads. v115 additionally
recovers explicitly declared nested exec tools (notably collaboration spawn/wait)
when the model lifts them into the outer bridge envelope. v116 closes the real
FDEX full-smoke gap where ``arguments.input`` itself contains unescaped raw-JS
quotes. v118 additionally accepts the exact equivalent duplicated identity
``functions.exec`` for an outer ``namespace=functions, name=exec`` custom tool.
Keep this module name because older installers import it.
"""

from typing import Any

from fastapi import FastAPI

from .responses_tool_stream_v118_patch import (
    PATCH_REVISION,
    _json_object_v116,
    _normalize_nested_custom_call_v118,
    _recover_nested_arguments_custom_tool_envelope,
    install_responses_tool_stream_v118_patch,
)
from .responses_tool_stream_v115_patch import (
    _call_item_with_nested_exec_recovery,
    _canonical_done_item,
    _canonical_response_tool_items,
    _declared_nested_tool_names,
    _mark_tool_follow_up,
    _normalize_nested_custom_call,
    _normalize_undeclared_nested_exec_call,
    _pending_item,
    _recover_single_custom_tool_envelope,
    _restore_tool_namespaces,
    _tool_argument_events,
)


def install_responses_tool_stream_v112_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v112_installed", False) and getattr(
        app.state, "responses_tool_stream_v118_installed", False
    ):
        return app
    install_responses_tool_stream_v118_patch(app)
    app.state.responses_tool_stream_v112_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_call_item_with_nested_exec_recovery",
    "_canonical_done_item",
    "_canonical_response_tool_items",
    "_declared_nested_tool_names",
    "_json_object_v116",
    "_mark_tool_follow_up",
    "_normalize_nested_custom_call",
    "_normalize_nested_custom_call_v118",
    "_normalize_undeclared_nested_exec_call",
    "_pending_item",
    "_recover_nested_arguments_custom_tool_envelope",
    "_recover_single_custom_tool_envelope",
    "_restore_tool_namespaces",
    "_tool_argument_events",
    "install_responses_tool_stream_v112_patch",
]
