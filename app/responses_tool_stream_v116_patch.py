from __future__ import annotations

"""Responses v116: recover the exact malformed nested custom-exec envelope.

The v113-v115 stack already handles raw custom input, nested ``arguments.input``
once valid JSON has been parsed, and lifted nested collaboration calls. Real FDEX
full-smoke traffic exposed the remaining intersection: ChatGPT can emit the custom
``exec`` input inside ``arguments`` *and* leave quotes in the raw JavaScript
unescaped. The outer bridge JSON is therefore invalid before v114 can normalize
it. This patch recovers only that single-call shape and still lets the existing
allowlist/call-item stack decide whether it is executable.
"""

import re
from typing import Any

from fastapi import FastAPI

from . import responses_emulated_tools_v109_patch as v109
from . import request_window_observability_v117_patch as window_observability
from .responses_tool_stream_v113_patch import (
    _decode_lenient_json_string,
    _json_object_with_custom_recovery,
)
from .responses_tool_stream_v115_patch import install_responses_tool_stream_v115_patch


PATCH_REVISION = 116


def _recover_nested_arguments_custom_tool_envelope(text: str) -> dict[str, Any] | None:
    """Recover one malformed ``arguments:{name,input}`` custom call.

    Recovery is deliberately syntax-only. The returned call still flows through
    v115 -> v114 -> v109, where the outer custom tool must match the supplied
    request catalog. Unknown tools remain fail-closed.
    """
    raw = str(text or "").strip()
    if v109.BRIDGE_START in raw and v109.BRIDGE_END in raw:
        raw = raw.split(v109.BRIDGE_START, 1)[1].split(v109.BRIDGE_END, 1)[0].strip()

    outer = re.fullmatch(
        r'\s*\{\s*"kind"\s*:\s*"tool_calls"\s*,\s*"calls"\s*:\s*\[\s*\{(?P<body>.*)\}\s*\]\s*\}\s*',
        raw,
        flags=re.DOTALL,
    )
    if outer is None:
        return None

    body = outer.group("body")
    call = re.fullmatch(
        r'\s*(?:(?:"namespace"\s*:\s*(?:"(?P<namespace>[^"]+)"|null)\s*,\s*)?)'
        r'"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*'
        r'"arguments"\s*:\s*\{\s*'
        r'"name"\s*:\s*"(?P<nested_name>[^"]+)"\s*,\s*'
        r'"input"\s*:\s*"(?P<input>.*)"\s*\}\s*',
        body,
        flags=re.DOTALL,
    )
    if call is None:
        return None

    nested_name = str(call.group("nested_name") or "").strip()
    outer_name = str(call.group("name") or "").strip()
    if not nested_name or not outer_name:
        return None

    result: dict[str, Any] = {
        "name": outer_name,
        "arguments": {
            "name": nested_name,
            "input": _decode_lenient_json_string(call.group("input")),
        },
    }
    namespace = call.group("namespace")
    if namespace:
        result["namespace"] = namespace
    return {"kind": "tool_calls", "calls": [result]}


def _json_object_v116(text: str) -> dict[str, Any] | None:
    parsed = _json_object_with_custom_recovery(text)
    if parsed is not None:
        return parsed
    return _recover_nested_arguments_custom_tool_envelope(text)


def install_responses_tool_stream_v116_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v116_installed", False):
        return app
    install_responses_tool_stream_v115_patch(app)
    v109._json_object = _json_object_v116
    window_observability.install_request_window_observability_v117_patch(app)
    app.state.responses_tool_stream_v116_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app


__all__ = [
    "PATCH_REVISION",
    "_json_object_v116",
    "_recover_nested_arguments_custom_tool_envelope",
    "install_responses_tool_stream_v116_patch",
]
