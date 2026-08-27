from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .admin_auth import SESSION_COOKIE
from .runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, CHROME_BRIDGE_VERSION, SERVER_RUNTIME_VERSION


ASSET_PATH = "/assets/chat2api-server-update.js"
GITHUB_REPOSITORY = "b8vipvip/chat2api"
GITHUB_MAIN_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/commits/main"
UPDATE_REQUEST_NAME = "admin-update-request.json"
UPDATE_STATUS_NAME = "admin-update-status.json"
UPDATE_LOG_NAME = "admin-update.log"
UPDATER_MARKER_NAME = "admin-updater-installed.json"
DEPLOYMENT_NAME = "deployment.json"
REMOTE_CACHE_SECONDS = 30.0


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _tail_log(path: Path, max_lines: int = 180, max_chars: int = 120_000) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except OSError:
        return []
    return [line.rstrip() for line in text.splitlines() if line.strip()][-max_lines:]


def _data_dir(app: FastAPI) -> Path:
    return Path(app.state.settings.data_dir)


def _status_payload(app: FastAPI) -> dict[str, Any]:
    data_dir = _data_dir(app)
    raw = _read_json(data_dir / UPDATE_STATUS_NAME)
    marker = _read_json(data_dir / UPDATER_MARKER_NAME)
    status = str(raw.get("status") or "idle")
    return {
        "status": status,
        "stage": str(raw.get("stage") or ""),
        "percent": max(0, min(int(raw.get("percent") or 0), 100)),
        "message": str(raw.get("message") or ""),
        "request_id": str(raw.get("request_id") or ""),
        "started_at": str(raw.get("started_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "completed_at": str(raw.get("completed_at") or ""),
        "from_commit": str(raw.get("from_commit") or ""),
        "target_commit": str(raw.get("target_commit") or ""),
        "deployed_commit": str(raw.get("deployed_commit") or ""),
        "rollback_commit": str(raw.get("rollback_commit") or ""),
        "rollback_succeeded": bool(raw.get("rollback_succeeded", False)),
        "updater_installed": bool(marker.get("installed")),
        "updater_mode": str(marker.get("mode") or ""),
        "logs": _tail_log(data_dir / UPDATE_LOG_NAME),
    }


async def _remote_main(app: FastAPI, *, refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cache = getattr(app.state, "server_update_remote_cache", None)
    if not refresh and isinstance(cache, dict) and now - float(cache.get("at") or 0) < REMOTE_CACHE_SECONDS:
        value = cache.get("value")
        if isinstance(value, dict):
            return value

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(
                GITHUB_MAIN_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "chat2api-server-update-check",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            payload = response.json()
        commit = payload.get("commit") if isinstance(payload, dict) else {}
        message = str((commit or {}).get("message") or "").splitlines()[0]
        result = {
            "ok": True,
            "sha": str(payload.get("sha") or ""),
            "short_sha": str(payload.get("sha") or "")[:12],
            "message": message,
            "url": str(payload.get("html_url") or ""),
        }
    except Exception as exc:
        result = {"ok": False, "sha": "", "short_sha": "", "message": "", "url": "", "error": str(exc)[:320]}

    app.state.server_update_remote_cache = {"at": now, "value": result}
    return result


def install_server_update_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "server_update_patch_installed", False):
        return app
    app.state.server_update_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    @app.get("/api/admin/server-update")
    async def server_update_overview(request: Request, refresh: int = 0) -> dict[str, Any]:
        admin(request)
        data_dir = _data_dir(app)
        marker = _read_json(data_dir / UPDATER_MARKER_NAME)
        deployment = _read_json(data_dir / DEPLOYMENT_NAME)
        remote = await _remote_main(app, refresh=bool(refresh))
        deployed_commit = str(deployment.get("commit") or "")
        remote_sha = str(remote.get("sha") or "")
        return {
            "repository": GITHUB_REPOSITORY,
            "branch": "main",
            "server_runtime_version": SERVER_RUNTIME_VERSION,
            "chrome_bridge_version": CHROME_BRIDGE_VERSION,
            "chrome_bundle_version": CHROME_BRIDGE_BUNDLE_VERSION,
            "deployed_commit": deployed_commit,
            "deployed_short_commit": deployed_commit[:12],
            "deployment": deployment,
            "remote": remote,
            "update_available": bool(deployed_commit and remote_sha and deployed_commit != remote_sha),
            "updater": {
                "installed": bool(marker.get("installed")),
                "mode": str(marker.get("mode") or ""),
                "installed_at": str(marker.get("installed_at") or ""),
                "app_dir": str(marker.get("app_dir") or "/opt/chat2api"),
                "install_command": "sudo bash /opt/chat2api/scripts/install_chat2api_server_updater.sh",
            },
            "status": _status_payload(app),
        }

    @app.get("/api/admin/server-update/status")
    async def server_update_status(request: Request) -> dict[str, Any]:
        admin(request)
        return _status_payload(app)

    @app.post("/api/admin/server-update/start")
    async def server_update_start(request: Request) -> JSONResponse:
        admin(request)
        data_dir = _data_dir(app)
        marker = _read_json(data_dir / UPDATER_MARKER_NAME)
        if not marker.get("installed"):
            raise HTTPException(409, "主机更新助手尚未安装，请先执行控制台显示的一次性安装命令")
        current = _status_payload(app)
        if current.get("status") in {"queued", "running"}:
            raise HTTPException(409, "已有服务端更新任务正在执行")

        request_id = f"upd_{uuid.uuid4().hex[:18]}"
        now = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(
            data_dir / UPDATE_STATUS_NAME,
            {
                "status": "queued",
                "stage": "queued",
                "percent": 1,
                "message": "更新请求已提交，等待主机 systemd 更新助手接管",
                "request_id": request_id,
                "started_at": now,
                "updated_at": now,
                "completed_at": "",
            },
        )
        _write_json_atomic(
            data_dir / UPDATE_REQUEST_NAME,
            {
                "request_id": request_id,
                "requested_at": now,
                "repository": GITHUB_REPOSITORY,
                "branch": "main",
            },
        )
        return JSONResponse({"ok": True, "request_id": request_id, "status": "queued"}, status_code=202)

    @app.get(ASSET_PATH, include_in_schema=False)
    async def admin_server_update_js() -> Response:
        source = Path(__file__).with_name("admin_server_update.js").read_text(encoding="utf-8")
        return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_server_update_console(request: Request, call_next):
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
