from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_REVISION = 105


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


def install_user_console_privacy_v105_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "user_console_privacy_v105_installed", False):
        return app
    app.state.user_console_privacy_v105_installed = True

    @app.middleware("http")
    async def user_console_privacy_boundary(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if not path.startswith("/api/user/") or "application/json" not in content_type or response.status_code < 400:
            return response

        # User-facing surfaces never relay internal routing/runtime failure text.
        # Controlled account and validation messages remain available elsewhere;
        # the playground is the only endpoint that proxies another API boundary.
        if path == "/api/user/playground":
            detail = "账户余额不足，请先充值" if response.status_code == 402 else "测试请求失败，请稍后重试或查看请求记录"
            return JSONResponse({"detail": detail}, status_code=response.status_code, headers={"Cache-Control": "no-store"})

        if response.status_code >= 500:
            return JSONResponse({"detail": "请求暂时无法完成，请稍后重试"}, status_code=response.status_code, headers={"Cache-Control": "no-store"})

        # Preserve explicitly designed 4xx account validation messages. Rebuild
        # the response after consuming it so upstream middleware stays correct.
        raw = await _response_bytes(response)
        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"detail": "请求未完成"}
        headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store"
        return JSONResponse(payload, status_code=response.status_code, headers=headers)

    return app
