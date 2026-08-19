from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.22.18"
MAX_PROXY_NAME_LENGTH = 80
CHINESE_PROGRESS_ASSET = "/assets/chat2api-linux-worker-chinese-progress-v22-18.js"


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


def _safe_port(value: Any) -> int:
    try:
        port = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return port if 0 < port <= 65535 else 0


def _decode_b64_json(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    text += "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            payload = json.loads(decoder(text.encode("ascii")).decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            continue
    return {}


def _decode_b64_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text += "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(text.encode("ascii")).decode("utf-8")
        except Exception:
            continue
    return ""


def _proxy_endpoint(share_link: str) -> tuple[str, str, int]:
    raw = str(share_link or "").strip()
    if "://" not in raw:
        return "", "", 0
    scheme, remainder = raw.split("://", 1)
    protocol = scheme.lower()
    if protocol == "vmess":
        payload = _decode_b64_json(remainder.split("#", 1)[0])
        return "vmess", str(payload.get("add") or "").strip().lower(), _safe_port(payload.get("port"))

    try:
        parsed = urlsplit(raw)
        hostname = str(parsed.hostname or "").strip().lower()
        port = _safe_port(parsed.port)
    except (TypeError, ValueError):
        hostname = ""
        port = 0
    if hostname:
        return ("ss" if protocol == "shadowsocks" else protocol), hostname, port

    if protocol == "ss":
        encoded = remainder.split("#", 1)[0]
        decoded = _decode_b64_text(encoded)
        host_part = decoded.rsplit("@", 1)[-1]
        if ":" in host_part:
            host, raw_port = host_part.rsplit(":", 1)
            port = _safe_port(raw_port)
            if port:
                return "ss", host.strip("[]").lower(), port
    return protocol, "", 0


def _fallback_name_from_link(share_link: str) -> str:
    raw = str(share_link or "").strip()
    try:
        fragment = unquote(urlsplit(raw).fragment).strip()
    except Exception:
        fragment = ""
    return fragment[:MAX_PROXY_NAME_LENGTH]


def install_linux_worker_proxy_name_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_proxy_name_patch_installed", False):
        return app

    workers = app.state.linux_workers
    catalog = app.state.linux_worker_proxy_catalog
    app.state.linux_worker_proxy_name_patch_installed = True

    def catalog_name_for_link(share_link: str) -> str:
        raw = str(share_link or "").strip()
        for item in catalog.list():
            if str(item.get("share_link") or "").strip() == raw:
                return str(item.get("name") or "").strip()[:MAX_PROXY_NAME_LENGTH]
        return _fallback_name_from_link(raw)

    def catalog_name_for_summary(summary: dict[str, Any]) -> str:
        protocol = str(summary.get("protocol") or "").lower()
        server = str(summary.get("server") or "").strip().lower()
        port = _safe_port(summary.get("port"))
        if not protocol or not server:
            return ""
        for item in catalog.list():
            item_protocol, item_server, item_port = _proxy_endpoint(str(item.get("share_link") or ""))
            if item_protocol == protocol and item_server == server and (not port or not item_port or item_port == port):
                return str(item.get("name") or "").strip()[:MAX_PROXY_NAME_LENGTH]
        return ""

    def persist_name(worker_id: str, name: str) -> None:
        clean = str(name or "").strip()[:MAX_PROXY_NAME_LENGTH]
        if not clean:
            return
        with workers._lock:
            worker = workers.data["workers"].get(worker_id)
            if not worker or str(worker.get("proxy_status") or "").lower() not in {"connected", "ready"}:
                return
            metadata = dict(worker.get("metadata") or {})
            summary = dict(metadata.get("proxy_summary") or {}) if isinstance(metadata.get("proxy_summary"), dict) else {}
            if not summary or summary.get("name") == clean:
                return
            summary["name"] = clean
            metadata["proxy_summary"] = summary
            worker["metadata"] = metadata
            workers._save()

    @app.get(CHINESE_PROGRESS_ASSET, include_in_schema=False)
    async def linux_worker_chinese_progress_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_worker_chinese_progress.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_worker_proxy_names(request: Request, call_next):
        path = request.url.path
        apply_prefix = "/api/admin/linux-workers/"
        apply_suffix = "/proxy"
        worker_id = ""
        proxy_name = ""

        if request.method == "POST" and path.startswith(apply_prefix) and path.endswith(apply_suffix):
            worker_id = path[len(apply_prefix):-len(apply_suffix)].strip("/")
            try:
                body = await request.json()
            except Exception:
                body = {}
            if isinstance(body, dict):
                proxy_name = catalog_name_for_link(str(body.get("share_link") or ""))

        response = await call_next(request)

        if worker_id and proxy_name and response.status_code < 400:
            persist_name(worker_id, proxy_name)

        if path == "/api/admin/linux-worker-installations" and request.method == "GET" and "application/json" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                for row in payload["data"]:
                    if not isinstance(row, dict) or str(row.get("proxy_status") or "").lower() not in {"connected", "ready"}:
                        continue
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    summary = metadata.get("proxy_summary") if isinstance(metadata.get("proxy_summary"), dict) else {}
                    if not summary or summary.get("name"):
                        continue
                    name = catalog_name_for_summary(summary)
                    if not name:
                        continue
                    summary = dict(summary)
                    summary["name"] = name
                    metadata = dict(metadata)
                    metadata["proxy_summary"] = summary
                    row["metadata"] = metadata
                    worker_row_id = str(row.get("worker_id") or "")
                    if worker_row_id:
                        persist_name(worker_row_id, name)
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{CHINESE_PROGRESS_ASSET}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app