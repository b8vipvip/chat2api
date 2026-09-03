from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from .admin_auth import SESSION_COOKIE


PATCH_REVISION = 68
ADMIN_ASSET = "/assets/chat2api-api-key-editor-v68.js"
EDITABLE_SCOPES = ("chat", "models", "files", "images", "audio")
EDIT_ICON_MIRROR_MARKER = "data-chat2api-api-key-edit-icon-mirror-v77"
EDIT_ICON_MIRROR_STYLE = f'''<style {EDIT_ICON_MIRROR_MARKER}="1">
button[data-api-key-edit] {{
  transform: scaleX(-1);
  transform-origin: center;
}}
</style>'''


class ApiKeySettingsUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    scopes: list[str] | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "ApiKeySettingsUpdate":
        if self.name is None and self.scopes is None:
            raise ValueError("At least one API key setting must be supplied")
        if self.name is not None and not self.name.strip():
            raise ValueError("API key name must not be empty")
        if self.scopes is not None:
            normalized: list[str] = []
            seen: set[str] = set()
            for raw in self.scopes:
                scope = str(raw or "").strip().lower()
                if not scope or scope in seen:
                    continue
                if scope not in EDITABLE_SCOPES:
                    raise ValueError(f"Unsupported API key permission: {scope}")
                seen.add(scope)
                normalized.append(scope)
            if not normalized:
                raise ValueError("At least one API key permission must remain enabled")
            self.scopes = normalized
        return self


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_api_key_console_v68_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "api_key_console_v68_installed", False):
        return app
    app.state.api_key_console_v68_installed = True

    store = app.state.api_keys
    sessions = getattr(app.state, "admin_sessions", None)
    settings = app.state.settings

    def admin_ok(request: Request) -> bool:
        if sessions and sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            return True
        supplied = str(request.headers.get("x-api-key") or "").strip()
        authorization = str(request.headers.get("authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        master = str(getattr(settings, "api_key", "") or "")
        return bool(supplied and master and secrets.compare_digest(supplied, master))

    @app.patch("/api/admin/keys/{key_id}/settings")
    async def update_api_key_settings(key_id: str, body: ApiKeySettingsUpdate, request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        async with store.lock:
            item = store.keys.get(str(key_id or ""))
            if not item:
                raise HTTPException(status_code=404, detail="Unknown API key")
            if item.revoked_at:
                raise HTTPException(status_code=409, detail="Revoked API keys cannot be modified")
            if body.name is not None:
                item.name = body.name.strip()[:120]
            if body.scopes is not None:
                item.scopes = list(body.scopes)
            await store.save()
            payload = item.public()
        return {"ok": True, "data": payload, "revision": PATCH_REVISION}

    @app.get(ADMIN_ASSET, include_in_schema=False)
    async def api_key_editor_asset() -> Response:
        path = Path(__file__).with_name("admin_api_key_editor_v68.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def api_key_console_v68(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        if EDIT_ICON_MIRROR_MARKER not in text:
            text = text.replace("</head>", EDIT_ICON_MIRROR_STYLE + "</head>")
        marker = f'<script src="{ADMIN_ASSET}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
