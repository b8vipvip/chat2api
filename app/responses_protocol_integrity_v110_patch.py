from __future__ import annotations

import json
import re
from typing import Any

from . import responses_emulated_tools_v109_patch as bridge


PATCH_REVISION = 110
PROTOCOL_ERROR_CODE = "responses_tool_bridge_protocol_error"

_ORIGINAL_BRIDGE_PROMPT = bridge._bridge_prompt
_ORIGINAL_INTERPRET = bridge._interpret
_ORIGINAL_COMPLETED_RESPONSE = bridge._completed_response
_LITERAL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{11,}")


class ResponsesToolBridgeProtocolError(ValueError):
    """Raised when ChatGPT returns a transport-invalid emulated-tool response."""


def _input_texts(body: dict[str, Any]) -> list[str]:
    value = body.get("input")
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    texts: list[str] = []
    for item in value:
        if isinstance(item, str):
            texts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "message")
        if kind not in {"message", "input_message", "easy_input_message"}:
            continue
        content = item.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for piece in content:
                if isinstance(piece, str):
                    texts.append(piece)
                elif isinstance(piece, dict):
                    text = piece.get("text") or piece.get("content")
                    if isinstance(text, str):
                        texts.append(text)
    return texts


def _current_user_request(body: dict[str, Any]) -> str:
    """Return only the active user task, not older serialized Codex turns."""
    value = body.get("input")
    texts: list[str] = []
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "message") not in {
                "message", "input_message", "easy_input_message"
            } or str(item.get("role") or "user").lower() != "user":
                continue
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.append("".join(
                    part if isinstance(part, str) else str(part.get("text") or "")
                    for part in content if isinstance(part, (str, dict))
                ))
    if not texts:
        return ""
    current = re.split(r"(?m)^\s*USER MESSAGE:\s*$", texts[-1])[-1]
    current = re.split(
        r"(?m)^\s*(?:ASSISTANT|DEVELOPER|SYSTEM) MESSAGE:\s*$"
        r"|^\s*(?:PRIOR TOOL CALL|TOOL RESULT)\b",
        current,
    )[0]
    return current.strip()


def _tool_has_returned_since_user(body: dict[str, Any], namespace: str | None, name: str) -> bool:
    """Observe actual caller-owned tool results; never infer execution from text claims."""
    value = body.get("input")
    if isinstance(value, list):
        last_user = max(
            (i for i, item in enumerate(value)
             if isinstance(item, dict)
             and str(item.get("role") or "").lower() == "user"
             and str(item.get("type") or "message") in {
                 "message", "input_message", "easy_input_message"
             }),
            default=-1,
        )
        segment = value[last_user + 1:]
        ids = {
            str(item.get("call_id") or "")
            for item in segment
            if isinstance(item, dict)
            and str(item.get("type") or "") in {"function_call", "custom_tool_call"}
            and str(item.get("name") or "") == name
            and (str(item.get("namespace") or "") or None) == namespace
        }
        if ids and any(
            isinstance(item, dict)
            and str(item.get("type") or "") in {"function_call_output", "custom_tool_call_output"}
            and str(item.get("call_id") or "") in ids
            for item in segment
        ):
            return True
        # Codex can serialize its history into one input message instead.
        value = "\n".join(str(item.get("content") or "") for item in value if isinstance(item, dict) and isinstance(item.get("content"), str))
    if isinstance(value, str):
        after_user = re.split(r"(?m)^\s*USER MESSAGE:\s*$", value)[-1]
        if "PRIOR TOOL CALL:" in after_user and "TOOL RESULT (" in after_user:
            for part in after_user.split("PRIOR TOOL CALL:")[1:]:
                if "TOOL RESULT (" not in part:
                    continue
                header = part.split("TOOL RESULT (", 1)[0]
                if (re.search(r'"name"\s*:\s*"' + re.escape(name) + r'"', header)
                    and (namespace is None or re.search(
                        r'"namespace"\s*:\s*"' + re.escape(namespace) + r'"', header
                    ))):
                    return True
    return False


