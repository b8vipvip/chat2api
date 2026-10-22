"""Select the public media origin without trusting arbitrary forwarded hosts.

An HTTPS console may reach the ASGI app over HTTP through a reverse proxy.
Use a browser Origin only when it is a clean HTTPS origin for the *same*
Host already accepted by ASGI. Explicit configured public_base_url still wins
inside MediaArchive, and machine-to-machine callers with no Origin keep the
ordinary request-base behavior.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def effective_media_base_url(base_url: str, origin: str | None) -> str:
    base = str(base_url or "").strip().rstrip("/")
    try:
        source = urlsplit(base)
        candidate = urlsplit(str(origin or "").strip())
        if source.scheme != "http" or candidate.scheme != "https":
            return base
        if (not source.hostname or candidate.hostname != source.hostname
            or source.username or source.password or candidate.username or candidate.password
            or candidate.path not in ("", "/") or candidate.query or candidate.fragment):
            return base
        # Only standard proxy-terminated HTTPS origins, not arbitrary ports.
        # A nonstandard LAN deployment keeps its original explicit URL.
        if source.port not in (None, 80) or candidate.port not in (None, 443):
            return base
        return "https://" + candidate.netloc.lower()
    except ValueError:
        return base


def media_base_for_request(request) -> str:
    return effective_media_base_url(str(request.base_url), request.headers.get("origin"))
