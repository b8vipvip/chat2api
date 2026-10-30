"""Authoritative Git commit of the code running in this process.

The host checkout and deployment.json are NOT proof of the active runtime.
Hotfixes write an in-container stamp; verified image builds bake the exact SHA.
An unproven runtime returns an empty string and must never be version-scoped.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_RUNTIME_STAMP = Path("/app/.gptauto-runtime-sha")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def runtime_code_sha() -> str:
    """Return the current process's effective, verified-format Git SHA or ''."""
    try:
        if _RUNTIME_STAMP.exists() or _RUNTIME_STAMP.is_symlink():
            if not _RUNTIME_STAMP.is_file():
                return ""
            value = _RUNTIME_STAMP.read_text(encoding="ascii").strip()
            return value if _SHA_PATTERN.fullmatch(value) else ""
    except (OSError, UnicodeError):
        # An unreadable/corrupt stamp must never silently fall back to an
        # older baked image SHA, which would misattribute hotfixed requests.
        return ""

    value = os.environ.get("CHAT2API_BUILD_COMMIT", "").strip()
    return value if _SHA_PATTERN.fullmatch(value) else ""
