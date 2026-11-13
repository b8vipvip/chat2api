"""W-05: authenticated download boundary for user-owned chat files."""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response

from .file_store import safe_name
from .user_commerce import USER_SESSION_COOKIE


_FILE_ID_RE = re.compile(r"^file_[A-Za-z0-9_-]{8,100}$")
_MIME_RE = re.compile(r"^[A-Za-z0-9.+-]{1,100}/[A-Za-z0-9.+-]{1,100}$")
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _http_error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail, headers=_PRIVATE_HEADERS)


def install_web_chat_file_download_api(app: FastAPI) -> FastAPI:
    if getattr(app.state, "web_chat_file_download_api_installed", False):
        return app
    app.state.web_chat_file_download_api_installed = True

    @app.get("/api/user/chat/files/{file_id}/download")
    async def download_chat_file(file_id: str, request: Request) -> Response:
        accounts = getattr(request.app.state, "user_accounts", None)
        if accounts is None:
            _http_error(503, "user account service unavailable")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            _http_error(401, "user login required")

        if not _FILE_ID_RE.fullmatch(file_id):
            _http_error(404, "file not found")
        file_store = getattr(request.app.state, "file_store", None)
        if file_store is None:
            _http_error(503, "file service unavailable")
        item = file_store.get(file_id)
        if (
            item is None
            or not str(item.owner_key_id or "")
            or not accounts.owns_key(str(user["user_id"]), str(item.owner_key_id))
        ):
            # Conceal the existence of files belonging to other accounts.
            _http_error(404, "file not found")

        try:
            stored, payload = file_store.read(file_id)
        except (KeyError, FileNotFoundError):
            _http_error(404, "file content not found")
        except OSError:
            # Do not leak filesystem details; keep even storage failures
            # private and non-cacheable for this session-scoped resource.
            _http_error(503, "file content unavailable")

        filename = safe_name(str(stored.filename or "download.bin"))
        ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download.bin"
        content_type = str(stored.mime_type or "")
        if not _MIME_RE.fullmatch(content_type):
            content_type = "application/octet-stream"
        disposition = (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
        return Response(
            payload,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Vary": "Cookie",
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )

    return app
