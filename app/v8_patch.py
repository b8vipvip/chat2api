from __future__ import annotations

import io
import json
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from .api_keys import ApiPrincipal
from .diagnostics import DiagnosticMiddleware, DiagnosticStore, configure_file_logging


PATCH_VERSION = "0.8.0"


def _supplied_token(authorization: str | None, x_api_key: str | None) -> str:
    supplied = (x_api_key or "").strip()
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return supplied


def _master_principal() -> ApiPrincipal:
    return ApiPrincipal(
        key_id="master",
        name="CHAT2API_API_KEY",
        kind="master",
        scopes=("admin", "chat", "models", "files", "images", "audio"),
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _safe_client_rows(registry) -> list[dict[str, Any]]:
    rows = []
    for row in registry.summaries():
        if hasattr(row, "model_dump"):
            row = row.model_dump()
        elif not isinstance(row, dict):
            row = dict(row)
        clean = dict(row)
        for key in ("token", "secret", "pairing_code", "api_key"):
            clean.pop(key, None)
        rows.append(clean)
    return rows


def install_v8_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    telemetry = app.state.telemetry
    api_keys = app.state.api_keys
    diagnostic_store = DiagnosticStore(settings.data_dir)
    log_path = configure_file_logging(settings.data_dir)
    app.state.diagnostic_store = diagnostic_store
    app.version = PATCH_VERSION
    app.add_middleware(DiagnosticMiddleware, store=diagnostic_store)

    async def require_admin_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> ApiPrincipal:
        supplied = _supplied_token(authorization, x_api_key)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing administrator API key")
        if settings.api_key and secrets.compare_digest(supplied, settings.api_key):
            return _master_principal()
        if await api_keys.authenticate(supplied):
            raise HTTPException(status_code=403, detail="Managed API keys cannot access administrator endpoints")
        raise HTTPException(status_code=401, detail="Invalid administrator API key")

    def request_log_report(request_id: str) -> dict[str, Any]:
        row = telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        trace_id = str(row.get("trace_id") or "")
        return {
            "report_type": "chat2api-request-diagnostic",
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server_version": PATCH_VERSION,
            "request": row,
            "http_trace": diagnostic_store.by_trace(trace_id) if trace_id else [],
            "extension_snapshot": _safe_client_rows(registry),
            "notes": [
                "API keys, Authorization headers, pairing codes, prompt bodies and base64 payloads are not included.",
                "trace_id is available for requests created after server v0.8.0 was deployed.",
            ],
        }

    @app.get("/api/admin/diagnostics/events", dependencies=[Depends(require_admin_key)])
    async def diagnostic_events(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
        return {"data": diagnostic_store.recent(limit), "count": min(limit, len(diagnostic_store.items))}

    @app.get("/api/admin/requests/{request_id}/log", dependencies=[Depends(require_admin_key)])
    async def download_request_log(request_id: str) -> Response:
        report = request_log_report(request_id)
        filename = f"chat2api-request-{request_id}.json"
        return Response(
            _json_bytes(report),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    @app.get("/api/admin/diagnostics/export", dependencies=[Depends(require_admin_key)])
    async def export_diagnostics(limit: int = Query(default=200, ge=1, le=500)) -> Response:
        requests = telemetry.recent(limit)
        events = diagnostic_store.recent(min(1000, max(limit * 3, 200)))
        failures = []
        for row in requests:
            if row.get("status") != "error":
                continue
            trace_id = str(row.get("trace_id") or "")
            failures.append(
                {
                    "request": row,
                    "http_trace": diagnostic_store.by_trace(trace_id) if trace_id else [],
                }
            )

        summary = {
            "report_type": "chat2api-diagnostic-bundle",
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server_version": PATCH_VERSION,
            "public_url": settings.public_url or None,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "allowed_origins": settings.origins,
            "online_extensions": len(registry.online_client_ids()),
            "telemetry": telemetry.summary(),
            "retained_http_events": len(diagnostic_store.items),
            "exported_requests": len(requests),
            "exported_http_events": len(events),
            "exported_failures": len(failures),
            "privacy": {
                "api_keys_included": False,
                "authorization_headers_included": False,
                "pairing_code_included": False,
                "prompt_bodies_included": False,
                "base64_payloads_included": False,
            },
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.json", _json_bytes(summary))
            archive.writestr("requests.json", _json_bytes(requests))
            archive.writestr("server_events.json", _json_bytes(events))
            archive.writestr("failures.json", _json_bytes(failures))
            archive.writestr("extensions.json", _json_bytes(_safe_client_rows(registry)))
            archive.writestr("models.json", _json_bytes(registry.model_catalog(online_only=True)))
            archive.writestr(
                "README.txt",
                (
                    "chat2api diagnostic bundle\n"
                    "==========================\n"
                    "This archive is intended for troubleshooting API failures.\n"
                    "It contains structured request telemetry, sanitized HTTP traces, extension/model snapshots and server application logs.\n"
                    "It intentionally excludes API keys, Authorization headers, pairing codes, prompt bodies and base64 file/audio/image payloads.\n"
                    "For a single failure, the request list also provides a smaller per-request JSON log download.\n"
                ).encode("utf-8"),
            )
            for candidate in [log_path, *[Path(str(log_path) + f".{index}") for index in range(1, 4)]]:
                if candidate.exists() and candidate.is_file():
                    try:
                        archive.write(candidate, arcname=candidate.name)
                    except OSError:
                        pass

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="chat2api-diagnostics-{stamp}.zip"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/assets/chat2api-v8.js")
    async def admin_v8_js() -> Response:
        path = Path(__file__).with_name("admin_v8.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    async def response_bytes(response) -> bytes:
        body = getattr(response, "body", None)
        if body is not None:
            return bytes(body)
        chunks: list[bytes] = []
        iterator = getattr(response, "body_iterator", None)
        if iterator is not None:
            async for chunk in iterator:
                chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
        return b"".join(chunks)

    @app.middleware("http")
    async def v8_console_and_version(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/healthz", "/api/admin/overview"} and "application/json" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["diagnostic_export"] = True
                        capabilities["request_trace"] = True
            return JSONResponse(payload, status_code=response.status_code, headers={"Cache-Control": "no-store"})

        if path in {"/admin", "/developers"} and "text/html" in response.headers.get("content-type", ""):
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v8.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            return Response(text, status_code=response.status_code, media_type="text/html", headers={"Cache-Control": "no-store"})
        return response

    return app
