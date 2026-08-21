#!/usr/bin/env python3
"""One-shot Chrome startup hygiene for a Linux Worker profile.

Chrome's persistent profile can restore dozens of historical ChatGPT windows
before the MV3 service worker has started. This helper runs beside the Chrome
launcher, waits for loopback CDP, keeps one initialization ChatGPT page, and
closes only duplicate ChatGPT page targets. It never touches non-ChatGPT tabs,
extension pages, or later steady-state Worker capacity.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


CHATGPT_HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"}


def _json(debug_url: str, path: str, *, method: str = "GET") -> Any:
    request = Request(f"{debug_url.rstrip('/')}{path}", data=b"" if method != "GET" else None, method=method)
    with urlopen(request, timeout=2.5) as response:
        return json.loads(response.read(1_000_000).decode("utf-8"))


def _chatgpt_page(target: Any) -> bool:
    if not isinstance(target, dict) or target.get("type") != "page":
        return False
    try:
        return urlsplit(str(target.get("url") or "")).hostname in CHATGPT_HOSTS
    except ValueError:
        return False


def _score(target: dict[str, Any]) -> tuple[int, int]:
    """Prefer the normal root page as the stable initialization authority."""
    url = str(target.get("url") or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return (0, 0)
    path = parsed.path.rstrip("/")
    root = int(parsed.hostname in {"chatgpt.com", "www.chatgpt.com"} and path == "")
    login = int(path.startswith("/auth/"))
    return (root, -login)


def _close(debug_url: str, target_id: str) -> bool:
    if not target_id:
        return False
    url = f"{debug_url.rstrip('/')}/json/close/{quote(target_id, safe='')}"
    # Chrome responds to /json/close with plain text rather than JSON. Accept
    # either documented method so this helper also works across CfT revisions.
    for method in ("PUT", "GET"):
        try:
            request = Request(url, data=b"" if method != "GET" else None, method=method)
            with urlopen(request, timeout=2.5) as response:
                response.read(64_000)
            return True
        except (OSError, HTTPError, URLError):
            continue
    return False


def reconcile(debug_url: str, keep: int = 1) -> dict[str, int]:
    targets = _json(debug_url, "/json/list")
    pages = [item for item in targets if _chatgpt_page(item)] if isinstance(targets, list) else []
    if len(pages) <= keep:
        return {"seen": len(pages), "closed": 0, "remaining": len(pages)}

    ordered = sorted(enumerate(pages), key=lambda pair: (_score(pair[1]), -pair[0]), reverse=True)
    keep_ids = {str(item.get("id") or "") for _, item in ordered[:keep]}
    closed = 0
    for item in pages:
        target_id = str(item.get("id") or "")
        if target_id and target_id not in keep_ids and _close(debug_url, target_id):
            closed += 1
    return {"seen": len(pages), "closed": closed, "remaining": max(0, len(pages) - closed)}


def run(debug_url: str, keep: int, wait_seconds: float) -> int:
    deadline = time.monotonic() + max(1.0, wait_seconds)
    total_closed = 0
    max_seen = 0
    stable = 0
    saw_browser = False

    while time.monotonic() < deadline:
        try:
            result = reconcile(debug_url, keep=keep)
            saw_browser = True
        except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
            time.sleep(0.25)
            continue

        max_seen = max(max_seen, result["seen"])
        total_closed += result["closed"]
        if result["remaining"] <= keep:
            stable += 1
        else:
            stable = 0

        # Several stable passes catch late session-restore targets without
        # lingering long enough to interfere with normal warm-pool creation.
        if stable >= 6:
            print(
                f"[chat2api-tab-init] ready seen_max={max_seen} closed={total_closed} keep={keep}",
                flush=True,
            )
            return 0
        time.sleep(0.25)

    if saw_browser:
        print(
            f"[chat2api-tab-init] timeout seen_max={max_seen} closed={total_closed} keep={keep}",
            flush=True,
        )
        return 0
    print("[chat2api-tab-init] CDP unavailable; extension supervisor remains the fallback", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-url", default="http://127.0.0.1:9222")
    parser.add_argument("--keep", type=int, default=1)
    parser.add_argument("--wait", type=float, default=45.0)
    args = parser.parse_args()
    return run(args.debug_url, max(1, min(4, args.keep)), args.wait)


if __name__ == "__main__":
    raise SystemExit(main())