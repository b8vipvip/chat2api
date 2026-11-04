"""Verified, independent Windows DOM collector release.

Only the trusted repository's dedicated diagnostic release branch is eligible.
Collector JavaScript is executed through Chrome's documented userScripts API,
never eval/importScripts or injected into the MV3 background service worker.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import time

import httpx

RELEASE_REPO = "b8vipvip/chat2api"
RELEASE_BRANCH = "native-page-diagnostic-releases"
RELEASE_PATH = "scripts/windows_native_page_collector_v1.js"
RELEASE_API = (
    f"https://api.github.com/repos/{RELEASE_REPO}/contents/{RELEASE_PATH}"
    f"?ref={RELEASE_BRANCH}"
)
# Public fixed GitHub raw endpoint does not consume the unauthenticated REST
# API quota. Never accept caller-supplied URLs, redirects or arbitrary hosts.
RELEASE_RAW = (
    f"https://raw.githubusercontent.com/{RELEASE_REPO}/{RELEASE_BRANCH}/{RELEASE_PATH}"
)
MAX_SOURCE_BYTES = 120_000
CACHE_SECONDS = 90.0
VERSION = re.compile(rb'\bconst DIAGNOSTIC_TOOL_VERSION="([0-9]+\.[0-9]+\.[0-9]+)";')


class WindowsCollectorReleaseUnavailable(Exception):
    pass


def parse_windows_collector_release(data: dict) -> tuple[str, dict]:
    if (not isinstance(data, dict) or data.get("type") != "file"
        or data.get("path") != RELEASE_PATH
        or data.get("encoding") != "base64"):
        raise WindowsCollectorReleaseUnavailable("windows_release_envelope_invalid")
    encoded = data.get("content")
    if not isinstance(encoded, str) or len(encoded) > MAX_SOURCE_BYTES * 2:
        raise WindowsCollectorReleaseUnavailable("windows_release_content_invalid")
    try:
        body = base64.b64decode(encoded.replace("\n", ""), validate=True)
    except (ValueError, binascii.Error):
        raise WindowsCollectorReleaseUnavailable("windows_release_base64_invalid") from None
    if (not 0 < len(body) <= MAX_SOURCE_BYTES
        or type(data.get("size")) is not int or data["size"] != len(body)):
        raise WindowsCollectorReleaseUnavailable("windows_release_size_invalid")
    git_blob = hashlib.sha1(f"blob {len(body)}\0".encode("ascii") + body).hexdigest()
    if data.get("sha") != git_blob:
        raise WindowsCollectorReleaseUnavailable("windows_release_blob_sha_invalid")
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise WindowsCollectorReleaseUnavailable("windows_release_utf8_invalid") from None
    version = VERSION.search(body)
    if not version or not source.rstrip().endswith("})()"):
        raise WindowsCollectorReleaseUnavailable("windows_release_script_invalid")
    return source, {
        "version": version.group(1).decode("ascii"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "source": "github-dedicated-release-branch",
        "release_branch": RELEASE_BRANCH,
        "repository": RELEASE_REPO,
        "git_blob_sha": git_blob,
        "execution": "chrome.userScripts.execute/USER_SCRIPT",
        "capability": "windows-dom-independent-v1",
    }


def parse_windows_collector_raw(body: bytes) -> tuple[str, dict]:
    """Parse a public, fixed-path GitHub raw response without API-envelope data.

    A raw response cannot cryptographically prove the branch ref itself. It is
    fetched only from the fixed GitHub HTTPS host and path and still verified
    with identical source/version/length rules and computed content digests.
    """
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_SOURCE_BYTES:
        raise WindowsCollectorReleaseUnavailable("windows_release_raw_size_invalid")
    blob = hashlib.sha1(f"blob {len(body)}\0".encode("ascii") + body).hexdigest()
    envelope = {
        "type": "file", "path": RELEASE_PATH,
        "encoding": "base64", "size": len(body), "sha": blob,
        "content": base64.b64encode(body).decode("ascii"),
    }
    source, manifest = parse_windows_collector_release(envelope)
    manifest["source"] = "github-raw-fixed-release-branch"
    return source, manifest


class WindowsCollectorReleaseSource:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached: tuple[str, dict] | None = None
        self._expiry = 0.0
        self._api_cooldown_until = 0.0

    async def load(self, *, force: bool = False) -> tuple[str, dict]:
        async with self._lock:
            if self._cached and not force and time.monotonic() < self._expiry:
                return self._cached
            # A 403 might be a REST rate limit, secondary throttle or network
            # policy. Prefer the fixed GitHub raw file during cooldown instead
            # of retrying a known-blocked REST endpoint on every admin click.
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0), follow_redirects=False
                ) as client:
                    result = None
                    api_problem = ""
                    if time.monotonic() >= self._api_cooldown_until:
                        try:
                            response = await client.get(RELEASE_API, headers={
                                "Accept": "application/vnd.github+json",
                                "User-Agent": "chat2api-windows-diagnostic-release",
                                "X-GitHub-Api-Version": "2022-11-28",
                            })
                            if response.status_code == 200:
                                if len(response.content) > MAX_SOURCE_BYTES * 3:
                                    raise WindowsCollectorReleaseUnavailable(
                                        "windows_release_envelope_too_large")
                                # Do not downgrade an invalid GitHub API
                                # envelope to raw: it could mask tampering.
                                result = parse_windows_collector_release(response.json())
                            elif response.status_code in (403, 429):
                                api_problem = f"github_windows_release_http_{response.status_code}"
                                self._api_cooldown_until = time.monotonic() + 300
                            else:
                                raise WindowsCollectorReleaseUnavailable(
                                    f"github_windows_release_http_{response.status_code}")
                        except httpx.HTTPError:
                            api_problem = "github_windows_release_api_unreachable"
                            self._api_cooldown_until = time.monotonic() + 60
                    if result is None:
                        raw = await client.get(RELEASE_RAW, headers={
                            "Accept": "text/plain",
                            "User-Agent": "chat2api-windows-diagnostic-release",
                        })
                        if raw.status_code != 200:
                            raise WindowsCollectorReleaseUnavailable(
                                f"{api_problem or 'github_windows_release_api_throttled'}"
                                f";github_windows_raw_http_{raw.status_code}")
                        result = parse_windows_collector_raw(raw.content)
                        if api_problem:
                            result[1]["api_fallback_reason"] = api_problem
            except WindowsCollectorReleaseUnavailable:
                raise
            except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                raise WindowsCollectorReleaseUnavailable(
                    "github_windows_release_unreachable") from None
            self._cached = result
            self._expiry = time.monotonic() + CACHE_SECONDS
            return result
