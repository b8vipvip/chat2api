"""Fail closed on an explicit strict JSON-array output contract.

This is intentionally narrow: generic plain-text completions and multimodal
requests are unaffected. Only a prompt that explicitly demands exactly ten
JSON strings is validated; no content is generated or repaired by the server.
"""
import json
import re


STRICT_JSON_ARRAY = re.compile(r"严格\\s*JSON\\s*数组", re.IGNORECASE)
EXACT_TEN_STRINGS = re.compile(r"恰好\\s*10\\s*个字符串")


def enforce_nonstream_contract(prompt: str, text: str) -> None:
    source = str(prompt or "")
    if not (STRICT_JSON_ARRAY.search(source) and EXACT_TEN_STRINGS.search(source)):
        return
    try:
        parsed = json.loads(str(text or ""))
    except (TypeError, ValueError) as error:
        raise RuntimeError("ChatGPT completed with invalid or incomplete JSON array") from error
    if not isinstance(parsed, list) or len(parsed) != 10 or any(not isinstance(item, str) for item in parsed):
        raise RuntimeError("ChatGPT completed with invalid JSON array: expected exactly 10 strings")
