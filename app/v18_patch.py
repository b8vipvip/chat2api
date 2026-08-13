from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.18.0"


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


def install_v18_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    pairings = app.state.pairings
    app.version = PATCH_VERSION

    @app.get("/api/admin/pairing-codes/{pairing_id}/secret")
    async def reveal_pairing_code(pairing_id: str) -> dict[str, Any]:
        try:
            code, rotated = await pairings.reveal_or_rotate(pairing_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"pairing_id": pairing_id, "code": code, "rotated": rotated}

    @app.delete("/api/admin/pairing-codes/{pairing_id}")
    async def delete_pairing_code(pairing_id: str) -> dict[str, Any]:
        try:
            item = await pairings.delete(pairing_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"deleted": True, "pairing": item}

    @app.delete("/api/admin/extensions/{client_id}")
    async def delete_extension_history(client_id: str) -> dict[str, Any]:
        try:
            item = await registry.delete_client(client_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"deleted": True, "client_id": item.client_id}

    @app.get("/assets/chat2api-v18.js")
    async def admin_v18_js() -> Response:
        path = Path(__file__).with_name("admin_v18.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v18_extension_management_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        should_rewrite_json = (
            "application/json" in content_type
            and (
                path in {"/", "/healthz"}
                or path.startswith("/api/admin/")
                or (path.startswith("/api/admin/requests/") and path.endswith("/log"))
            )
        )
        if should_rewrite_json:
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
                        capabilities["pairing_code_copy_and_delete"] = True
                        capabilities["extension_history_delete"] = True
                        capabilities["pairing_status_separated_from_online_status"] = True
                elif path == "/api/admin/extensions":
                    pairing_rows = payload.get("pairing_codes")
                    if isinstance(pairing_rows, list):
                        for row in pairing_rows:
                            if not isinstance(row, dict):
                                continue
                            row["pairing_status"] = "paired" if row.get("bound_client_id") or row.get("last_paired_at") else "unpaired"
                            row.pop("connection_status", None)
                    client_rows = payload.get("clients")
                    if isinstance(client_rows, list):
                        for row in client_rows:
                            if not isinstance(row, dict):
                                continue
                            row["status"] = "online" if bool(row.get("online")) else "offline"
                if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION

            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v18.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
