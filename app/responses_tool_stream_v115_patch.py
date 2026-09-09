from __future__ import annotations

"""Responses nested-exec tool-call normalization for Codex 0.149.

The Responses v109 bridge exposes Codex's large nested tool surface through one
allowlisted custom ``exec`` tool. Real GPT-5.6 Sol FDEX full-smoke traffic showed
that the model can occasionally lift a nested collaboration tool into the outer
bridge envelope, for example::

    {"namespace":"functions.collaboration","name":"spawn_agent",...}
    {"namespace":"functions.collaboration","name":"collaboration__spawn_agent",...}
    {"namespace":"collaboration","name":"collaboration__spawn_agent",...}

Those calls are not top-level catalog entries, so v109 correctly rejected them as
undeclared. v115 keeps that fail-closed contract but recognizes an exact nested
tool name only when it is explicitly declared inside the allowlisted custom
``exec`` description. The call is then translated back into raw JavaScript for
that same custom exec host. Unknown nested names remain rejected.
"""

import json
import re
from typing import Any

from fastapi import FastAPI

from . import responses_emulated_tools_v109_patch as v109
from .responses_tool_stream_v114_patch import (
    _call_item_with_nested_custom_recovery,
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


PATCH_REVISION = 115
_NESTED_TOOL_NAME = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__[A-Za-z_][A-Za-z0-9_]*)\s*\(")
_SAFE_JS_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _declared_nested_tool_names(exec_tool: dict[str, Any]) -> set[str]:
    description = str(exec_tool.get("description") or "")
    return set(_NESTED_TOOL_NAME.findall(description))


def _nested_candidates(namespace: str | None, name: str) -> list[str]:
    candidates: list[str] = []
    if "__" in name:
        candidates.append(name)
    if namespace:
        tail = namespace.rsplit(".", 1)[-1].strip()
        if tail and "__" not in name:
            candidates.append(f"{tail}__{name}")
        elif tail and name.startswith(f"{tail}__"):
            candidates.append(name)
    # Preserve order while de-duplicating.
    return list(dict.fromkeys(item for item in candidates if item))


def _exec_tool_for_nested_call(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    namespace = str(call.get("namespace") or "").strip() or None
    name = str(call.get("name") or "").strip()
    if not name:
        return None

    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        return None

    matches: list[tuple[dict[str, Any], str]] = []
    for tool in catalog:
        if str(tool.get("type") or "") != "custom" or str(tool.get("name") or "") != "exec":
            continue
        declared = _declared_nested_tool_names(tool)
        for candidate in _nested_candidates(namespace, name):
            if candidate in declared and _SAFE_JS_IDENTIFIER.fullmatch(candidate):
                matches.append((tool, candidate))

    # Ambiguous exec hosts must not be guessed.
    unique = {(str(tool.get("namespace") or ""), str(tool.get("name") or ""), candidate) for tool, candidate in matches}
    if len(unique) != 1:
        return None
    return matches[0]


def _normalize_undeclared_nested_exec_call(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Translate an explicitly-declared nested tool back through custom exec.

    The nested tool name comes only from the trusted request catalog description,
    and arguments are JSON encoded before embedding in JavaScript. This does not
    turn arbitrary undeclared model output into an executable tool call.
    """
    if v109._match_tool(
        catalog,
        str(call.get("namespace") or "").strip() or None,
        str(call.get("name") or "").strip(),
    ) is not None:
        return dict(call)

    matched = _exec_tool_for_nested_call(call, catalog)
    if matched is None:
        return dict(call)
    exec_tool, nested_name = matched
    arguments = call.get("arguments")
    assert isinstance(arguments, dict)
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    source = (
        f"const __chat2api_nested_result = await tools.{nested_name}({encoded});\n"
        "if (__chat2api_nested_result !== undefined) text(__chat2api_nested_result);"
    )
    return {
        "namespace": str(exec_tool.get("namespace") or "").strip() or None,
        "name": str(exec_tool.get("name") or "exec"),
        "input": source,
    }


def _call_item_with_nested_exec_recovery(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = _normalize_undeclared_nested_exec_call(call, catalog)
    return _call_item_with_nested_custom_recovery(normalized, catalog)


def install_responses_tool_stream_v115_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v115_installed", False):
        return app
    install_responses_tool_stream_v114_patch(app)
    v109._call_item = _call_item_with_nested_exec_recovery
    app.state.responses_tool_stream_v115_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_call_item_with_nested_exec_recovery",
    "_canonical_done_item",
    "_canonical_response_tool_items",
    "_declared_nested_tool_names",
    "_mark_tool_follow_up",
    "_normalize_nested_custom_call",
    "_normalize_undeclared_nested_exec_call",
    "_pending_item",
    "_recover_single_custom_tool_envelope",
    "_restore_tool_namespaces",
    "_tool_argument_events",
    "install_responses_tool_stream_v115_patch",
]
