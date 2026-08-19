from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .linux_worker_installs import LinuxWorkerInstallStore, code_hash
from .runtime_contract import CHROME_BRIDGE_VERSION, SERVER_RUNTIME_VERSION


PATCH_VERSION = "0.22.5"
ACTIVE_EXPIRES_AT = "9999-12-31T23:59:59Z"
DISABLED_EXPIRES_AT = "1970-01-01T00:00:00Z"
BUNDLE_DIR = Path("/app/bootstrap")


def install_linux_worker_install_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_install_patch_installed", False):
        return app
    worker_store = app.state.linux_workers
    installs = LinuxWorkerInstallStore(app.state.settings.data_dir)
    app.state.linux_worker_installs = installs
    app.state.linux_worker_install_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def server_url(request: Request) -> str:
        return app.state.settings.resolved_public_url(str(request.base_url)).rstrip("/")

    def install_command(item: dict[str, Any], request: Request) -> str:
        return (
            f"curl -fsSL {server_url(request)}/bootstrap/linux-worker.sh | sudo bash -s -- "
            f"--server {server_url(request)} --enroll-code {item['code']}"
        )

    def legacy_item(code: str) -> dict[str, Any] | None:
        return worker_store.data["enrollments"].get(code_hash(str(code).strip().upper()))

    def sync_link(item: dict[str, Any]) -> dict[str, Any]:
        legacy = legacy_item(str(item.get("code") or ""))
        worker_id = str((legacy or {}).get("worker_id") or item.get("worker_id") or "")
        if worker_id and worker_id != str(item.get("worker_id") or ""):
            item = installs.link_worker(str(item.get("code") or ""), worker_id)
        return item

    def combined_rows(request: Request) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        linked_workers: set[str] = set()
        for raw in installs.list_admin():
            item = sync_link(raw)
            worker_id = str(item.get("worker_id") or "")
            worker = worker_store.data["workers"].get(worker_id) if worker_id else None
            base = worker_store.public(worker) if worker else {
                "worker_id": "",
                "status": item.get("state") or "pending",
                "network_status": "-",
                "proxy_status": "-",
                "chatgpt_status": "-",
                "platform": "linux",
                "arch": item.get("arch") or "",
                "os_version": item.get("os_version") or "",
                "chrome_bridge_version": "",
                "last_seen_at": None,
                "metadata": {},
            }
            if worker_id:
                linked_workers.add(worker_id)
            row = {
                **base,
                "record_type": "installation",
                "install_id": item["install_id"],
                "name": item["name"],
                "install_state": item["state"],
                "install_stage": item["stage"],
                "install_message": item["message"],
                "install_enabled": bool(item.get("enabled")),
                "install_created_at": item.get("created_at"),
                "install_updated_at": item.get("updated_at"),
                "install_started_at": item.get("started_at"),
                "install_completed_at": item.get("completed_at"),
                "install_failed_at": item.get("failed_at"),
                "install_consumed_at": item.get("consumed_at"),
                "install_command": install_command(item, request),
                "install_history": item.get("history") or [],
                "hostname": base.get("hostname") or item.get("hostname") or "",
            }
            rows.append(row)
        for worker in worker_store.list_public():
            if str(worker.get("worker_id") or "") in linked_workers:
                continue
            rows.append({**worker, "record_type": "worker", "install_state": "legacy", "install_enabled": False})
        rows.sort(key=lambda row: str(row.get("install_created_at") or row.get("created_at") or ""), reverse=True)
        return rows

    @app.post("/api/admin/linux-worker-installations")
    async def create_installation(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        item = installs.create(str(body.get("name") or "Linux Worker"))
        digest = code_hash(item["code"])
        with worker_store._lock:
            worker_store.data["enrollments"][digest] = {
                "code_hash": digest,
                "name": item["name"],
                "created_at": item["created_at"],
                "expires_at": ACTIVE_EXPIRES_AT,
                "used_at": None,
                "install_id": item["install_id"],
            }
            worker_store._save()
        return {**item, "install_command": install_command(item, request), "expires_at": None}

    @app.get("/api/admin/linux-worker-installations")
    async def list_installations(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": combined_rows(request), "version": PATCH_VERSION}

    @app.patch("/api/admin/linux-worker-installations/{install_id}")
    async def update_installation(install_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        current = installs.get(install_id)
        if not current:
            raise HTTPException(404, "Install record not found")
        body = await request.json()
        try:
            item = installs.update(
                install_id,
                name=body.get("name") if "name" in body else None,
                enabled=bool(body.get("enabled")) if "enabled" in body else None,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        digest = code_hash(str(item.get("code") or "").strip().upper())
        with worker_store._lock:
            legacy = worker_store.data["enrollments"].get(digest)
            if legacy and not legacy.get("used_at"):
                legacy["name"] = item["name"]
                legacy["expires_at"] = ACTIVE_EXPIRES_AT if item.get("enabled") else DISABLED_EXPIRES_AT
                worker_store._save()
        return {**item, "install_command": install_command(item, request)}

    @app.delete("/api/admin/linux-worker-installations/{install_id}")
    async def delete_installation(install_id: str, request: Request) -> dict[str, bool]:
        admin(request)
        current = installs.get(install_id)
        if not current:
            raise HTTPException(404, "Install record not found")
        digest = code_hash(str(current.get("code") or "").strip().upper())
        installs.delete(install_id)
        with worker_store._lock:
            worker_store.data["enrollments"].pop(digest, None)
            worker_store._save()
        return {"deleted": True}

    @app.post("/api/workers/install-progress")
    async def worker_install_progress(request: Request) -> dict[str, Any]:
        body = await request.json()
        code = str(body.get("enroll_code") or "").strip().upper()
        if not code:
            raise HTTPException(400, "Enrollment code required")
        state = str(body.get("state") or "installing").strip().lower()
        stage = str(body.get("stage") or "unknown")
        message = str(body.get("message") or "")
        facts = {key: body.get(key) for key in ("hostname", "os_version", "arch") if key in body}
        try:
            item = installs.record_progress(code, stage=stage, state=state, message=message, facts=facts)
        except ValueError as exc:
            raise HTTPException(403, str(exc)) from exc

        digest = code_hash(code)
        with worker_store._lock:
            legacy = worker_store.data["enrollments"].get(digest)
            if legacy and legacy.get("worker_id"):
                item = installs.link_worker(code, str(legacy["worker_id"]))
            if state == "failed" and legacy and not legacy.get("used_at"):
                legacy["expires_at"] = DISABLED_EXPIRES_AT
                worker_store._save()
        return {"ok": True, "install_id": item["install_id"], "state": item["state"], "enabled": item["enabled"]}

    @app.get("/bootstrap/linux-worker-bundle.json", include_in_schema=False)
    async def worker_bundle_manifest() -> dict[str, Any]:
        bundle = BUNDLE_DIR / "linux-worker-bundle.tar.gz"
        digest_file = BUNDLE_DIR / "linux-worker-bundle.sha256"
        if not bundle.is_file() or not digest_file.is_file():
            raise HTTPException(503, "Worker bundle is not packaged in this server image")
        return {
            "object": "chat2api.linux-worker-bundle",
            "version": 1,
            "server_runtime": SERVER_RUNTIME_VERSION,
            "chrome_bridge": CHROME_BRIDGE_VERSION,
            "sha256": digest_file.read_text(encoding="utf-8").strip(),
            "size": bundle.stat().st_size,
        }

    @app.get("/bootstrap/linux-worker-bundle.tar.gz", include_in_schema=False)
    async def worker_bundle_download() -> Response:
        bundle = BUNDLE_DIR / "linux-worker-bundle.tar.gz"
        digest_file = BUNDLE_DIR / "linux-worker-bundle.sha256"
        if not bundle.is_file() or not digest_file.is_file():
            raise HTTPException(503, "Worker bundle is not packaged in this server image")
        return Response(
            bundle.read_bytes(),
            media_type="application/gzip",
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Chat2API-SHA256": digest_file.read_text(encoding="utf-8").strip(),
                "Content-Disposition": 'attachment; filename="linux-worker-bundle.tar.gz"',
            },
        )

    return app
