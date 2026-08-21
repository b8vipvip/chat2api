from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response


PATCH_VERSION = "0.21.5"


def _active_api_calls(broker: Any, client_id: str) -> int:
    active = getattr(broker, "client_active_requests", {}).get(str(client_id), {})
    return len(active) if isinstance(active, dict) else 0


def install_v21_5_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    broker = app.state.broker
    app.version = PATCH_VERSION

    base_summaries = registry.summaries
    if not getattr(registry, "_chat2api_v215_live_concurrency", False):
        def summaries_with_live_concurrency() -> list[dict[str, Any]]:
            rows = base_summaries()
            runtime = getattr(app.state, "concurrency_config", {})
            limit_for = runtime.get("limit_for") if isinstance(runtime, dict) else None
            default_limit = int(
                runtime.get("default_max_concurrency")
                or runtime.get("max_concurrency")
                or getattr(broker, "max_concurrency", 0)
                or 0
            ) if isinstance(runtime, dict) else int(getattr(broker, "max_concurrency", 0) or 0)
            client_limits = runtime.get("client_limits", {}) if isinstance(runtime, dict) else {}
            if not isinstance(client_limits, dict):
                client_limits = {}

            for row in rows:
                client_id = str(row.get("client_id") or "")
                capacity = row.get("capacity") if isinstance(row.get("capacity"), dict) else {}
                if callable(limit_for):
                    configured_limit = int(limit_for(client_id))
                else:
                    configured_limit = int(capacity.get("limit_units") or default_limit or 0)
                row["active_api_calls"] = _active_api_calls(broker, client_id)
                row["max_concurrency"] = configured_limit
                row["concurrency_limit_source"] = "extension" if client_id in client_limits else "default"
                row["default_max_concurrency"] = default_limit
            return rows

        registry.summaries = summaries_with_live_concurrency
        registry._chat2api_v215_live_concurrency = True

    @app.get("/assets/chat2api-v21-5.js")
    async def admin_v21_5_js() -> Response:
        path = Path(__file__).with_name("admin_v21_5.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v21_5_live_concurrency_console(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path in {"/admin", "/developers"} and "text/html" in content_type:
            body = getattr(response, "body", None)
            if body is None:
                chunks: list[bytes] = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
                body = b"".join(chunks)
            text = bytes(body).decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v21-5.js"></script>'
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
