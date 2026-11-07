"""Guard obsolete *manual* admin browser SSE without breaking real ABCD SSE.

At v0.22.291 newer manual Playground chat uses a short, authenticated 202
launch and an in-process nonstream API request. Real machines can keep old JS
running in an already-open admin tab, still posting stream=true under the same
TestRunStore v3 schema. A stale browser transport can close 5-11 seconds into
a 180-second request. The Worker completing afterward does not prove that
browser received its API response.

A verified browser-origin Playground run *with no explicit new ABCD transport
contract* must refresh rather than dispatch another expensive, doomed stream.
This is a version-safety guard, NOT an SSE or reverse-proxy repair.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from typing import Any

ABCD_TRANSPORT = "abcd-real-sse-v293"


def _is_admin_browser_request(headers: Any) -> bool:
    """Require a real same-origin admin page route; never trust Referer alone."""
    try:
        origin = str(headers.get("origin") or "")
        referer = str(headers.get("referer") or "")
        if not origin or not referer:
            return False
        o, r = urlsplit(origin), urlsplit(referer)
        if not o.scheme in {"http", "https"} or not o.netloc or o.path or o.query or o.fragment:
            return False
        if r.scheme != o.scheme or r.netloc != o.netloc:
            return False
        return r.path in {"/admin", "/developers"}
    except (ValueError, AttributeError, TypeError):
        return False


def is_legacy_manual_playground_sse(
    *,
    request_id: str,
    stream: bool,
    headers: Any,
    test_runs: Any,
) -> bool:
    if not stream or not request_id.startswith("req_"):
        return False
    if not _is_admin_browser_request(headers):
        return False
    # Only current, explicitly registered Playground runs are in scope.
    # Never block external API callers or uncertain/expired legacy records.
    import uuid
    run_id = "chatrun_" + uuid.uuid5(
        uuid.NAMESPACE_URL, f"chat2api-playground-chat:{request_id}"
    ).hex
    run = test_runs.get(run_id) if test_runs is not None else None
    if not isinstance(run, dict) or request_id not in (run.get("request_ids") or []):
        return False
    # Dedicated ABCD real-SSE test deliberately owns the browser stream.
    # A current v293 test declares that intent explicitly.
    return str(headers.get("x-chat2api-playground-transport") or "") != ABCD_TRANSPORT
