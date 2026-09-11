from __future__ import annotations

import asyncio
import copy
import logging
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .linux_worker_installs import code_hash


PATCH_REVISION = 124
MIN_SLOT = 2
MAX_SLOT = 32
ASSET_PATH = "/assets/chat2api-linux-device-authority-v124.js"
LEGACY_LINUX_ASSET_RE = re.compile(
    r'<script[^>]+src=["\']/assets/chat2api-linux-[^"\']+["\'][^>]*></script>',
    re.I,
)
logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_url(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    return f"{scheme}://{host}".rstrip("/")


def _install_meta(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return dict(meta)


def _is_v124(row: dict[str, Any]) -> bool:
    meta = _install_meta(row)
    try:
        return int(meta.get("device_authority_revision") or 0) == PATCH_REVISION
    except (TypeError, ValueError):
        return False


def _is_device_install(row: dict[str, Any]) -> bool:
    return _is_v124(row) and _install_meta(row).get("install_kind") == "device"


def _is_slot_install(row: dict[str, Any]) -> bool:
    return _is_v124(row) and _install_meta(row).get("install_kind") == "worker_slot"


def _device_id(row: dict[str, Any]) -> str:
    return str(_install_meta(row).get("device_id") or row.get("install_id") or "")


def _persist_install_fields(store: Any, install_id: str, **values: Any) -> dict[str, Any]:
    """Update one production LinuxWorkerInstallStore row without changing its code."""
    with store._lock:
        row = store.data.get("installs", {}).get(install_id)
        if row is None:
            raise KeyError(install_id)
        for key, value in values.items():
            if key == "metadata":
                current = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                row["metadata"] = {**current, **copy.deepcopy(value or {})}
            else:
                row[key] = copy.deepcopy(value)
        row["updated_at"] = _utc_iso()
        store._save()
        return store.admin_public(dict(row))


def _merge_worker_metadata(store: Any, worker_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    with store._lock:
        worker = store.data.get("workers", {}).get(worker_id)
        if worker is None:
            raise KeyError(worker_id)
        current = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
        worker["metadata"] = {**current, **copy.deepcopy(metadata)}
        store._save()
        return store.public(worker)


def _delete_install_and_enrollment(app: FastAPI, install_id: str) -> bool:
    installs = app.state.linux_worker_installs
    workers = app.state.linux_workers
    removed = installs.delete(install_id)
    with workers._lock:
        before = len(workers.data.get("enrollments", {}))
        workers.data["enrollments"] = {
            digest: item
            for digest, item in workers.data.get("enrollments", {}).items()
            if str(item.get("install_id") or "") != install_id
        }
        if len(workers.data["enrollments"]) != before:
            workers._save()
    return removed


def _delete_worker(store: Any, worker_id: str) -> bool:
    with store._lock:
        existed = worker_id in store.data.get("workers", {})
        if not existed:
            return False
        store.data["workers"].pop(worker_id, None)
        store.data["enrollments"] = {
            digest: item
            for digest, item in store.data.get("enrollments", {}).items()
            if str(item.get("worker_id") or "") != worker_id
        }
        store._save()
        return True


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
    installs = app.state.linux_worker_installs.list_admin()
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
        result.append(
            {
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
            }
        )
    result.sort(key=lambda row: str(row.get("install_created_at") or ""), reverse=True)
    return result


def _legacy_snapshot(app: FastAPI) -> dict[str, Any]:
    installs = app.state.linux_worker_installs.list_admin()
    workers = app.state.linux_workers.list_public()
    v124_worker_ids = {
        str(row.get("worker_id") or "")
        for row in installs
        if _is_v124(row) and row.get("worker_id")
    }
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
                "pairing_id": str(
                    ((row.get("metadata") or {}).get("worker_pairing") or {}).get("pairing_id")
                    or (row.get("metadata") or {}).get("device_pairing_id")
                    or ""
                ),
                "extension_client_id": str(row.get("extension_client_id") or ""),
            }
            for row in legacy_workers
        ],
        "installations": [
            {"install_id": str(row.get("install_id") or ""), "name": str(row.get("name") or "Linux Worker")}
            for row in legacy_installs
        ],
    }


async def _release_pairing(app: FastAPI, worker: dict[str, Any]) -> None:
    pairing_id = str(worker.get("pairing_id") or "")
    if not pairing_id:
        return
    pairings = app.state.pairings
    await pairings.ensure_loaded()
    async with pairings.lock:
        item = pairings.items.get(pairing_id)
        if item:
            client_id = str(worker.get("extension_client_id") or "")
            if not client_id or not item.bound_client_id or item.bound_client_id == client_id:
                item.bound_client_id = None
                item.bound_device_id = None
                await pairings.save()


