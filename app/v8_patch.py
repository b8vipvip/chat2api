from __future__ import annotations

import io
import json
import secrets
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .api_keys import ApiPrincipal
from .diagnostics import DiagnosticMiddleware, DiagnosticStore, configure_file_logging
from .runtime_contract import SERVER_RUNTIME_VERSION
from .runtime_code_identity import runtime_code_sha
from .telemetry import parse_request_since
from .playground_request_evidence_v207 import request_test_index, playground_diagnostics


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

    def request_probe_evidence(request_id: str) -> dict[str, Any]:
        # One authenticated request log is the canonical diagnostics artifact.
        # Persisted Worker facts are already allowlisted/redacted at ingest;
        # read them in sequence, without making probes a terminal authority.
        store = getattr(app.state, "worker_probes", None)
        if store is None:
            return {"capture_status": "not_collected", "event_count": 0, "events": []}
        try:
            summary = store.summary(request_id)
            return {**summary, "events": store.events(request_id)}
        except (ValueError, OSError, json.JSONDecodeError):
            return {"capture_status": "unavailable", "event_count": 0, "events": []}

    def request_log_report(
        request_id: str, *, row_override: dict[str, Any] | None = None,
        test_index: dict[str, Any] | None = None,
        extension_snapshot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        row = dict(row_override) if row_override is not None else telemetry.get(request_id)
        if not row:
            raise HTTPException(status_code=404, detail="Request record not found")
        trace_id = str(row.get("trace_id") or "")
        if test_index is None:
            test_index = request_test_index(getattr(app.state, "test_runs", None))
        test_run = test_index.get(request_id)
        return {
            "report_type": "chat2api-request-diagnostic",
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server_version": SERVER_RUNTIME_VERSION,
            "request": row,
            "http_trace": diagnostic_store.by_trace(trace_id) if trace_id else [],
            "worker_probe_evidence": request_probe_evidence(request_id),
            "worker_native_dom_evidence": (
                app.state.native_dom_evidence.summary(request_id, str(row.get("api_key_id") or ""))
                if getattr(app.state, "native_dom_evidence", None) else {"available": False}
            ),
            "worker_native_dom_evidence_download": f"/api/admin/requests/{request_id}/native-dom-evidence",
            # Include the private bounded reply tree inside the already
            # authenticated single-request report. Combined ZIP exports reuse
            # this report, so one familiar download now carries the evidence
            # needed for paired original-vs-MV3C comparison.
            "worker_native_dom_reply_tree": (
                app.state.native_dom_evidence.get(request_id, str(row.get("api_key_id") or ""))
                if getattr(app.state, "native_dom_evidence", None)
                and str(request_id).startswith(("req_", "resp_", "imgreq_"))
                else None
            ),
            "playground_diagnostics": playground_diagnostics(test_run),
            "extension_snapshot": extension_snapshot if extension_snapshot is not None else _safe_client_rows(registry),
            "notes": [
                "Stored final prompts and assistant responses ARE included; review and redact before sharing. Authorization credentials and pairing codes are not intentionally exported.",
                "trace_id is available for requests created after server v0.8.0 was deployed.",
                "worker_probe_evidence includes all captured allowlisted facts and timestamps, bounded to 32 events per request; it contains no raw page HTML or pixels.",
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

    @app.get("/api/admin/requests/logs/batch", dependencies=[Depends(require_admin_key)])
    async def download_recent_request_logs(
        since: str | None = Query(default=None, max_length=64),
        current_version_only: bool = Query(default=False),
        q: str | None = Query(default=None, max_length=200),
        status: str | None = Query(default=None, max_length=40),
        model: str | None = Query(default=None, max_length=120),
    ) -> StreamingResponse:
        # Exactly the same retained-row predicate as /api/admin/requests.
        # No timestamp means no time restriction, including version-scoped export.
        # Reject invalid nonblank time rather than silently skipping the filter.
        try:
            cutoff = parse_request_since(since)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        active_commit = runtime_code_sha() if current_version_only else ""
        if current_version_only and not active_commit:
            raise HTTPException(
                status_code=409,
                detail="Current running Git commit is unverified; cannot export by current code revision",
            )
        # Query is the single predicate owner, but admin list middleware
        # intentionally removes prompt and response text. Rehydrate from the
        # original request history BEFORE assembling the export; exporting
        # list-view copies would silently destroy diagnostic evidence.
        filtered_rows = telemetry.query(
            limit=telemetry.items.maxlen or 500, offset=0,
            q=q, status=status, model=model, since=cutoff,
            current_version_only=current_version_only,
            server_version=SERVER_RUNTIME_VERSION,
            server_commit=active_commit,
        )["data"]
        matching_ids = {str(row.get("request_id") or "") for row in filtered_rows}
        selected = [
            row for row in telemetry.recent(telemetry.items.maxlen or 500)
            if str(row.get("request_id") or "") in matching_ids
        ]
        if not selected:
            detail = "No retained request records match the selected filters"
            raise HTTPException(status_code=404, detail=detail)

        test_index = request_test_index(getattr(app.state, "test_runs", None))
        extension_snapshot = _safe_client_rows(registry)
        exported = []
        plain_bytes = 0
        max_plain_bytes = 160 * 1024 * 1024
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for index, row in enumerate(selected, start=1):
                    request_id = str(row.get("request_id") or "")
                    if not request_id:
                        continue
                    # The manifest and each JSON share an immutable row snapshot
                    # from the selection point, even if a live request later ends.
                    report = request_log_report(
                        request_id, row_override=row, test_index=test_index,
                        extension_snapshot=extension_snapshot,
                    )
                    payload = _json_bytes(report)
                    plain_bytes += len(payload)
                    if plain_bytes > max_plain_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Combined request logs exceed 160 MiB; choose a later start time",
                        )
                    safe_id = request_id if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id) else f"record_{index:04d}"
                    filename = f"requests/{index:04d}-chat2api-request-{safe_id}.json"
                    archive.writestr(filename, payload)
                    exported.append({
                        "request_id": request_id,
                        "recorded_at": row.get("recorded_at"),
                        "status": row.get("status"),
                        "request_server_version": row.get("request_server_version"),
                        "request_server_commit": row.get("request_server_commit"),
                        "filename": filename,
                    })

                if not exported:
                    raise HTTPException(status_code=404, detail="No named request records to export")
                manifest = {
                    "report_type": "chat2api-recent-request-log-bundle",
                    "report_version": 3,
                    "selection_mode": "current_version" if current_version_only else ("since" if cutoff and not any((q, status, model)) else "filtered"),
                    "filters": {"q": q or None, "status": status or None, "model": model or None},
                    "current_version_only": current_version_only,
                    "request_version_field": "request_server_commit",
                    "request_commit_field": "request_server_commit",
                    "selected_server_commit": active_commit if current_version_only else None,
                    "selected_server_version": SERVER_RUNTIME_VERSION if current_version_only else None,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "server_version": SERVER_RUNTIME_VERSION,
                    "since": cutoff.isoformat() if cutoff else None,
                    "time_filter_field": "recorded_at" if cutoff else None,
                    "exported_count": len(exported),
                    "retention_note": "Includes all matching currently retained requests (currently up to 500 distinct records); earlier history is not available.",
                    "selection_order": "request-history newest first, by last record update",
                    "single_request_log_route": "/api/admin/requests/{request_id}/log",
                    "contains": "The same per-request JSON, HTTP trace, Worker probes, and Playground diagnostics as single-log downloads.",
                    "privacy_note": "Stored prompts and replies may be included. Review before sharing.",
                    "requests": exported,
                }
                archive.writestr("index.json", _json_bytes(manifest))
                archive.writestr(
                    "README.txt",
                    ("chat2api — 请求日志合并包\n"
                     f"导出数量：{len(exported)} 条（仅限仍留存的请求记录）\n"
                     f"起始时间（含）：{cutoff.isoformat() if cutoff else '不限制时间'}\n"
                     f"其它筛选：搜索={q or '不限'}，状态={status or '不限'}，模型={model or '不限'}\n"
                     f"仅当前运行提交：{'是，'+active_commit if current_version_only else '否'}\n"
                     f"当前正式版本：{SERVER_RUNTIME_VERSION}\n"
                     "历史请求若缺少创建时的可靠 Git SHA 标记，不会被归入当前提交。\n"
                     "requests/ 目录中每条 JSON 对应一次请求，结构与请求记录列表中的“下载日志”一致。\n"
                     "index.json 按页面最新记录顺序给出 ID、状态和文件名；打包只读，不修改请求终态。\n"
                     "注意：日志可能包含完整提示词、生成回复和诊断证据，分享前请检查并脱敏。\n").encode("utf-8"),
                )
            spool.seek(0)
        except BaseException:
            spool.close()
            raise

        def chunks():
            try:
                while data := spool.read(1024 * 1024):
                    yield data
            finally:
                spool.close()

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="chat2api-request-logs-{"current-version-" if current_version_only else ("since-" if cutoff else "filtered-")}{len(exported)}-{stamp}.zip"',
                "Cache-Control": "no-store",
                "X-Chat2API-Exported-Count": str(len(exported)),
                "X-Chat2API-Version-Scope": active_commit if current_version_only else "all",
            },
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
                    "worker_probe_evidence": request_probe_evidence(str(row.get("request_id") or "")),
                }
            )

        summary = {
            "report_type": "chat2api-diagnostic-bundle",
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server_version": SERVER_RUNTIME_VERSION,
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
                "worker_probe_events_included_for_failures": True,
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
