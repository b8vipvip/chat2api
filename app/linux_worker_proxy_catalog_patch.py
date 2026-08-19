from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .admin_auth import SESSION_COOKIE


PROXY_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss"})
MAX_PROXY_LINK_LENGTH = 16384
MAX_PROXY_NAME_LENGTH = 80


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_name(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text[:MAX_PROXY_NAME_LENGTH] or fallback


def validate_proxy_share_link(value: Any) -> str:
    share_link = str(value or "").strip()
    if not share_link or len(share_link) > MAX_PROXY_LINK_LENGTH or "\n" in share_link or "\r" in share_link:
        raise ValueError("Proxy share link is empty, too long, or not a single line")
    scheme = share_link.split(":", 1)[0].lower()
    if scheme not in PROXY_SCHEMES:
        raise ValueError("Supported proxy links: VLESS, VMess, Trojan, Shadowsocks")
    return share_link


class LinuxWorkerProxyCatalog:
    """Admin-managed proxy links stored only in the center server data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "linux_worker_proxies.json"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = {"proxies": []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict) and isinstance(loaded.get("proxies"), list):
                self.data = loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.path)
        self.path.chmod(0o600)

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        share_link = str(item.get("share_link") or "")
        return {
            "proxy_id": str(item.get("proxy_id") or ""),
            "name": str(item.get("name") or ""),
            "share_link": share_link,
            "scheme": share_link.split(":", 1)[0].lower() if ":" in share_link else "",
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(item) for item in self.data["proxies"] if isinstance(item, dict)]

    def create(self, name: Any, share_link: Any) -> dict[str, Any]:
        clean_link = validate_proxy_share_link(share_link)
        now = _utc_iso()
        with self._lock:
            fallback = f"代理 {len(self.data['proxies']) + 1}"
            item = {
                "proxy_id": "pxy_" + secrets.token_hex(8),
                "name": _normalize_name(name, fallback=fallback),
                "share_link": clean_link,
                "created_at": now,
                "updated_at": now,
            }
            self.data["proxies"].append(item)
            self._save()
            return self._public(item)

    def update(self, proxy_id: str, *, name: Any | None = None, share_link: Any | None = None) -> dict[str, Any]:
        with self._lock:
            item = next((entry for entry in self.data["proxies"] if isinstance(entry, dict) and entry.get("proxy_id") == proxy_id), None)
            if not item:
                raise KeyError(proxy_id)
            if name is not None:
                item["name"] = _normalize_name(name, fallback=str(item.get("name") or "代理"))
            if share_link is not None:
                item["share_link"] = validate_proxy_share_link(share_link)
            item["updated_at"] = _utc_iso()
            self._save()
            return self._public(item)

    def delete(self, proxy_id: str) -> None:
        with self._lock:
            before = len(self.data["proxies"])
            self.data["proxies"] = [
                item for item in self.data["proxies"]
                if not (isinstance(item, dict) and item.get("proxy_id") == proxy_id)
            ]
            if len(self.data["proxies"]) == before:
                raise KeyError(proxy_id)
            self._save()


def install_linux_worker_proxy_catalog_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_proxy_catalog_installed", False):
        return app

    catalog = LinuxWorkerProxyCatalog(app.state.settings.data_dir)
    app.state.linux_worker_proxy_catalog = catalog
    app.state.linux_worker_proxy_catalog_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    @app.get("/api/admin/linux-worker-proxies")
    async def list_linux_worker_proxies(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": catalog.list()}

    @app.post("/api/admin/linux-worker-proxies")
    async def create_linux_worker_proxy(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        try:
            item = catalog.create(body.get("name"), body.get("share_link"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"proxy": item}

    @app.patch("/api/admin/linux-worker-proxies/{proxy_id}")
    async def update_linux_worker_proxy(proxy_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        try:
            item = catalog.update(
                proxy_id,
                name=body.get("name") if "name" in body else None,
                share_link=body.get("share_link") if "share_link" in body else None,
            )
        except KeyError as exc:
            raise HTTPException(404, "Proxy entry not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"proxy": item}

    @app.delete("/api/admin/linux-worker-proxies/{proxy_id}")
    async def delete_linux_worker_proxy(proxy_id: str, request: Request) -> dict[str, bool]:
        admin(request)
        try:
            catalog.delete(proxy_id)
        except KeyError as exc:
            raise HTTPException(404, "Proxy entry not found") from exc
        return {"deleted": True}

    return app
