from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE


PATCH_REVISION = 121
ASSET_PATH = "/assets/chat2api-worker-limits-clipboard-v121.js"
WINDOW_CONFIG_FILENAME = "worker_window_limits.json"
MIN_LIMIT = 1
MAX_LIMIT = 32
MAX_CLIPBOARD_CHARS = 16_384
ROUTED_REQUEST_TYPES = {"chat.request", "image.request", "voice.request", "voice.live.start"}
LOGIN_TICKET_HEADER = "x-chat2api-login-ticket"
CONTROL_RESULT_KEY = "extension_control_result"


class WindowLimitUpdate(BaseModel):
    max_windows: int = Field(ge=MIN_LIMIT, le=MAX_LIMIT)


def _normalize_limit(value: Any, fallback: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(MIN_LIMIT, min(MAX_LIMIT, parsed))


def _load_window_limits(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    raw = payload.get("clients") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for client_id, value in raw.items():
        clean = str(client_id or "").strip()
        if clean:
            result[clean] = _normalize_limit(value)
    return result


def _save_window_limits(path: Path, limits: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "per-extension-routed-window-limit",
                "clients": {
                    str(client_id): int(value)
                    for client_id, value in sorted(limits.items())
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


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


def install_worker_limits_clipboard_v121_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "worker_limits_clipboard_v121_installed", False):
        return app
    app.state.worker_limits_clipboard_v121_installed = True

    registry = app.state.registry
    settings = app.state.settings
    sessions = app.state.admin_sessions
    linux_workers = app.state.linux_workers
    login_sessions = app.state.worker_login_sessions
    send_worker_command = app.state.send_linux_worker_command
    config_path = Path(settings.data_dir) / WINDOW_CONFIG_FILENAME
    explicit_limits = _load_window_limits(config_path)

    def admin(request: Request) -> None:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")

    def ensure_client(client_id: str) -> str:
        clean = str(client_id or "").strip()
        if not clean or clean not in registry.clients:
            raise HTTPException(status_code=404, detail="Unknown extension ID")
        return clean

    def concurrency_for(client_id: str) -> int:
        runtime = getattr(app.state, "concurrency_config", {})
        resolver = runtime.get("limit_for") if isinstance(runtime, dict) else None
        if callable(resolver):
            return _normalize_limit(resolver(client_id), 1)
        item = registry.clients.get(str(client_id or ""))
        metadata = item.metadata if item and isinstance(getattr(item, "metadata", None), dict) else {}
        fallback = metadata.get("max_concurrency") if isinstance(metadata, dict) else 1
        return _normalize_limit(fallback, 1)

    def window_limit_for(client_id: str) -> int:
        concurrency = concurrency_for(client_id)
        configured = explicit_limits.get(str(client_id or ""))
        if configured is None:
            return concurrency
        # A physical-window hard cap lower than the admitted concurrency cannot
        # be satisfied without adding a second browser-side queue. Keep server
        # admission authoritative by clamping the effective cap upward.
        return max(concurrency, _normalize_limit(configured, concurrency))

    def window_source_for(client_id: str) -> str:
        client_id = str(client_id or "")
        configured = explicit_limits.get(client_id)
        if configured is None:
            return "concurrency"
        if _normalize_limit(configured) < concurrency_for(client_id):
            return "clamped-to-concurrency"
        return "explicit"

    def window_payload(client_id: str) -> dict[str, Any]:
        client_id = ensure_client(client_id)
        concurrency = concurrency_for(client_id)
        effective = window_limit_for(client_id)
        configured = explicit_limits.get(client_id)
        return {
            "client_id": client_id,
            "max_concurrency": concurrency,
            "max_windows": effective,
            "configured_max_windows": int(configured) if configured is not None else None,
            "source": window_source_for(client_id),
            "inherits_concurrency": configured is None,
            "min": MIN_LIMIT,
            "max": MAX_LIMIT,
            "policy": "on-demand-hard-cap-no-warm-pool",
            "revision": PATCH_REVISION,
        }

    async def persist() -> None:
        await asyncio.to_thread(_save_window_limits, config_path, dict(explicit_limits))

    async def request_window_control(client_id: str, target: int, source: str) -> dict[str, Any]:
        item = registry.clients.get(client_id)
        if not item or not getattr(item, "connection_enabled", True):
            return {"ok": False, "pending": True, "reason": "extension_disabled"}
        if client_id not in registry.sockets:
            return {"ok": False, "pending": True, "reason": "extension_offline"}
        metadata = item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}
        try:
            control_version = int(metadata.get("extension_control_version") or 0)
        except (TypeError, ValueError):
            control_version = 0
        if control_version < 36 or metadata.get("extension_control_ready") is not True:
            return {"ok": False, "pending": True, "reason": "extension_control_not_ready"}

        control_id = "ctl_" + uuid.uuid4().hex
        try:
            await registry.send(
                client_id,
                {
                    "type": "extension.control",
                    "control_id": control_id,
                    "action": "windows.limit",
                    "payload": {"target": int(target), "source": str(source)[:40]},
                    "sent_at": time.time(),
                    "minimum_control_version": 36,
                },
            )
        except Exception as exc:
            return {"ok": False, "pending": True, "reason": "extension_control_send_failed", "error": str(exc)[:200]}

        deadline = asyncio.get_running_loop().time() + 12.0
        while asyncio.get_running_loop().time() < deadline:
            current = registry.clients.get(client_id)
            meta = current.metadata if current and isinstance(getattr(current, "metadata", None), dict) else {}
            result = meta.get(CONTROL_RESULT_KEY)
            if isinstance(result, dict) and str(result.get("control_id") or "") == control_id:
                data = result.get("data") if isinstance(result.get("data"), dict) else {}
                return {
                    "ok": result.get("ok") is True,
                    "pending": False,
                    "reason": "",
                    "error": str(result.get("error") or "")[:300],
                    "data": dict(data),
                    "control_id": control_id,
                }
            await asyncio.sleep(0.1)
        return {"ok": False, "pending": True, "reason": "extension_control_timeout", "control_id": control_id}

    # This final wrapper is deliberately passive: it only decorates summaries
    # and routed payloads with the independent window cap. It never chooses a
    # Worker and never performs browser window lifecycle work itself.
    base_summaries = registry.summaries
    if not getattr(registry, "_chat2api_window_limits_v121_summaries", False):
        def summaries_with_window_limits() -> list[dict[str, Any]]:
            rows = base_summaries()
            for row in rows:
                client_id = str(row.get("client_id") or "")
                if not client_id:
                    continue
                row["max_windows"] = window_limit_for(client_id)
                row["configured_max_windows"] = explicit_limits.get(client_id)
                row["window_limit_source"] = window_source_for(client_id)
                capacity = row.get("capacity") if isinstance(row.get("capacity"), dict) else {}
                if isinstance(capacity, dict):
                    capacity["window_limit"] = row["max_windows"]
                    capacity["window_limit_source"] = row["window_limit_source"]
                    row["capacity"] = capacity
            return rows

        registry.summaries = summaries_with_window_limits
        registry._chat2api_window_limits_v121_summaries = True

    base_send = registry.send
    if not getattr(registry, "_chat2api_window_limits_v121_routing", False):
        async def send_with_window_limit(client_id: str, payload: dict[str, Any]) -> None:
            value = dict(payload or {})
            if str(value.get("type") or "") in ROUTED_REQUEST_TYPES:
                routing = dict(value.get("routing") or {})
                routing["worker_window_limit"] = window_limit_for(client_id)
                routing["worker_window_limit_source"] = window_source_for(client_id)
                value["routing"] = routing
            await base_send(client_id, value)

        registry.send = send_with_window_limit
        registry._chat2api_window_limits_v121_routing = True

    app.state.worker_window_limits = {
        "explicit": explicit_limits,
        "limit_for": window_limit_for,
        "source_for": window_source_for,
        "payload_for": window_payload,
        "config_path": str(config_path),
    }

    @app.get("/api/admin/extensions/{client_id}/windows/limit")
    async def get_window_limit(client_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        return window_payload(client_id)

    @app.put("/api/admin/extensions/{client_id}/windows/limit")
    async def put_window_limit(client_id: str, body: WindowLimitUpdate, request: Request) -> dict[str, Any]:
        admin(request)
        client_id = ensure_client(client_id)
        concurrency = concurrency_for(client_id)
        if int(body.max_windows) < concurrency:
            raise HTTPException(
                status_code=409,
                detail=f"最大窗口数不能小于并发上限（当前并发={concurrency}）",
            )
        previous = explicit_limits.get(client_id)
        explicit_limits[client_id] = _normalize_limit(body.max_windows)
        try:
            await persist()
        except Exception:
            if previous is None:
                explicit_limits.pop(client_id, None)
            else:
                explicit_limits[client_id] = previous
            raise
        payload = window_payload(client_id)
        control = await request_window_control(client_id, payload["max_windows"], payload["source"])
        return {**payload, "saved": True, "applied": control.get("ok") is True, "control": control}

    @app.delete("/api/admin/extensions/{client_id}/windows/limit")
    async def reset_window_limit(client_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        client_id = ensure_client(client_id)
        previous = explicit_limits.pop(client_id, None)
        try:
            await persist()
        except Exception:
            if previous is not None:
                explicit_limits[client_id] = previous
            raise
        payload = window_payload(client_id)
        control = await request_window_control(client_id, payload["max_windows"], payload["source"])
        return {**payload, "saved": True, "applied": control.get("ok") is True, "control": control}

    def require_login_ticket(worker_id: str, request: Request) -> str:
        ticket = str(request.headers.get(LOGIN_TICKET_HEADER) or "")
        if not ticket:
            raise HTTPException(status_code=403, detail="Remote login session ticket required")
        worker = linux_workers.data.get("workers", {}).get(worker_id)
        if not worker or worker.get("revoked_at"):
            raise HTTPException(status_code=404, detail="Worker not found")
        try:
            login_sessions.require(worker_id, ticket, touch=True)
        except KeyError as exc:
            raise HTTPException(status_code=403, detail="Remote login session expired or invalid") from exc
        return ticket

    @app.post("/api/admin/linux-workers/{worker_id}/login-session/clipboard")
    async def remote_login_clipboard(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        require_login_ticket(worker_id, request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Clipboard payload must be an object")
        action = str(body.get("action") or "")
        if action == "paste":
            text = str(body.get("text") or "").replace("\x00", "")
            if not text:
                return {"ok": True, "characters": 0}
            if len(text) > MAX_CLIPBOARD_CHARS:
                raise HTTPException(status_code=413, detail=f"Clipboard text is limited to {MAX_CLIPBOARD_CHARS} characters")
            command = await send_worker_command(
                worker_id,
                "login_session_input",
                {"kind": "clipboard", "action": "paste", "text": text},
                wait=True,
                timeout=8,
            )
            result = command.get("result") if isinstance(command.get("result"), dict) else {}
            if not result.get("ok"):
                raise HTTPException(status_code=422, detail=f"Remote paste failed: {str(result.get('error') or 'paste_failed')[:120]}")
            return {"ok": True, "characters": int(result.get("characters") or len(text)), "transport": str(result.get("transport") or "")[:80]}
        if action == "copy_selection":
            command = await send_worker_command(
                worker_id,
                "login_session_input",
                {"kind": "clipboard", "action": "copy_selection"},
                wait=True,
                timeout=8,
            )
            result = command.get("result") if isinstance(command.get("result"), dict) else {}
            if not result.get("ok"):
                raise HTTPException(status_code=422, detail=f"Remote copy failed: {str(result.get('error') or 'copy_failed')[:120]}")
            text = str(result.get("text") or "")[:MAX_CLIPBOARD_CHARS]
            return {"ok": True, "text": text, "characters": len(text), "transport": str(result.get("transport") or "")[:80]}
        raise HTTPException(status_code=400, detail="Unsupported clipboard action")

    @app.get(ASSET_PATH, include_in_schema=False)
    async def worker_limits_clipboard_asset() -> Response:
        path = Path(__file__).with_name("admin_worker_limits_clipboard_v121.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def worker_limits_clipboard_admin_asset(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ASSET_PATH}"></script>'
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