def _explicit_declared_tool_requirement(
    body: dict[str, Any], catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Honor an explicitly named, imperative caller-owned tool request.

    A tool is required only when its *declared* name appears in the current
    user task with a nearby imperative. This never manufactures a tool call:
    the model must still supply exact allowlisted arguments. Historical tool
    names, explanatory questions and already returned tool results do not
    create new obligations.
    """
    current = _current_user_request(body)
    choice = body.get("tool_choice", "auto")
    if not current or choice == "none" or (
        isinstance(choice, dict) and choice.get("type") == "none"
    ):
        return None
    for match in re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*(?![A-Za-z0-9_])", current):
        token = re.sub(r"[^a-z0-9]", "", match.group(0).lower())
        before = current[max(0, match.start() - 90):match.start()]
        if not re.search(r"\b(?:use|call|invoke|execute|run|trigger)\b|调用|使用|执行", before, re.I):
            continue
        if re.search(r"\b(?:explain|describe|define|compare|what is|how to|how does)\b|解释|介绍|如何", before, re.I):
            continue
        if re.search(r"\b(?:do not|don't|never|avoid)\s+(?:use|call|invoke|execute|run)\b.{0,55}$", before, re.I):
            continue
        for tool in catalog:
            name = str(tool.get("name") or "")
            namespace = str(tool.get("namespace") or "").strip() or None
            exact_name = bool(name and token == re.sub(r"[^a-z0-9]", "", name.lower()))
            # The official collaboration API exposes wait_agent while user
            # instructions often call it the "wait tool". This is an alias
            # only for an explicitly named, declared tool, never a synthetic
            # collaboration event or a separate terminal authority.
            official_wait = bool(
                namespace == "collaboration" and name == "wait_agent"
                and token == "wait"
                and re.match(r"(?i)wait\s+tool\b", current[match.start():])
            )
            if not (exact_name or official_wait):
                continue
            if not _tool_has_returned_since_user(body, namespace, name):
                return tool
    return None


def _required_exact_literals(body: dict[str, Any]) -> list[str]:
    """Extract exact literals only from the active user turn."""
    current = _current_user_request(body)
    if not current:
        return []
    lowered = current.lower()
    found: list[str] = []
    for phrase in ("exact marker", "exact string", "exactly this text", "reply with"):
        offset = 0
        while True:
            index = lowered.find(phrase, offset)
            if index < 0:
                break
            window = current[index + len(phrase) : index + len(phrase) + 190]
            candidates = [
                token for token in _LITERAL_TOKEN.findall(window)
                if "_" in token or (any(ch.isdigit() for ch in token)
                                    and not token.startswith("http"))
            ]
            if candidates:
                # Pick the nearest explicit literal following the instruction.
                # A later path/URL is not the required final marker.
                token = candidates[0].rstrip(".,;:!?")
                if token and token not in found:
                    found.append(token)
            offset = index + len(phrase)
    return found[:4]


_RENDER_ESCAPE_START = "<escape>"
_RENDER_ESCAPE_END = "</escape>"


def _render_escape_json(text: str) -> str:
    """Remove only the known ChatGPT render wrapper around a whole JSON body.

    Real network-completed responses sometimes contain a clipped opening
    <escape> tag (e.g. 'escape>' or '>') while retaining its closing tag.
    This is not a generic JSON-substring extractor: unknown prefixes, extra
    suffixes, and incomplete JSON must still fail protocol validation.
    """
    if not text.endswith(_RENDER_ESCAPE_END):
        return text
    before_end = text[:-len(_RENDER_ESCAPE_END)]
    for index in range(len(_RENDER_ESCAPE_START)):
        prefix = _RENDER_ESCAPE_START[index:]
        if before_end.startswith(prefix):
            return before_end[len(prefix):].strip()
    # The strict parser below rejects any text that is not one complete
    # protocol object. A missing opening tag does not make arbitrary text valid.
    return before_end.strip()


def _recover_protocol_prefix(raw: str) -> str:
    """Recover only missing bytes of the fixed bridge identity, never payload data.

    Real TX03 SSE captures sometimes start inside the known transport prefix
    (e.g. 'api-responses-tool-v159","kind":"tool_calls"...'). Reconstructing
    that immutable prefix is safe only if the *whole* resulting document still
    passes strict JSON, schema, literal, and tool allowlist validation.
    """
    fixed = '{"bridge":' + json.dumps(bridge.JSON_BRIDGE_PROTOCOL)
    if raw.startswith("{") or not raw.endswith("}"):
        return raw
    for offset in range(1, len(fixed) - 3):
        if raw.startswith(fixed[offset:]):
            return fixed[:offset] + raw
    return raw


def _bridge_kind_hint(text: str) -> str | None:
    """An anchored correction hint; never itself authorizes a tool call."""
    raw = _recover_protocol_prefix(_render_escape_json(str(text or "").strip()))
    fixed = re.escape('{"bridge":' + json.dumps(bridge.JSON_BRIDGE_PROTOCOL))
    match = re.match(fixed + r'\s*,\s*"kind"\s*:\s*"(tool_calls|final)"', raw)
    return match.group(1) if match else None


def _unique_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResponsesToolBridgeProtocolError("Responses tool-bridge JSON has duplicate keys")
        result[key] = value
    return result


def _strict_envelope(text: str) -> dict[str, Any]:
    raw = _recover_protocol_prefix(_render_escape_json(str(text or "").strip()))
    if raw.startswith("{"):
        # New protocol: one whole JSON document with an explicit in-band
        # identity. This avoids ChatGPT truncating separately generated
        # start/end sentinel lines while preserving strict fail-closed parsing.
        try:
            value = json.loads(raw, object_pairs_hook=_unique_json_keys)
        except (TypeError, ValueError) as error:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT returned an incomplete or invalid Responses tool-bridge JSON object"
            ) from error
        if not isinstance(value, dict) or value.get("bridge") != bridge.JSON_BRIDGE_PROTOCOL:
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge JSON object has an invalid protocol identifier"
            )
        kind = value.get("kind")
        required_keys = (
            {"bridge", "kind", "text"} if kind == "final"
            else {"bridge", "kind", "calls"} if kind == "tool_calls"
            else None
        )
        if required_keys is None or set(value) != required_keys:
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge JSON object has an invalid field set"
            )
    else:
        # One strict parser also supports complete legacy envelopes during a
        # rolling upgrade. A clipped/mixed pair never authorizes a response.
        delimiters = (
            (bridge.BRIDGE_START, bridge.BRIDGE_END),
            (bridge.LEGACY_BRIDGE_START, bridge.LEGACY_BRIDGE_END),
        )
        for start_marker, end_marker in delimiters:
            start = raw.find(start_marker)
            end = raw.find(end_marker, start + len(start_marker)) if start >= 0 else -1
            if start >= 0 and end >= 0:
                break
        else:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT completed without the required Responses tool-bridge envelope"
            )
        before = raw[:start].strip()
        after = raw[end + len(end_marker) :].strip()
        if before or after:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT returned text outside the required Responses tool-bridge envelope"
            )
        payload_text = raw[start + len(start_marker) : end].strip()
        try:
            value = json.loads(payload_text, object_pairs_hook=_unique_json_keys)
        except (TypeError, ValueError) as error:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT returned an incomplete or invalid Responses tool-bridge JSON envelope"
            ) from error
        if not isinstance(value, dict):
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge envelope must contain one JSON object"
            )

    kind = str(value.get("kind") or "").strip()
    if kind == "final":
        if not isinstance(value.get("text"), str):
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge final envelope is missing string field 'text'"
            )
        return value
    if kind == "tool_calls":
        calls = value.get("calls")
        if not isinstance(calls, list) or not calls or not all(isinstance(call, dict) for call in calls):
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge tool_calls envelope contains missing or invalid calls"
            )
        return value
    raise ResponsesToolBridgeProtocolError(
        "Responses tool-bridge envelope has an unsupported kind"
    )

def _interpret(
    text: str,
    catalog: list[dict[str, Any]],
    body: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    # v109 intentionally tolerated arbitrary plain text. That made transport
    # success indistinguishable from protocol success: a truncated literal could
    # be reported as response.completed. v110 requires the contract first, then
    # delegates canonical tool-item validation to v109.
    value = _strict_envelope(text)
    required_tool = _explicit_declared_tool_requirement(body, catalog)
    if required_tool:
        required_name = str(required_tool["name"])
        required_namespace = str(required_tool.get("namespace") or "") or None
        if value["kind"] == "final":
            raise ResponsesToolBridgeProtocolError(
                "Explicitly requested declared tool has not been called: "
                + (required_namespace + "." if required_namespace else "") + required_name
            )
        if not any(
            str(call.get("name") or "") == required_name
            and (str(call.get("namespace") or "") or None) == required_namespace
            for call in value["calls"]
        ):
            raise ResponsesToolBridgeProtocolError(
                "Tool response omitted the explicitly requested declared tool: "
                + (required_namespace + "." if required_namespace else "") + required_name
            )
    if str(value.get("kind") or "") == "final":
        final_text = str(value.get("text") or "")
        missing = [literal for literal in _required_exact_literals(body) if literal not in final_text]
        if missing:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT truncated or changed an explicitly required exact literal"
            )
    # Interpret the *same verified object*, not a second permissive substring
    # scan of the raw page text. Tool allowlisting remains in the v109 adapter.
    return _ORIGINAL_INTERPRET(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")), catalog, body,
    )


def _bridge_prompt(app: Any, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    prompt, catalog = _ORIGINAL_BRIDGE_PROMPT(app, body)
    literals = _required_exact_literals(body)
    required_tool = _explicit_declared_tool_requirement(body, catalog)
    tool_note = ""
    if required_tool:
        qualified = (
            str(required_tool.get("namespace") or "") + "."
            if required_tool.get("namespace") else ""
        ) + str(required_tool["name"])
        tool_note = (
            "\nCURRENT TURN DECLARED TOOL REQUIREMENT: The user explicitly "
            "requested " + qualified + ". It has NOT been executed. "
            "Your next response MUST be kind=tool_calls with that exact "
            "namespace and name and valid arguments from TOOL CATALOG. "
            "Do not claim the tool was executed or return kind=final until "
            "the caller supplies the actual tool result."
        )
    literal_note = ""
    if literals:
        literal_note = "\nExplicit exact literals from caller input (copy whole if required): " + json.dumps(
            literals, ensure_ascii=False, separators=(",", ":")
        )
    # Repeat the transport contract at the *end* of the large Codex prompt. The
    # original contract can be tens of thousands of characters away from the
    # model's generation boundary, which makes exact literals easier to shorten
    # or answer directly. This trailer is deliberately short and generic.
    trailer = f"""FINAL TRANSPORT INTEGRITY CHECK (v159):
Return ONE complete JSON object only, beginning with {{ and ending with }}.
Do not emit sentinel markers, Markdown fences, HTML tags, or text outside JSON.
Include the exact protocol property \"bridge\":\"{bridge.JSON_BRIDGE_PROTOCOL}\".
The only allowed top-level keys are bridge, kind, and text (final) OR bridge, kind, and calls (tool_calls).
When kind=final, copy the final text literally. Preserve identifiers, request IDs, hashes, markers, underscores, suffixes, capitalization, and punctuation byte-for-byte; never abbreviate, normalize, summarize, or truncate them.
If the conversation asks for an exact marker/string, put that complete exact string in the JSON field \"text\".
A partial JSON object or plain-text answer is a transport failure, not a valid final response.{literal_note}{tool_note}"""
    return prompt + "\n\n" + trailer, catalog


def _completed_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
    response = _ORIGINAL_COMPLETED_RESPONSE(*args, **kwargs)
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        response["metadata"] = metadata
    metadata["chat2api_tool_bridge_integrity"] = "v110"
    return response


def install_responses_protocol_integrity_v110_patch() -> None:
    bridge._bridge_prompt = _bridge_prompt
    bridge._interpret = _interpret
    bridge._completed_response = _completed_response


# Imported from app.__init__ so every v109 middleware instance sees the stricter
# globals before handling traffic. Keeping the middleware itself unchanged also
# preserves its existing streaming response.failed / non-streaming 502 handling.
install_responses_protocol_integrity_v110_patch()
