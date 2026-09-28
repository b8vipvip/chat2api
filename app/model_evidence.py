from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .model_catalog import normalize_model_id, normalize_reasoning_level

MODEL_KEYS = {
    "model_slug", "modelslug", "model_id", "modelid", "used_model", "resolved_model",
    "resolved_model_slug", "served_model", "served_model_slug", "used_model_slug",
    "default_model_slug", "default_model", "model",
}
SERVED_MODEL_KEYS = {
    "used_model", "used_model_slug", "resolved_model", "resolved_model_slug",
    "served_model", "served_model_slug",
}
DEFAULT_MODEL_KEYS = {"default_model_slug", "default_model"}
REASONING_KEYS = {
    "reasoning_effort", "reasoningeffort", "reasoning_level", "reasoninglevel",
    "thinking_level", "thinkinglevel", "thinking_effort",
}
SKIPPED_CONTENT_KEYS = {"content", "parts", "text", "prompt", "input", "output_text", "arguments"}
MODEL_HEADERS = ("x-openai-model", "openai-model", "x-gpt-model", "x-model")
REASONING_HEADERS = ("x-openai-reasoning-effort", "x-reasoning-effort", "x-reasoning-level")
MAX_WALK_DEPTH = 14
MAX_BODY_CHARS = 16 * 1024 * 1024


def canonical_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(".", "_")


@dataclass(frozen=True)
class SelectedCandidate:
    value: str | None
    conflict: bool = False
    field: str | None = None


@dataclass(frozen=True)
class ModelEvidence:
    request_model: str | None = None
    served_model: str | None = None
    default_model: str | None = None
    reasoning: str | None = None
    model_conflict: bool = False
    reasoning_conflict: bool = False
    model_field: str | None = None
    default_model_field: str | None = None
    reasoning_field: str | None = None
    evidence_source: str = "unknown"

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _path_score(path: list[str], key: str, kind: str, mode: str) -> int:
    normalized = [canonical_key(part) for part in path]
    metadata = any("metadata" in part or "details" in part or "response" in part for part in normalized)
    if kind == "model":
        if mode == "request":
            return 140 if key == "model" and not path else 0
        if key in SERVED_MODEL_KEYS:
            return 150
        if key in DEFAULT_MODEL_KEYS:
            return 110
        return 0
    return 115 if metadata else 95 if len(path) <= 3 else 0


def _collect(value: Any, candidates: dict[str, list[tuple[str, int, str]]], path: list[str] | None = None, depth: int = 0, mode: str = "response") -> None:
    path = path or []
    if depth > MAX_WALK_DEPTH or value is None:
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _collect(child, candidates, [*path, str(index)], depth + 1, mode)
        return
    if not isinstance(value, dict):
        return
    for raw_key, child in value.items():
        key = canonical_key(raw_key)
        next_path = [*path, str(raw_key)]
        if key in MODEL_KEYS and isinstance(child, str):
            model = normalize_model_id(child)
            score = _path_score(path, key, "model", mode)
            if model and score:
                candidates["model"].append((model, score, ".".join(next_path)))
        if key in REASONING_KEYS and isinstance(child, str):
            reasoning = normalize_reasoning_level(child)
            score = _path_score(path, key, "reasoning", mode)
            if reasoning and score:
                candidates["reasoning"].append((reasoning, score, ".".join(next_path)))
        if key not in SKIPPED_CONTENT_KEYS:
            _collect(child, candidates, next_path, depth + 1, mode)


def _select(candidates: list[tuple[str, int, str]]) -> SelectedCandidate:
    if not candidates:
        return SelectedCandidate(None)
    score = max(item[1] for item in candidates)
    best = [item for item in candidates if item[1] == score]
    values = {item[0] for item in best}
    if len(values) != 1:
        return SelectedCandidate(None, True, None)
    return SelectedCandidate(best[-1][0], False, best[-1][2])


def inspect_objects(values: Iterable[Any], mode: str = "response") -> ModelEvidence:
    candidates: dict[str, list[tuple[str, int, str]]] = {"model": [], "reasoning": []}
    for value in values:
        _collect(value, candidates, mode=mode)
    model = _select(candidates["model"])
    reasoning = _select(candidates["reasoning"])
    defaults = [item for item in candidates["model"] if canonical_key(item[2].split(".")[-1]) in DEFAULT_MODEL_KEYS]
    default = _select(defaults)
    return ModelEvidence(
        request_model=model.value if mode == "request" else None,
        served_model=model.value if mode != "request" and model.field and canonical_key(model.field.split(".")[-1]) in SERVED_MODEL_KEYS else None,
        default_model=default.value,
        reasoning=reasoning.value,
        model_conflict=model.conflict,
        reasoning_conflict=reasoning.conflict,
        model_field=model.field,
        default_model_field=default.field,
        reasoning_field=reasoning.field,
        evidence_source="network_request_metadata" if mode == "request" else "network_response_metadata",
    )


def parse_sse_objects(body: str) -> list[Any]:
    objects: list[Any] = []
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal data_lines
        if not data_lines:
            return
        raw = "\n".join(data_lines).strip()
        data_lines = []
        if not raw or raw == "[DONE]":
            return
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return
        if isinstance(value, (dict, list)):
            objects.append(value)

    for line in str(body or "").splitlines():
        if not line:
            flush()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return objects


def extract_request_evidence(post_data: str | bytes | None) -> ModelEvidence:
    try:
        if isinstance(post_data, bytes):
            post_data = post_data.decode("utf-8")
        parsed = json.loads(post_data or "{}")
    except (UnicodeDecodeError, TypeError, ValueError):
        parsed = {}
    return inspect_objects([parsed] if isinstance(parsed, dict) else [], "request")


def extract_response_evidence(body: str = "", headers: dict[str, str] | None = None, mime_type: str = "") -> ModelEvidence:
    normalized_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items() if isinstance(v, str)}
    header_model = next((normalize_model_id(normalized_headers.get(name)) for name in MODEL_HEADERS if normalize_model_id(normalized_headers.get(name))), None)
    header_reasoning = next((normalize_reasoning_level(normalized_headers.get(name)) for name in REASONING_HEADERS if normalize_reasoning_level(normalized_headers.get(name))), None)
    values: list[Any] = []
    text = str(body or "")
    if len(text) <= MAX_BODY_CHARS:
        try:
            parsed = json.loads(text.strip())
            if isinstance(parsed, (dict, list)):
                values.append(parsed)
        except (TypeError, ValueError):
            pass
        if "event-stream" in str(mime_type).lower() or "\ndata:" in "\n" + text:
            values.extend(parse_sse_objects(text))
    body_evidence = inspect_objects(values, "response")
    conflict = bool(header_model and body_evidence.served_model and header_model != body_evidence.served_model) or body_evidence.model_conflict
    reasoning_conflict = bool(header_reasoning and body_evidence.reasoning and header_reasoning != body_evidence.reasoning) or body_evidence.reasoning_conflict
    return ModelEvidence(
        served_model=None if conflict else header_model or body_evidence.served_model,
        default_model=body_evidence.default_model,
        reasoning=None if reasoning_conflict else header_reasoning or body_evidence.reasoning,
        model_conflict=conflict,
        reasoning_conflict=reasoning_conflict,
        model_field="response-header" if header_model and not conflict else body_evidence.model_field,
        default_model_field=body_evidence.default_model_field,
        reasoning_field="response-header" if header_reasoning and not reasoning_conflict else body_evidence.reasoning_field,
        evidence_source="network_response_metadata",
    )
