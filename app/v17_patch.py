from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .admin_auth import AdminSessionStore, SESSION_COOKIE
from .pairing import PairingStore


PATCH_VERSION = "0.17.0"


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=500)


class PairingCreate(BaseModel):
    name: str = Field(default="Chrome 扩展", min_length=1, max_length=120)


class EnabledUpdate(BaseModel):
    enabled: bool


def _cookie_secure(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


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


def install_v17_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    api_keys = app.state.api_keys

    legacy_admin_secret = str(settings.api_key or "")
    legacy_pairing_code = str(settings.pairing_code or "")
    internal_admin_secret = "internal-admin-" + secrets.token_urlsafe(48)
    # Historical dependencies read settings.api_key at request time. Give them an
    # unexposed, process-local compatibility token so the external master key is gone.
    settings.api_key = internal_admin_secret
    settings.pairing_code = "internal-pairing-disabled-" + secrets.token_urlsafe(24)

    sessions = AdminSessionStore(
        settings.admin_username,
        settings.admin_password,
        ttl_seconds=max(1, int(settings.admin_session_hours)) * 3600,
    )
    pairings = PairingStore(settings.data_dir)
    app.state.admin_sessions = sessions
    app.state.pairings = pairings
    app.version = PATCH_VERSION

    pairing_loaded = False

    async def ensure_pairings() -> None:
        nonlocal pairing_loaded
        if pairing_loaded:
            return
        await pairings.load()
        await pairings.seed_legacy(legacy_pairing_code)
        pairing_loaded = True

    # Every successful managed API-key authentication sets the routing context used
    # by ClientRegistry.resolve_client(). This automatically covers chat, image,
    # voice and realtime patches without duplicating routing rules in each endpoint.
    if not getattr(api_keys, "_chat2api_v17_routing_wrapped", False):
        base_authenticate = api_keys.authenticate

        async def authenticate_with_route(token: str):
            principal = await base_authenticate(token)
            if principal:
                registry.set_routing_key(principal.key_id)
            return principal

        api_keys.authenticate = authenticate_with_route
        api_keys._chat2api_v17_routing_wrapped = True

    @app.post("/api/admin/auth/login")
    async def admin_login(body: AdminLogin, request: Request) -> Response:
        if not sessions.verify_credentials(body.username, body.password):
            return JSONResponse({"detail": "管理员账号或密码错误"}, status_code=401)
        token = sessions.create()
        response = JSONResponse({"ok": True, "username": settings.admin_username, "version": PATCH_VERSION})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=sessions.ttl_seconds,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/api/admin/auth/session")
    async def admin_session(request: Request) -> dict[str, Any]:
        authenticated = sessions.authenticate(request.cookies.get(SESSION_COOKIE))
        return {
            "authenticated": authenticated,
            "username": settings.admin_username if authenticated else None,
            "version": PATCH_VERSION,
        }

    @app.post("/api/admin/auth/logout")
    async def admin_logout(request: Request) -> Response:
        sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/admin/models")
    async def admin_models() -> dict[str, Any]:
        return {"object": "list", "data": registry.model_catalog(online_only=True)}

    @app.get("/api/admin/extensions")
    async def admin_extensions() -> dict[str, Any]:
        await ensure_pairings()
        clients = registry.summaries()
        by_id = {row["client_id"]: row for row in clients}
        pairing_rows: list[dict[str, Any]] = []
        for row in pairings.list_public():
            client = by_id.get(str(row.get("bound_client_id") or ""))
            if not client:
                state = "unbound" if not row.get("bound_client_id") else "missing"
            elif not client.get("connection_enabled", True):
                state = "disabled"
            elif client.get("online"):
                state = "busy" if client.get("busy") else "online"
            else:
                state = "offline"
            pairing_rows.append({**row, "connection_status": state, "client": client})
        return {
            "pairing_codes": pairing_rows,
            "clients": clients,
            "routing": dict(registry.api_key_routes),
        }

    @app.post("/api/admin/pairing-codes")
    async def create_pairing_code(body: PairingCreate) -> dict[str, Any]:
        await ensure_pairings()
        item, raw = await pairings.create(body.name)
        return {"pairing": item, "code": raw}

    @app.patch("/api/admin/pairing-codes/{pairing_id}")
    async def update_pairing_code(pairing_id: str, body: EnabledUpdate) -> dict[str, Any]:
        await ensure_pairings()
        try:
            item = await pairings.set_enabled(pairing_id, body.enabled)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"pairing": item}

    @app.post("/api/admin/extensions/{client_id}/disconnect")
    async def disconnect_extension(client_id: str) -> dict[str, Any]:
        try:
            item = await registry.set_connection_enabled(client_id, False)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"client_id": client_id, "connection_enabled": item.connection_enabled}

    @app.post("/api/admin/extensions/{client_id}/enable")
    async def enable_extension(client_id: str) -> dict[str, Any]:
        try:
            item = await registry.set_connection_enabled(client_id, True)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"client_id": client_id, "connection_enabled": item.connection_enabled}

    @app.get("/assets/chat2api-v17.js")
    async def admin_v17_js() -> Response:
        path = Path(__file__).with_name("admin_v17.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    def session_ok(request: Request) -> bool:
        return sessions.authenticate(request.cookies.get(SESSION_COOKIE))

    def inject_internal_admin_header(request: Request) -> None:
        headers = [
            (name, value)
            for name, value in request.scope.get("headers", [])
            if name.lower() not in {b"authorization", b"x-api-key"}
        ]
        headers.append((b"authorization", f"Bearer {internal_admin_secret}".encode("utf-8")))
        request.scope["headers"] = headers

    @app.middleware("http")
    async def v17_admin_pairing_and_master_key_removal(request: Request, call_next):
        path = request.url.path

        if path == "/api/extensions/register" and request.method == "POST":
            await ensure_pairings()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"detail": "Invalid registration JSON"}, status_code=400)
            if not isinstance(body, dict):
                return JSONResponse({"detail": "Invalid registration JSON"}, status_code=400)
            raw_code = str(request.headers.get("x-pairing-code") or "")
            device_id = str(body.get("device_id") or (body.get("metadata") or {}).get("device_id") or "").strip()
            if len(device_id) < 8:
                return JSONResponse({"detail": "Extension device_id is required; update the Chrome Bridge"}, status_code=400)
            try:
                pairing = pairings.authorize(raw_code, device_id)
            except PermissionError as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            if not pairing:
                return JSONResponse({"detail": "Invalid or disabled pairing code"}, status_code=401)
            metadata = dict(body.get("metadata") or {})
            metadata["device_id"] = device_id
            metadata["pairing_id"] = pairing.pairing_id
            try:
                if pairing.bound_client_id and pairing.bound_client_id in registry.clients:
                    client_id, token = await registry.rotate_token(
                        pairing.bound_client_id,
                        name=str(body.get("name") or "Chrome"),
                        browser_name=str(body.get("browser_name") or "Chrome"),
                        version=str(body.get("version") or "unknown"),
                        metadata=metadata,
                        device_id=device_id,
                        pairing_id=pairing.pairing_id,
                    )
                else:
                    client_id, token = await registry.register(
                        str(body.get("name") or "Chrome"),
                        str(body.get("browser_name") or "Chrome"),
                        str(body.get("version") or "unknown"),
                        metadata,
                        device_id=device_id,
                        pairing_id=pairing.pairing_id,
                    )
                await pairings.bind(pairing.pairing_id, client_id, device_id)
            except (KeyError, PermissionError) as error:
                return JSONResponse({"detail": str(error)}, status_code=409)
            return JSONResponse({"client_id": client_id, "token": token})

        auth_path = path.startswith("/api/admin/auth/")
        admin_path = path.startswith("/api/admin/") or path == "/api/clients"
        if admin_path and not auth_path:
            if not session_ok(request):
                return JSONResponse({"detail": "Administrator login required"}, status_code=401)
            if "/api/admin/keys/master" in path:
                return JSONResponse({"detail": "Administrator master API key has been removed"}, status_code=404)
            inject_internal_admin_header(request)

        response = await call_next(request)
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type and admin_path and not auth_path:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION if path in {"/api/admin/overview"} else payload.get("version", PATCH_VERSION)
                if path == "/api/admin/overview":
                    if isinstance(payload.get("api_keys"), list):
                        payload["api_keys"] = [row for row in payload["api_keys"] if row.get("key_id") != "master"]
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["admin_account_login"] = True
                        capabilities["administrator_master_api_key_removed"] = True
                        capabilities["managed_extension_pairing"] = True
                        capabilities["sticky_api_key_extension_routing"] = True
                if path == "/api/admin/keys" and isinstance(payload.get("data"), list):
                    payload["data"] = [row for row in payload["data"] if row.get("key_id") != "master"]
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v17.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    # Keep the legacy secret only long enough for ApiKeyStore migration code to use
    # it; it is not accepted by any public endpoint after settings.api_key is rotated.
    app.state.legacy_api_key_for_migration = legacy_admin_secret
    return app
