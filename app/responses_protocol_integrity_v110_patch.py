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


def _required_exact_literals(body: dict[str, Any]) -> list[str]:
    """Extract only literals the caller explicitly labels exact in input messages."""
    found: list[str] = []
    for text in _input_texts(body):
        lowered = text.lower()
        for phrase in ("exact marker", "exact string"):
            offset = 0
            while True:
                index = lowered.find(phrase, offset)
                if index < 0:
                    break
                window = text[index : index + 240]
                candidates = _LITERAL_TOKEN.findall(window)
                # Ignore prose-like words; exact smoke/request literals normally
                # contain an underscore and/or a digit. Prefer the longest token.
                candidates = [token for token in candidates if "_" in token or any(ch.isdigit() for ch in token)]
                if candidates:
                    token = max(candidates, key=len)
                    if token not in found:
                        found.append(token)
                offset = index + len(phrase)
    return found[:4]


def _strict_envelope(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    start = raw.find(bridge.BRIDGE_START)
    end = raw.find(bridge.BRIDGE_END, start + len(bridge.BRIDGE_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ResponsesToolBridgeProtocolError(
            "ChatGPT completed without the required Responses tool-bridge envelope"
        )
    before = raw[:start].strip()
    after = raw[end + len(bridge.BRIDGE_END) :].strip()
    if before or after:
        raise ResponsesToolBridgeProtocolError(
            "ChatGPT returned text outside the required Responses tool-bridge envelope"
        )
    payload_text = raw[start + len(bridge.BRIDGE_START) : end].strip()
    try:
        value = json.loads(payload_text)
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
        if not isinstance(calls, list) or not calls:
            raise ResponsesToolBridgeProtocolError(
                "Responses tool-bridge tool_calls envelope contains no calls"
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
    if str(value.get("kind") or "") == "final":
        final_text = str(value.get("text") or "")
        missing = [literal for literal in _required_exact_literals(body) if literal not in final_text]
        if missing:
            raise ResponsesToolBridgeProtocolError(
                "ChatGPT truncated or changed an explicitly required exact literal"
            )
    return _ORIGINAL_INTERPRET(text, catalog, body)


def _bridge_prompt(app: Any, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    prompt, catalog = _ORIGINAL_BRIDGE_PROMPT(app, body)
    literals = _required_exact_literals(body)
    literal_note = ""
    if literals:
        literal_note = "\nExplicit exact literals from caller input (copy whole if required): " + json.dumps(
            literals, ensure_ascii=False, separators=(",", ":")
        )
    # Repeat the transport contract at the *end* of the large Codex prompt. The
    # original contract can be tens of thousands of characters away from the
    # model's generation boundary, which makes exact literals easier to shorten
    # or answer directly. This trailer is deliberately short and generic.
    trailer = f"""FINAL TRANSPORT INTEGRITY CHECK (v110):
Your entire response MUST start with {bridge.BRIDGE_START} and MUST end with {bridge.BRIDGE_END}.
Return exactly one JSON object inside those sentinel lines and no text outside them.
When kind=final, copy the final text literally. Preserve identifiers, request IDs, hashes, markers, underscores, suffixes, capitalization, and punctuation byte-for-byte; never abbreviate, normalize, summarize, or truncate them.
If the conversation asks for an exact marker/string, put that complete exact string in the JSON field \"text\".
A partial marker or a plain-text answer is a transport failure, not a valid final response.{literal_note}"""
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
