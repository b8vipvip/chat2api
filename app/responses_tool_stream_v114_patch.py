from __future__ import annotations

"""Codex 0.149 Responses custom-tool bridge normalization.

v113 repaired malformed raw-JavaScript envelopes and canonicalized the streamed
Responses item shape. Real FDEX MCP smoke traffic exposed one more model-produced
shape: a custom tool call whose raw ``input`` was incorrectly nested inside an
``arguments`` wrapper. The v109 compatibility layer serialized that wrapper as
JSON and Codex executed the JSON object text as JavaScript, producing
``SyntaxError: Unexpected token ':'`` and retrying until the smoke timed out.

v114 narrowly promotes the nested raw custom input after matching the requested
tool catalog. It does not relax tool allowlisting and rejects conflicting nested
name/namespace values.
"""

from typing import Any

from fastapi import FastAPI

from . import responses_emulated_tools_v109_patch as v109
from .responses_tool_stream_v113_patch import (
    _canonical_done_item,
    _canonical_response_tool_items,
    _mark_tool_follow_up,
    _pending_item,
    _recover_single_custom_tool_envelope,
    _restore_tool_namespaces,
    _tool_argument_events,
    install_responses_tool_stream_v113_patch,
)


PATCH_REVISION = 114
_BASE_CALL_ITEM = v109._call_item


def _normalize_nested_custom_call(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Promote ``arguments.input`` only for an allowlisted custom tool.

    ChatGPT occasionally mirrors the transport example and emits::

        {"name":"exec","arguments":{"name":"exec","input":"...raw JS..."}}

    Custom tools do not take JSON ``arguments``; their payload is the raw
    top-level ``input`` string. Serializing the wrapper makes the V8 exec host
    receive JSON source instead of JavaScript. Keep recovery deliberately narrow:
    the outer tool must resolve against the supplied catalog, it must be a custom
    tool, and any duplicated nested identity must agree with the outer identity.
    """
    current = dict(call)
    if current.get("input") is not None:
        return current

    arguments = current.get("arguments")
    if not isinstance(arguments, dict) or "input" not in arguments:
        return current

    namespace = str(current.get("namespace") or "").strip() or None
    name = str(current.get("name") or "").strip()
    if not name:
        return current

    tool = v109._match_tool(catalog, namespace, name)
    if tool is None or str(tool.get("type") or "") != "custom":
        return current

    nested_name = str(arguments.get("name") or "").strip()
    if nested_name and nested_name != name:
        raise ValueError(
            f"Nested custom tool name {nested_name!r} does not match outer tool {name!r}"
        )

    nested_namespace = str(arguments.get("namespace") or "").strip() or None
    catalog_namespace = str(tool.get("namespace") or "").strip() or None
    if nested_namespace and namespace and nested_namespace != namespace:
        raise ValueError(
            "Nested custom tool namespace does not match the outer namespace"
        )
    if nested_namespace and catalog_namespace and nested_namespace != catalog_namespace:
        raise ValueError(
            "Nested custom tool namespace does not match the allowlisted catalog namespace"
        )

    raw_input = arguments.get("input")
    if not isinstance(raw_input, str):
        raise ValueError("Nested custom tool input must be a raw string")

    normalized = dict(current)
    normalized["input"] = raw_input
    normalized.pop("arguments", None)
    if not namespace and nested_namespace:
        normalized["namespace"] = nested_namespace
    return normalized


def _call_item_with_nested_custom_recovery(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    return _BASE_CALL_ITEM(_normalize_nested_custom_call(call, catalog), catalog)


def install_responses_tool_stream_v114_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v114_installed", False):
        return app
    install_responses_tool_stream_v113_patch(app)
    v109._call_item = _call_item_with_nested_custom_recovery
    app.state.responses_tool_stream_v114_installed = True
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
    "install_responses_tool_stream_v114_patch",
]
