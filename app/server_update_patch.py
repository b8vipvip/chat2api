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
GITHUB_SMART_REFS = f"https://github.com/{GITHUB_REPOSITORY}.git/info/refs?service=git-upload-pack"
UPDATE_REQUEST_NAME = "admin-update-request.json"
UPDATE_STATUS_NAME = "admin-update-status.json"
UPDATE_LOG_NAME = "admin-update.log"
UPDATER_MARKER_NAME = "admin-updater-installed.json"
DEPLOYMENT_NAME = "deployment.json"
# Even an explicit UI refresh respects this short anti-stampede window. The old
# terminal-state polling path could otherwise bypass the cache every few seconds.
REMOTE_CACHE_SECONDS = 60.0


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


def _git_smart_main_sha(body: bytes) -> str:
    """Extract refs/heads/main from a Git smart-HTTP v0 advertisement."""
    cursor = 0
    target = b"refs/heads/main"
    while cursor + 4 <= len(body):
        prefix = body[cursor : cursor + 4]
        cursor += 4
        try:
            size = int(prefix, 16)
        except ValueError:
            return ""
        if size == 0:
            continue
        if size < 4 or cursor + size - 4 > len(body):
            return ""
        packet = body[cursor : cursor + size - 4]
        cursor += size - 4
        line = packet.rstrip(b"\n")
        if b"\x00" in line:
            line = line.split(b"\x00", 1)[0]
        fields = line.split()
        if len(fields) >= 2 and fields[1] == target:
            sha = fields[0].decode("ascii", errors="ignore").lower()
            if len(sha) == 40 and all(ch in "0123456789abcdef" for ch in sha):
                return sha
    return ""


async def _remote_main(app: FastAPI, *, refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cache = getattr(app.state, "server_update_remote_cache", None)
    if isinstance(cache, dict) and now - float(cache.get("at") or 0) < REMOTE_CACHE_SECONDS:
        value = cache.get("value")
        if isinstance(value, dict):
            return value

    smart_error = ""
    result: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(
                GITHUB_SMART_REFS,
                headers={
                    "Accept": "application/x-git-upload-pack-advertisement",
                    "User-Agent": "chat2api-server-update-check",
                },
            )
            response.raise_for_status()
            sha = _git_smart_main_sha(response.content)
            if not sha:
                raise ValueError("Git smart HTTP response did not advertise refs/heads/main")
        result = {
            "ok": True,
            "sha": sha,
            "short_sha": sha[:12],
            "message": "通过 Git Smart HTTP 检查 main（不消耗 GitHub REST API 配额）",
            "url": f"https://github.com/{GITHUB_REPOSITORY}/commit/{sha}",
            "source": "git-smart-http",
        }
    except Exception as exc:
        smart_error = str(exc)[:240]

    # Smart HTTP is the normal path for this public repository. Keep a REST API
    # fallback for unusual proxies/private deployments. An optional token raises
    # the REST rate limit, but no token/deploy key is required for the public repo.
    if result is None:
        try:
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "chat2api-server-update-check",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            token = str(os.getenv("CHAT2API_GITHUB_TOKEN") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                response = await client.get(GITHUB_MAIN_API, headers=headers)
                response.raise_for_status()
                payload = response.json()
            commit = payload.get("commit") if isinstance(payload, dict) else {}
            message = str((commit or {}).get("message") or "").splitlines()[0]
            sha = str(payload.get("sha") or "")
            result = {
                "ok": True,
                "sha": sha,
                "short_sha": sha[:12],
                "message": message,
                "url": str(payload.get("html_url") or ""),
                "source": "github-rest-api",
            }
        except Exception as exc:
            rest_error = str(exc)[:240]
            result = {
                "ok": False,
                "sha": "",
                "short_sha": "",
                "message": "",
                "url": "",
                "error": f"Git Smart HTTP: {smart_error}; GitHub API fallback: {rest_error}"[:480],
            }

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

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        use_build_cache = body.get("use_build_cache") is not False

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
                "use_build_cache": use_build_cache,
            },
        )
        return JSONResponse(
            {"ok": True, "request_id": request_id, "status": "queued", "use_build_cache": use_build_cache},
            status_code=202,
        )

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
