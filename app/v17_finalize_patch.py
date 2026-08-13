from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.17.0"


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


def install_v17_finalize_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION

    @app.middleware("http")
    async def v17_version_reporting(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        if path not in {"/", "/healthz", "/api/admin/overview"} and not (
            path.startswith("/api/admin/requests/") and path.endswith("/log")
        ):
            return response
        raw = await _response_bytes(response)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return Response(raw, status_code=response.status_code, media_type="application/json")
        if isinstance(payload, dict):
            if path in {"/", "/healthz", "/api/admin/overview"}:
                payload["version"] = PATCH_VERSION
            if path == "/api/admin/overview":
                capabilities = payload.setdefault("capabilities", {})
                if isinstance(capabilities, dict):
                    capabilities.update({
                        "admin_account_login": True,
                        "administrator_master_api_key_removed": True,
                        "managed_extension_pairing": True,
                        "persistent_extension_device_identity": True,
                        "sticky_api_key_extension_routing": True,
                    })
            if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                payload["server_version"] = PATCH_VERSION
        headers = {
            key: value for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store"
        return JSONResponse(payload, status_code=response.status_code, headers=headers)

    return app
