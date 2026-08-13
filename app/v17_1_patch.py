from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.17.1"
WEAK_ADMIN_PASSWORDS = {"", "change-me-admin", "replace-with-a-strong-console-password"}


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_v17_1_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    app.version = PATCH_VERSION

    # Re-pairing may rotate a device token, but it must never reverse an explicit
    # administrator disconnect. Only /api/admin/extensions/{id}/enable may do that.
    if not getattr(registry, "_chat2api_v171_rotate_guard", False):
        base_rotate = registry.rotate_token

        async def rotate_token_preserving_admin_disable(client_id: str, **kwargs):
            existing = registry.clients.get(client_id)
            was_enabled = True if existing is None else bool(existing.connection_enabled)
            result = await base_rotate(client_id, **kwargs)
            if not was_enabled:
                await registry.set_connection_enabled(client_id, False)
            return result

        registry.rotate_token = rotate_token_preserving_admin_disable
        registry._chat2api_v171_rotate_guard = True

    @app.get("/assets/chat2api-v17-1.js")
    async def admin_v17_1_js() -> Response:
        path = Path(__file__).with_name("admin_v17_1.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def v17_1_security_and_version(request: Request, call_next):
        path = request.url.path
        if path == "/api/admin/auth/login" and request.method == "POST":
            if str(settings.admin_password or "") in WEAK_ADMIN_PASSWORDS:
                return JSONResponse(
                    {"detail": "管理员密码仍是默认占位值。请先配置 CHAT2API_ADMIN_PASSWORD 为强密码并重启服务。"},
                    status_code=503,
                )

        response = await call_next(request)
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview", "/api/admin/auth/login", "/api/admin/auth/session"}
            or (path.startswith("/api/admin/requests/") and path.endswith("/log"))
        ):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["strong_admin_password_required"] = True
                        capabilities["disabled_device_repair_guard"] = True
                if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v17-1.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
