from __future__ import annotations

"""Responses v118: normalize exact namespace-qualified nested custom identities.

Real FDEX full-smoke traffic on v0.22.72 produced a valid custom ``exec`` wrapper
whose duplicated inner identity was ``functions.exec`` while the outer identity
was ``namespace=functions, name=exec``. v114 treated the qualified and split forms
as different tools and rejected the call even though both resolve to the same
allowlisted catalog entry.

v118 accepts only that exact equivalence. Truly different nested names or
namespaces continue through v114's fail-closed validation unchanged.
"""

from typing import Any

from fastapi import FastAPI

from . import responses_emulated_tools_v109_patch as v109
from . import responses_tool_stream_v114_patch as v114
from . import responses_tool_stream_v115_patch as v115
from .responses_tool_stream_v116_patch import (
    _json_object_v116,
    _recover_nested_arguments_custom_tool_envelope,
    install_responses_tool_stream_v116_patch,
)


PATCH_REVISION = 118
_BASE_NORMALIZE = v114._normalize_nested_custom_call


def _normalize_nested_custom_call_v118(
    call: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    current = dict(call)
    arguments = current.get("arguments")
    if not isinstance(arguments, dict):
        return _BASE_NORMALIZE(current, catalog)

    namespace = str(current.get("namespace") or "").strip() or None
    name = str(current.get("name") or "").strip()
    nested_name = str(arguments.get("name") or "").strip()
    if not name or not nested_name:
        return _BASE_NORMALIZE(current, catalog)

    tool = v109._match_tool(catalog, namespace, name)
    if tool is None or str(tool.get("type") or "") != "custom":
        return _BASE_NORMALIZE(current, catalog)

    catalog_namespace = str(tool.get("namespace") or "").strip() or None
    equivalent_qualified_names = {
        f"{value}.{name}"
        for value in (namespace, catalog_namespace)
        if value
    }
    if nested_name not in equivalent_qualified_names:
        return _BASE_NORMALIZE(current, catalog)

    normalized_arguments = dict(arguments)
    normalized_arguments["name"] = name
    if "namespace" not in normalized_arguments and (namespace or catalog_namespace):
        normalized_arguments["namespace"] = namespace or catalog_namespace
    current["arguments"] = normalized_arguments
    return _BASE_NORMALIZE(current, catalog)


def install_responses_tool_stream_v118_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v118_installed", False):
        return app
    install_responses_tool_stream_v116_patch(app)
    # v115's call-item recovery delegates into the v114 module at runtime, so
    # replacing the module-level normalizer upgrades both direct custom exec calls
    # and the nested collaboration recovery path without weakening v109 allowlisting.
    v114._normalize_nested_custom_call = _normalize_nested_custom_call_v118
    v109._call_item = v115._call_item_with_nested_exec_recovery
    app.state.responses_tool_stream_v118_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_json_object_v116",
    "_normalize_nested_custom_call_v118",
    "_recover_nested_arguments_custom_tool_envelope",
    "install_responses_tool_stream_v118_patch",
]
