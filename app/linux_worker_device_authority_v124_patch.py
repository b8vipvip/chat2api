from __future__ import annotations

import asyncio
import copy
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .admin_auth import SESSION_COOKIE


PATCH_REVISION = 124
MIN_SLOT = 2
MAX_SLOT = 32
ASSET_PATH = "/assets/chat2api-linux-device-authority-v124.js"
LEGACY_LINUX_ASSET_RE = re.compile(r'<script[^>]+src="/assets/chat2api-linux-[^"]+"[^>]*></script>', re.I)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_url(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    return f"{scheme}://{host}".rstrip("/")


def _worker_slot(worker: dict[str, Any]) -> int:
    meta = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    try:
        return max(1, int(meta.get("worker_slot") or 1))
    except (TypeError, ValueError):
        return 1


def _install_meta(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return dict(meta)


def _is_v124(row: dict[str, Any]) -> bool:
    meta = _install_meta(row)
    return int(meta.get("device_authority_revision") or 0) == PATCH_REVISION


def _is_device_install(row: dict[str, Any]) -> bool:
    return _is_v124(row) and _install_meta(row).get("install_kind") == "device"


def _is_slot_install(row: dict[str, Any]) -> bool:
    return _is_v124(row) and _install_meta(row).get("install_kind") == "worker_slot"


def _device_id(row: dict[str, Any]) -> str:
    return str(_install_meta(row).get("device_id") or row.get("install_id") or "")


def _persist_install_metadata(store: Any, install_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    with store._lock:
        row = store._find(install_id)
        if row is None:
            raise KeyError(install_id)
        current = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        row["metadata"] = {**current, **copy.deepcopy(metadata)}
        row["updated_at"] = _utc_iso()
        store._save()
        return store._public(row)


def _delete_install(store: Any, install_id: str) -> bool:
    with store._lock:
        before = len(store.data.get("installations", []))
        store.data["installations"] = [
            row for row in store.data.get("installations", [])
            if not (isinstance(row, dict) and str(row.get("install_id") or "") == install_id)
        ]
        changed = len(store.data["installations"]) != before
        if changed:
            store._save()
        return changed


def _delete_worker(store: Any, worker_id: str) -> bool:
    with store._lock:
        before = len(store._workers)
        store._workers = [row for row in store._workers if str(row.get("worker_id") or "") != worker_id]
        changed = len(store._workers) != before
        if changed:
            store._save()
        return changed


def _recent(worker: dict[str, Any], seconds: int = 45) -> bool:
    raw = str(worker.get("last_seen_at") or "").strip()
    if not raw:
        return False
    try:
        seen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - seen).total_seconds() <= seconds
    except ValueError:
        return False


def _device_rows(app: FastAPI) -> list[dict[str, Any]]:
    installs = app.state.linux_worker_installs.list()
    workers = app.state.linux_workers.list_public()
    workers_by_id = {str(row.get("worker_id") or ""): row for row in workers if row.get("worker_id")}
    device_installs = [row for row in installs if _is_device_install(row)]
    slot_installs = [row for row in installs if _is_slot_install(row)]
    result: list[dict[str, Any]] = []
    for device in device_installs:
        meta = _install_meta(device)
        device_id = _device_id(device)
        related = [row for row in slot_installs if str(_install_meta(row).get("parent_device_id") or "") == device_id]
        worker_rows: list[dict[str, Any]] = []
        primary_id = str(device.get("worker_id") or "")
        if primary_id and primary_id in workers_by_id:
            primary = dict(workers_by_id[primary_id])
            primary["worker_slot"] = 1
            primary["install_id"] = device.get("install_id")
            worker_rows.append(primary)
        for slot_install in related:
            worker_id = str(slot_install.get("worker_id") or "")
            slot = int(_install_meta(slot_install).get("worker_slot") or 0)
            if worker_id and worker_id in workers_by_id:
                worker = dict(workers_by_id[worker_id])
                worker["worker_slot"] = slot
                worker["install_id"] = slot_install.get("install_id")
                worker_rows.append(worker)
        worker_rows.sort(key=lambda row: int(row.get("worker_slot") or 1))
        result.append({
            "device_id": device_id,
            "device_name": str(meta.get("device_name") or device.get("name") or "Linux 设备"),
            "device_pairing_id": str(meta.get("pairing_id") or ""),
            "proxy_id": str(meta.get("proxy_id") or ""),
            "proxy_name": str(meta.get("proxy_name") or ""),
            "install_id": str(device.get("install_id") or ""),
            "install_state": str(device.get("state") or "pending"),
            "install_stage": str(device.get("stage") or ""),
            "install_message": str(device.get("message") or ""),
            "install_command": str(device.get("install_command") or ""),
            "install_enabled": bool(device.get("enabled", True)),
            "install_created_at": device.get("created_at"),
            "install_updated_at": device.get("updated_at"),
            "workers": worker_rows,
            "worker_count": len(worker_rows),
            "slot_installations": [
                {
                    "install_id": str(row.get("install_id") or ""),
                    "slot": int(_install_meta(row).get("worker_slot") or 0),
                    "state": str(row.get("state") or "pending"),
                    "command": str(row.get("install_command") or ""),
                    "worker_id": str(row.get("worker_id") or ""),
                }
                for row in sorted(related, key=lambda item: int(_install_meta(item).get("worker_slot") or 0))
            ],
            "authority": "linux-device-v124",
        })
    result.sort(key=lambda row: str(row.get("install_created_at") or ""), reverse=True)
    return result


def _legacy_snapshot(app: FastAPI) -> dict[str, Any]:
    installs = app.state.linux_worker_installs.list()
    workers = app.state.linux_workers.list_public()
    v124_worker_ids: set[str] = set()
    for row in installs:
        if not _is_v124(row):
            continue
        worker_id = str(row.get("worker_id") or "")
        if worker_id:
            v124_worker_ids.add(worker_id)
    legacy_workers = [row for row in workers if str(row.get("worker_id") or "") not in v124_worker_ids]
    legacy_installs = [row for row in installs if not _is_v124(row)]
    return {
        "workers": [
            {
                "worker_id": str(row.get("worker_id") or ""),
                "name": str(row.get("name") or row.get("device_id") or "Linux Worker"),
                "last_seen_at": row.get("last_seen_at"),
                "revoked": bool(row.get("revoked_at")),
                "online": _recent(row),
            }
            for row in legacy_workers
        ],
        "installations": [
            {"install_id": str(row.get("install_id") or ""), "name": str(row.get("name") or "Linux Worker")}
            for row in legacy_installs
        ],
    }


def install_linux_worker_device_authority_v124_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_device_authority_v124_installed", False):
        return app
    app.state.linux_worker_device_authority_v124_installed = True

    script = (Path(__file__).with_name("admin_linux_device_authority_v124.js")).read_text(encoding="utf-8")
    base_enroll = app.state.linux_workers.enroll

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    async def setup_options() -> dict[str, Any]:
        pairings = await app.state.registry.list_pairing_codes()
        proxies = app.state.linux_worker_proxy_catalog.list()
        return {
            "pairing_codes": [
                {
                    "pairing_id": str(item.get("pairing_id") or ""),
                    "device_name": str(item.get("name") or "未命名设备"),
                    "prefix": str(item.get("code_prefix") or ""),
                    "enabled": bool(item.get("enabled", True)),
                    "paired": bool(item.get("client_id")),
                }
                for item in pairings if isinstance(item, dict)
            ],
            "proxies": proxies,
        }

    def create_install(request: Request, *, pairing: dict[str, Any], proxy: dict[str, Any], slot: int, parent_device_id: str = "") -> dict[str, Any]:
        device_name = str(pairing.get("device_name") or "Linux 设备").strip()[:80] or "Linux 设备"
        worker_name = device_name if slot == 1 else f"{device_name} · Worker {slot}"
        metadata = {
            "device_authority_revision": PATCH_REVISION,
            "device_name": device_name,
            "pairing_id": str(pairing.get("pairing_id") or ""),
            "pairing_prefix": str(pairing.get("prefix") or ""),
            "proxy_id": str(proxy.get("proxy_id") or ""),
            "proxy_name": str(proxy.get("name") or ""),
            "proxy_share_link": str(proxy.get("share_link") or ""),
            "worker_slot": slot,
            "install_kind": "device" if slot == 1 else "worker_slot",
            "parent_device_id": parent_device_id,
        }
        enrollment = app.state.worker_enrollment.create(worker_name, metadata=metadata)
        install = app.state.linux_worker_installs.create(worker_name, enrollment["code"])
        device_id = parent_device_id or str(install.get("install_id") or "")
        metadata["device_id"] = device_id
        _persist_install_metadata(app.state.linux_worker_installs, str(install["install_id"]), metadata)
        server = _server_url(request)
        if slot == 1:
            command = (
                f"curl -fsSL {shlex.quote(server + '/api/workers/bootstrap')} | "
                f"sudo bash -s -- --server {shlex.quote(server)} --enroll-code {shlex.quote(enrollment['code'])}"
            )
        else:
            command = (
                "sudo bash /opt/chat2api-worker/scripts/linux_worker_slot_install_reported.sh "
                f"--server {shlex.quote(server)} --enroll-code {shlex.quote(enrollment['code'])} --slot {slot}"
            )
        app.state.linux_worker_installs.mark_command(str(install["install_id"]), command)
        row = app.state.linux_worker_installs.get(str(install["install_id"])) or {}
        return {"install": row, "install_command": command, "device_id": device_id, **metadata}

    def enroll_with_device_authority(*, code: str, metadata: dict[str, Any]) -> dict[str, Any]:
        result = base_enroll(code=code, metadata=metadata)
        worker_id = str(result.get("worker_id") or "")
        enroll_meta = app.state.worker_enrollment.metadata_for_worker(worker_id)
        if not isinstance(enroll_meta, dict) or int(enroll_meta.get("device_authority_revision") or 0) != PATCH_REVISION:
            return result
        slot = int(enroll_meta.get("worker_slot") or 1)
        app.state.linux_workers.update_metadata(worker_id, {
            "device_authority_revision": PATCH_REVISION,
            "device_id_v124": str(enroll_meta.get("device_id") or ""),
            "device_name": str(enroll_meta.get("device_name") or result.get("name") or "Linux 设备"),
            "device_pairing_id": str(enroll_meta.get("pairing_id") or ""),
            "device_pairing_prefix": str(enroll_meta.get("pairing_prefix") or ""),
            "worker_slot": slot,
            "proxy_catalog_id": str(enroll_meta.get("proxy_id") or ""),
            "proxy_catalog_name": str(enroll_meta.get("proxy_name") or ""),
        })
        share_link = str(enroll_meta.get("proxy_share_link") or "").strip()
        proxy_name = str(enroll_meta.get("proxy_name") or "").strip()
        if share_link:
            async def apply_proxy() -> None:
                await asyncio.sleep(1.0)
                try:
                    await app.state.send_linux_worker_command(worker_id, "apply_proxy", {"share_link": share_link}, 90)
                    app.state.linux_workers.update_metadata(worker_id, {"proxy_catalog_name": proxy_name})
                except Exception:
                    return
            try:
                asyncio.get_running_loop().create_task(apply_proxy())
            except RuntimeError:
                pass
        return result

    app.state.linux_workers.enroll = enroll_with_device_authority

    @app.get("/api/admin/linux-devices")
    async def list_linux_devices(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": _device_rows(app), "authority": "linux-device-v124", "revision": PATCH_REVISION}

    @app.get("/api/admin/linux-devices/setup-options")
    async def linux_device_setup_options(request: Request) -> dict[str, Any]:
        admin(request)
        return await setup_options()

    @app.post("/api/admin/linux-devices")
    async def create_linux_device(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        pairing_id = str(body.get("pairing_id") or "").strip()
        proxy_id = str(body.get("proxy_id") or "").strip()
        options = await setup_options()
        pairing = next((item for item in options["pairing_codes"] if item["pairing_id"] == pairing_id), None)
        proxy = next((item for item in options["proxies"] if item["proxy_id"] == proxy_id), None)
        if not pairing or not pairing.get("enabled") or pairing.get("paired"):
            raise HTTPException(409, "请选择启用且未配对的设备码")
        if not proxy:
            raise HTTPException(404, "代理不存在，请先在代理管理中添加")
        for row in _device_rows(app):
            if str(row.get("device_pairing_id") or "") == pairing_id:
                raise HTTPException(409, "这个设备码已经属于一个 Linux 设备")
        return create_install(request, pairing=pairing, proxy=proxy, slot=1)

    @app.post("/api/admin/linux-devices/{device_id}/workers")
    async def add_linux_device_worker(device_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        device = next((row for row in _device_rows(app) if row["device_id"] == device_id), None)
        if not device:
            raise HTTPException(404, "Linux 设备不存在")
        if not device.get("workers"):
            raise HTTPException(409, "请先完成 Worker 1 安装，再增加 Worker")
        pairing_id = str(body.get("pairing_id") or "").strip()
        proxy_id = str(body.get("proxy_id") or "").strip()
        options = await setup_options()
        pairing = next((item for item in options["pairing_codes"] if item["pairing_id"] == pairing_id), None)
        proxy = next((item for item in options["proxies"] if item["proxy_id"] == proxy_id), None)
        if not pairing or not pairing.get("enabled") or pairing.get("paired"):
            raise HTTPException(409, "请选择启用且未配对的设备码")
        if str(pairing.get("device_name") or "") != str(device.get("device_name") or ""):
            raise HTTPException(409, "新增 Worker 的设备码名称必须与设备名称一致")
        if not proxy:
            raise HTTPException(404, "代理不存在")
        occupied = {1}
        occupied.update(int(row.get("worker_slot") or 0) for row in device.get("workers", []))
        occupied.update(int(row.get("slot") or 0) for row in device.get("slot_installations", []))
        slot = next((value for value in range(MIN_SLOT, MAX_SLOT + 1) if value not in occupied), None)
        if slot is None:
            raise HTTPException(409, "此设备已达到 32 个 Worker 上限")
        return create_install(request, pairing=pairing, proxy=proxy, slot=slot, parent_device_id=device_id)

    @app.delete("/api/admin/linux-devices/{device_id}")
    async def delete_linux_device(device_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        device = next((row for row in _device_rows(app) if row["device_id"] == device_id), None)
        if not device:
            raise HTTPException(404, "Linux 设备不存在")
        online = [row for row in device.get("workers", []) if _recent(row)]
        if online:
            raise HTTPException(409, "设备仍有在线 Worker，请先在目标 Linux 主机卸载后再彻底删除")
        worker_ids = [str(row.get("worker_id") or "") for row in device.get("workers", []) if row.get("worker_id")]
        install_ids = [str(device.get("install_id") or "")]
        install_ids.extend(str(row.get("install_id") or "") for row in device.get("slot_installations", []))
        for worker_id in worker_ids:
            _delete_worker(app.state.linux_workers, worker_id)
        for install_id in install_ids:
            if install_id:
                _delete_install(app.state.linux_worker_installs, install_id)
        return {"deleted": True, "device_id": device_id, "workers_deleted": worker_ids, "installations_deleted": install_ids}

    @app.get("/api/admin/linux-devices/legacy-records")
    async def list_legacy_linux_records(request: Request) -> dict[str, Any]:
        admin(request)
        snapshot = _legacy_snapshot(app)
        return {**snapshot, "worker_count": len(snapshot["workers"]), "installation_count": len(snapshot["installations"])}

    @app.delete("/api/admin/linux-devices/legacy-records")
    async def purge_legacy_linux_records(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        if str(body.get("confirm") or "") != "PURGE_LEGACY_LINUX":
            raise HTTPException(400, "需要明确确认清理旧 Linux 数据")
        snapshot = _legacy_snapshot(app)
        online = [row for row in snapshot["workers"] if row.get("online")]
        if online:
            raise HTTPException(409, "仍有旧 Linux Worker 在线，先在目标主机卸载后再清理")
        worker_ids = [str(row.get("worker_id") or "") for row in snapshot["workers"] if row.get("worker_id")]
        install_ids = [str(row.get("install_id") or "") for row in snapshot["installations"] if row.get("install_id")]
        for worker_id in worker_ids:
            _delete_worker(app.state.linux_workers, worker_id)
        for install_id in install_ids:
            _delete_install(app.state.linux_worker_installs, install_id)
        return {"deleted": True, "workers_deleted": worker_ids, "installations_deleted": install_ids}

    @app.get(ASSET_PATH)
    async def linux_device_authority_asset() -> Response:
        return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_device_authority_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or response.status_code != 200:
            return response
        content_type = str(response.headers.get("content-type") or "")
        if "text/html" not in content_type:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        html = body.decode("utf-8", errors="replace")
        # v124 is the only Linux device presentation authority. Historical Linux
        # console scripts are removed from the delivered page instead of hidden,
        # so they cannot poll, render, relay actions, or make device decisions.
        html = LEGACY_LINUX_ASSET_RE.sub("", html)
        marker = f'<script src="{ASSET_PATH}?v={PATCH_REVISION}"></script>'
        html = html.replace("</body>", marker + "</body>") if "</body>" in html else html + marker
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["Cache-Control"] = "no-store"
        return Response(html, status_code=response.status_code, headers=headers, media_type="text/html")

    return app
