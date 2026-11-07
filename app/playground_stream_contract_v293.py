"""Content-free Playground transport attribution, not a stream admission gate.

An ABCD run can use browser SSE with no final ABCD report if browser connection
breaks. Never infer ordinary manual chat from report absence. Attribution is
best-effort and never changes Worker dispatch or request terminal authority.
"""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

ABCD_TRANSPORT = "abcd-real-sse-v293"
DETACHED_TRANSPORT = "server-detached-v292"


def _same_origin_admin(headers: Any) -> bool:
    try:
        origin, referer = str(headers.get("origin") or ""), str(headers.get("referer") or "")
        o, r = urlsplit(origin), urlsplit(referer)
        return (bool(origin and referer) and o.scheme in {"http", "https"} and bool(o.netloc)
                and not (o.path or o.query or o.fragment) and
                r.scheme == o.scheme and r.netloc == o.netloc and
                r.path in {"/admin", "/developers"})
    except (ValueError, AttributeError, TypeError):
        return False


def playground_stream_attribution(
    *, request_id: str, stream: bool, headers: Any, test_runs: Any,
) -> dict[str, Any] | None:
    """Classify only a matching persisted run. A missing ABCD report is NOT a type marker."""
    if not request_id.startswith("req_"):
        return None
    contract = str(headers.get("x-chat2api-playground-transport") or "")
    dispatch = str(headers.get("x-chat2api-playground-dispatch") or "")
    if not _same_origin_admin(headers) and dispatch != DETACHED_TRANSPORT:
        return None
    run_id = "chatrun_" + uuid.uuid5(
        uuid.NAMESPACE_URL, f"chat2api-playground-chat:{request_id}"
    ).hex
    run = test_runs.get(run_id) if test_runs is not None else None
    if not isinstance(run, dict) or request_id not in (run.get("request_ids") or []):
        return None
    quality = run.get("quality") if isinstance(run.get("quality"), dict) else {}
    scene = str(quality.get("abcd_scene") or run.get("abcd_scene") or "")
    if scene not in {"A", "B", "C", "D"}:
        scene = ""
    intent = str(quality.get("transport_contract") or "")
    if dispatch == DETACHED_TRANSPORT and not stream:
        kind = "manual-server-detached"
    elif contract == ABCD_TRANSPORT and stream:
        kind = "abcd-real-sse"
    elif scene and stream:
        kind = "abcd-scene-declared-sse"
    elif stream:
        kind = "unverified-browser-direct-sse"
    else:
        kind = "unverified-browser-nonstream"
    return {
        "kind": kind,
        "stream": bool(stream),
        "scene": scene or None,
        "declared_contract": intent[:80] or None,
        "request_header_contract": contract if contract == ABCD_TRANSPORT else None,
        "internal_dispatch": dispatch == DETACHED_TRANSPORT,
        "source": "matched-playground-run-v293",
    }
