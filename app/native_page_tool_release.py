"""Fixed-source independent release channel for the native page diagnostic.

Only the trusted repository's dedicated release branch is eligible. A tool
release never edits or redeploys the running chat2api server or Worker controller.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time

import httpx

RELEASE_BRANCH = "native-page-diagnostic-releases"
RELEASE_PATH = "scripts/native_page_diagnostic_v1.py"
RELEASE_REPO = "b8vipvip/chat2api"
RELEASE_API = (
    f"https://api.github.com/repos/{RELEASE_REPO}/contents/{RELEASE_PATH}"
    f"?ref={RELEASE_BRANCH}"
)
MAX_TOOL_BYTES = 200_000
CACHE_SECONDS = 90.0
_VERSION = re.compile(rb'(?m)^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')


class DiagnosticReleaseUnavailable(Exception):
    pass


def parse_diagnostic_release(data: dict) -> tuple[bytes, dict]:
    """Verify GitHub's content envelope and artifact before making executable code available."""
    if not isinstance(data, dict) or data.get("type") != "file":
        raise DiagnosticReleaseUnavailable("invalid_release_type")
    if data.get("path") != RELEASE_PATH or data.get("encoding") != "base64":
        raise DiagnosticReleaseUnavailable("unexpected_release_path_or_encoding")
    raw = data.get("content")
    if not isinstance(raw, str) or len(raw) > MAX_TOOL_BYTES * 2:
        raise DiagnosticReleaseUnavailable("invalid_release_content")
    try:
        body = base64.b64decode(raw.replace("\n", ""), validate=True)
    except (ValueError, base64.binascii.Error):
        raise DiagnosticReleaseUnavailable("release_base64_invalid") from None
    if not 0 < len(body) <= MAX_TOOL_BYTES or data.get("size") != len(body):
        raise DiagnosticReleaseUnavailable("release_size_invalid")
    # GitHub's content SHA is a blob object ID. Check it independently;
    # SHA-256 is still recomputed and checked by the Worker before installation.
    blob_sha = hashlib.sha1(
        f"blob {len(body)}\0".encode("ascii") + body
    ).hexdigest()
    if data.get("sha") != blob_sha:
        raise DiagnosticReleaseUnavailable("release_git_blob_mismatch")
    match = _VERSION.search(body)
    if not match:
        raise DiagnosticReleaseUnavailable("release_version_missing")
    try:
        compile(body.decode("utf-8"), RELEASE_PATH, "exec")
    except (SyntaxError, UnicodeError, ValueError):
        raise DiagnosticReleaseUnavailable("release_python_invalid") from None
    return body, {
        "version": match.group(1).decode("ascii"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "source": "github-dedicated-release-branch",
        "release_branch": RELEASE_BRANCH,
        "repository": RELEASE_REPO,
        "git_blob_sha": blob_sha,
    }


class DiagnosticReleaseSource:
    """Thread-safe cache keeps manifest & Python bytes consistent for a click.

    Fetch failure never silently replaces a requested release with the stale
    image-bundled script. The administrator receives an actionable error.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached: tuple[bytes, dict] | None = None
        self._expiry = 0.0

    async def load(self, *, force: bool = False) -> tuple[bytes, dict]:
        async with self._lock:
            if self._cached and not force and time.monotonic() < self._expiry:
                return self._cached
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0), follow_redirects=False
                ) as client:
                    response = await client.get(RELEASE_API, headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "chat2api-native-diagnostic-release",
                        "X-GitHub-Api-Version": "2022-11-28",
                    })
                if response.status_code != 200:
                    raise DiagnosticReleaseUnavailable(
                        f"github_release_http_{response.status_code}")
                if len(response.content) > MAX_TOOL_BYTES * 3:
                    raise DiagnosticReleaseUnavailable("release_envelope_too_large")
                body, manifest = parse_diagnostic_release(response.json())
            except DiagnosticReleaseUnavailable:
                raise
            except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                raise DiagnosticReleaseUnavailable("github_release_unreachable") from None
            self._cached = body, manifest
            self._expiry = time.monotonic() + CACHE_SECONDS
            return self._cached