def install_linux_worker_device_authority_v124_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_device_authority_v124_installed", False):
        return app
    app.state.linux_worker_device_authority_v124_installed = True

    script = (Path(__file__).with_name("admin_linux_device_authority_v124.js")).read_text(encoding="utf-8")
    workers = app.state.linux_workers
    installs = app.state.linux_worker_installs
    base_enroll = workers.enroll

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    async def setup_options() -> dict[str, Any]:
        await app.state.pairings.ensure_loaded()
        pairings = app.state.pairings.list_public()
        proxies = app.state.linux_worker_proxy_catalog.list()
        return {
            "pairing_codes": [
                {
                    "pairing_id": str(item.get("pairing_id") or ""),
                    "device_name": str(item.get("name") or "未命名设备"),
                    "prefix": str(item.get("prefix") or ""),
                    "enabled": bool(item.get("enabled", True)),
                    "paired": bool(item.get("bound_client_id")),
                }
                for item in pairings
                if isinstance(item, dict)
            ],
            "proxies": proxies,
        }

    def create_install(
        request: Request,
        *,
        pairing: dict[str, Any],
        pairing_code: str,
        proxy: dict[str, Any],
        slot: int,
        parent_device_id: str = "",
    ) -> dict[str, Any]:
        device_name = str(pairing.get("device_name") or "Linux 设备").strip()[:80] or "Linux 设备"
        worker_name = device_name if slot == 1 else f"{device_name} · Worker {slot}"
        install = installs.create(worker_name)
        device_id = parent_device_id or str(install.get("install_id") or "")
        metadata = {
            "device_authority_revision": PATCH_REVISION,
            "device_id": device_id,
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
        server = _server_url(request)
        code = str(install.get("code") or "")
        if slot == 1:
            command = (
                f"curl -fsSL {shlex.quote(server + '/bootstrap/linux-worker.sh')} | "
                f"sudo bash -s -- --server {shlex.quote(server)} --enroll-code {shlex.quote(code)} "
                f"--pairing-code {shlex.quote(pairing_code)} --device-name {shlex.quote(device_name)}"
            )
        else:
            command = (
                "sudo bash /opt/chat2api-worker/scripts/linux_worker_slot_install_reported.sh "
                f"--server {shlex.quote(server)} --enroll-code {shlex.quote(code)} --slot {slot} "
                f"--pairing-code {shlex.quote(pairing_code)} --device-name {shlex.quote(device_name)}"
            )

        _persist_install_fields(installs, str(install["install_id"]), metadata=metadata, install_command=command)
        digest = code_hash(code.strip().upper())
        with workers._lock:
            workers.data.setdefault("enrollments", {})[digest] = {
                "code_hash": digest,
                "name": worker_name,
                "created_at": install.get("created_at") or _utc_iso(),
                "expires_at": "9999-12-31T23:59:59Z",
                "used_at": None,
                "install_id": str(install.get("install_id") or ""),
                "metadata": copy.deepcopy(metadata),
            }
            workers._save()
        row = installs.get(str(install["install_id"])) or {}
        return {"install": row, "install_command": command, "device_id": device_id, **metadata}

    def enroll_with_device_authority(code: str, facts: dict[str, Any]) -> dict[str, str]:
        digest = code_hash(str(code).strip().upper())
        with workers._lock:
            pending = copy.deepcopy(workers.data.get("enrollments", {}).get(digest) or {})
        enroll_meta = pending.get("metadata") if isinstance(pending.get("metadata"), dict) else {}
        result = base_enroll(code, facts)
        worker_id = str(result.get("worker_id") or "")
        if int(enroll_meta.get("device_authority_revision") or 0) != PATCH_REVISION:
            return result

        slot = max(1, int(enroll_meta.get("worker_slot") or 1))
        pairing_id = str(enroll_meta.get("pairing_id") or "")
        pairing_name = str(enroll_meta.get("device_name") or "Linux 设备")
        safe_meta = {
            "device_authority_revision": PATCH_REVISION,
            "device_id_v124": str(enroll_meta.get("device_id") or ""),
            "device_name": pairing_name,
            "device_pairing_id": pairing_id,
            "device_pairing_prefix": str(enroll_meta.get("pairing_prefix") or ""),
            "worker_slot": slot,
            "proxy_catalog_id": str(enroll_meta.get("proxy_id") or ""),
            "proxy_catalog_name": str(enroll_meta.get("proxy_name") or ""),
            "worker_pairing": {
                "pairing_id": pairing_id,
                "name": pairing_name,
                "prefix": str(enroll_meta.get("pairing_prefix") or ""),
                "status": "pending",
                "bound_client_id": None,
                "bound_at": None,
                "last_error": "",
            },
        }
        with workers._lock:
            live = workers.data.get("workers", {}).get(worker_id)
            if live:
                current = live.get("metadata") if isinstance(live.get("metadata"), dict) else {}
                live["metadata"] = {**current, **safe_meta}
                live["worker_pairing_state"] = "pending"
                workers._save()
        install_id = str(pending.get("install_id") or "")
        if install_id:
            try:
                installs.link_worker(code, worker_id)
            except ValueError:
                logger.exception("v124 could not link install to Worker install_id=%s worker_id=%s", install_id, worker_id)

        share_link = str(enroll_meta.get("proxy_share_link") or "").strip()
        proxy_name = str(enroll_meta.get("proxy_name") or "").strip()
        if share_link:
            async def apply_proxy_when_connected() -> None:
                for _ in range(45):
                    await asyncio.sleep(2.0)
                    try:
                        command = await app.state.send_linux_worker_command(
                            worker_id,
                            "apply_proxy_config",
                            {"share_link": share_link},
                            wait=True,
                            timeout=90,
                        )
                    except HTTPException as exc:
                        if exc.status_code == 409:
                            continue
                        logger.warning("v124 proxy apply failed worker_id=%s status=%s", worker_id, exc.status_code)
                        return
                    except Exception:
                        logger.exception("v124 proxy apply failed worker_id=%s", worker_id)
                        return
                    result_payload = command.get("result") if isinstance(command.get("result"), dict) else {}
                    if result_payload.get("ok"):
                        summary = result_payload.get("proxy") if isinstance(result_payload.get("proxy"), dict) else {}
                        try:
                            workers.record_proxy_success(worker_id, summary)
                            _merge_worker_metadata(workers, worker_id, {"proxy_catalog_name": proxy_name})
                        except Exception:
                            logger.exception("v124 proxy metadata update failed worker_id=%s", worker_id)
                        return
                    logger.warning("v124 proxy apply returned failure worker_id=%s error=%s", worker_id, result_payload.get("error"))
                    return
                logger.warning("v124 proxy apply timed out waiting for Worker websocket worker_id=%s", worker_id)

            try:
                asyncio.get_running_loop().create_task(apply_proxy_when_connected())
            except RuntimeError:
                pass
        return result

    workers.enroll = enroll_with_device_authority

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
        pairing_code, _ = await app.state.pairings.reveal_or_rotate(pairing_id)
        return create_install(request, pairing=pairing, pairing_code=pairing_code, proxy=proxy, slot=1)

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
        pairing_code, _ = await app.state.pairings.reveal_or_rotate(pairing_id)
        return create_install(
            request,
            pairing=pairing,
            pairing_code=pairing_code,
            proxy=proxy,
            slot=slot,
            parent_device_id=device_id,
        )

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
            _delete_worker(workers, worker_id)
        for install_id in install_ids:
            if install_id:
                _delete_install_and_enrollment(app, install_id)
        return {"deleted": True, "device_id": device_id, "workers_deleted": worker_ids, "installations_deleted": install_ids}

    @app.get("/api/admin/linux-legacy-records")
    async def list_legacy_linux_records(request: Request) -> dict[str, Any]:
        admin(request)
        snapshot = _legacy_snapshot(app)
        return {**snapshot, "worker_count": len(snapshot["workers"]), "installation_count": len(snapshot["installations"])}

    @app.delete("/api/admin/linux-legacy-records")
    async def purge_legacy_linux_records(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        if str(body.get("confirm") or "") != "PURGE_LEGACY_LINUX":
            raise HTTPException(400, "需要明确确认清理旧 Linux 数据")
        snapshot = _legacy_snapshot(app)
        online = [row for row in snapshot["workers"] if row.get("online")]
        if online:
            raise HTTPException(409, "仍有旧 Linux Worker 在线，先在目标主机卸载后再清理")
        for worker in snapshot["workers"]:
            await _release_pairing(app, worker)
        worker_ids = [str(row.get("worker_id") or "") for row in snapshot["workers"] if row.get("worker_id")]
        install_ids = [str(row.get("install_id") or "") for row in snapshot["installations"] if row.get("install_id")]
        for worker_id in worker_ids:
            _delete_worker(workers, worker_id)
        for install_id in install_ids:
            _delete_install_and_enrollment(app, install_id)
        return {"deleted": True, "workers_deleted": worker_ids, "installations_deleted": install_ids}

    @app.get(ASSET_PATH, include_in_schema=False)
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
        html = LEGACY_LINUX_ASSET_RE.sub("", html)
        marker = f'<script src="{ASSET_PATH}?v={PATCH_REVISION}"></script>'
        if marker not in html:
            html = html.replace("</body>", marker + "</body>") if "</body>" in html else html + marker
        headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(html, status_code=response.status_code, headers=headers, media_type="text/html")

    logger.info("Linux device authority v124 installed as sole Linux console owner")
    return app
