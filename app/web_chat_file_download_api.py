"""W-05: authenticated download boundary for user-owned chat files."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response

from .admin_auth import same_origin_admin_request
from .file_store import safe_name
from .user_commerce import USER_SESSION_COOKIE


_FILE_ID_RE = re.compile(r"^file_[A-Za-z0-9_-]{8,100}$")
_GENERATED_FILE_ID_RE = re.compile(r"^gen_[A-Za-z0-9]{8,64}$")
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

    @app.get("/api/user/chat/generated-files/{file_id}/download")
    async def download_owned_generated_file(file_id: str, request: Request) -> Response:
        accounts = getattr(request.app.state, "user_accounts", None)
        if accounts is None:
            _http_error(503, "user account service unavailable")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            _http_error(401, "user login required")
        if not _GENERATED_FILE_ID_RE.fullmatch(file_id):
            _http_error(404, "file not found")
        archive = getattr(request.app.state, "generated_files", None)
        if archive is None:
            _http_error(503, "generated file archive unavailable")
        try:
            payload, _mime, filename = await asyncio.to_thread(
                archive.read_owned, file_id, accounts.key_ids(str(user["user_id"])),
            )
        except (KeyError, FileNotFoundError):
            _http_error(404, "file not found")
        except (ValueError, OSError):
            _http_error(503, "file content unavailable")
        filename = safe_name(str(filename or "download.bin"))
        ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "download.bin"
        return Response(payload, media_type="application/octet-stream", headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
        })

    @app.delete("/api/user/chat/generated-files/{file_id}")
    async def delete_owned_generated_file(file_id: str, request: Request) -> Response:
        if not same_origin_admin_request(request):
            _http_error(403, "same-origin request required")
        accounts = getattr(request.app.state, "user_accounts", None)
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE)) if accounts else None
        if not user:
            _http_error(401, "user login required")
        user_id = str(user["user_id"])
        expected = request.headers.get("X-Chat2API-User-ID")
        if expected is not None and expected != user_id:
            _http_error(409, "Web Chat account changed; reload the page")
        if user.get("is_guest"):
            _http_error(403, "authenticated account required")
        if not _GENERATED_FILE_ID_RE.fullmatch(file_id):
            _http_error(404, "file not found")
        archive = getattr(request.app.state, "generated_files", None)
        conversations = getattr(request.app.state, "web_chat_conversations", None)
        if archive is None or conversations is None:
            _http_error(503, "generated file reference service unavailable")

        def guarded_delete():
            identity = archive.deletion_identity(file_id)
            if not identity or not accounts.owns_key(user_id, identity[0]):
                _http_error(404, "file not found")
            owner_key, request_id = identity
            with conversations.generated_deletion_guard(user_id, file_id, request_id) as allowed:
                if not allowed:
                    _http_error(409, "生成文件仍被已保存的会话引用")
                # A newly generated file may not have been saved to the
                # conversation yet. The server, not the browser, enforces age.
                try:
                    return archive.delete(file_id, owner=owner_key,
                        cutoff=datetime.now(timezone.utc) - timedelta(hours=24))
                except PermissionError:
                    _http_error(409, "生成文件尚在 24 小时保护期内")

        try:
            deleted = await asyncio.to_thread(guarded_delete)
        except (ValueError, OSError):
            _http_error(503, "file content unavailable")
        if not deleted:
            _http_error(404, "file not found")
        return Response('{"deleted":true}', media_type="application/json", headers=_PRIVATE_HEADERS)

    return app
