from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE
from .linux_worker_installs import code_hash


PATCH_REVISION = 122
ASSET_PATH = "/assets/chat2api-linux-device-workers-v122.js"
ACTIVE_EXPIRES_AT = "9999-12-31T23:59:59Z"
MIN_SLOT = 2
MAX_SLOT = 32


class SlotCreate(BaseModel):
    slot: int | None = Field(default=None, ge=MIN_SLOT, le=MAX_SLOT)
    name: str | None = Field(default=None, max_length=80)


def _physical_key(worker: dict[str, Any]) -> str:
    platform = str(worker.get("platform") or "linux").strip().lower() or "linux"
    identity = str(worker.get("device_id") or worker.get("hostname") or worker.get("worker_id") or "").strip()
    return f"{platform}:{identity}"


def _slot_number(value: Any) -> int | None:
    text = str(value or "").strip().lower()
    if text.startswith("slot"):
        text = text[4:]
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= MAX_SLOT else None


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


def install_linux_worker_device_console_v122_patch(app: FastAPI) -> FastAPI:
    """Present Linux hosts as devices while keeping each browser Worker independent.

    A physical Linux device is one host identity. Worker 1 is the primary install;
    Worker 2..32 are isolated same-host slots with their own Chrome profile,
    extension identity, proxy/CDP resources and ChatGPT login state. The device
    list therefore renders one row per host and exposes the logical Workers from a
    dedicated manager instead of pretending every Worker is a separate machine.
    """
    if getattr(app.state, "linux_worker_device_console_v122_installed", False):
        return app
    app.state.linux_worker_device_console_v122_installed = True

    workers = app.state.linux_workers
    installs = app.state.linux_worker_installs
    sessions = app.state.admin_sessions

    def admin(request: Request) -> None:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")

    def server_url(request: Request) -> str:
        return app.state.settings.resolved_public_url(str(request.base_url)).rstrip("/")

    def slot_command(item: dict[str, Any], request: Request) -> str:
        slot = _slot_number(item.get("worker_slot"))
        if slot is None:
            return ""
        return (
            "sudo bash /opt/chat2api-worker/scripts/linux_worker_slot_install_reported.sh "
            f"--server {server_url(request)} --enroll-code {item['code']} --slot {slot}"
        )

    def install_meta() -> dict[str, dict[str, Any]]:
        return {str(item.get("install_id") or ""): item for item in installs.list_admin()}

    def public_workers() -> list[dict[str, Any]]:
        return [row for row in workers.list_public() if not row.get("revoked_at")]

    def group_workers() -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for worker in public_workers():
            groups.setdefault(_physical_key(worker), []).append(worker)
        for group in groups.values():
            group.sort(key=lambda row: str(row.get("created_at") or ""))
        return groups

    def slot_install_by_worker(meta: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in meta.values():
            if str(item.get("install_kind") or "") != "worker_slot":
                continue
            worker_id = str(item.get("worker_id") or "")
            if worker_id:
                result[worker_id] = item
        return result

    def decorated_group(
        group: list[dict[str, Any]],
        meta: dict[str, dict[str, Any]],
        request: Request,
    ) -> list[dict[str, Any]]:
        by_worker = slot_install_by_worker(meta)
        used: set[int] = set()
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(group):
            row = dict(raw)
            install = by_worker.get(str(row.get("worker_id") or ""), {})
            slot = _slot_number(row.get("worker_slot")) or _slot_number(install.get("worker_slot"))
            if slot is None:
                slot = 1 if index == 0 else next((n for n in range(2, MAX_SLOT + 1) if n not in used), index + 1)
            used.add(slot)
            row["worker_slot"] = slot
            row["worker_label"] = f"Worker {slot}"
            row["slot_install_id"] = install.get("install_id") or None
            result.append(row)
        result.sort(key=lambda row: int(row.get("worker_slot") or MAX_SLOT + 1))
        return result

    def group_rows(rows: list[dict[str, Any]], request: Request) -> list[dict[str, Any]]:
        meta = install_meta()
        groups = group_workers()
        slot_install_ids = {
            install_id
            for install_id, item in meta.items()
            if str(item.get("install_kind") or "") == "worker_slot"
        }
        slot_worker_ids = {
            str(item.get("worker_id") or "")
            for item in meta.values()
            if str(item.get("install_kind") or "") == "worker_slot" and item.get("worker_id")
        }
        rendered_keys: set[str] = set()
        output: list[dict[str, Any]] = []

        for raw in rows:
            row = dict(raw)
            install_id = str(row.get("install_id") or "")
            worker_id = str(row.get("worker_id") or "")
            if install_id in slot_install_ids or worker_id in slot_worker_ids:
                continue

            worker = workers.data.get("workers", {}).get(worker_id) if worker_id else None
            key = _physical_key(worker) if isinstance(worker, dict) else f"pending:{install_id or id(raw)}"
            if worker_id and key in rendered_keys:
                continue
            if worker_id:
                rendered_keys.add(key)

            group = groups.get(key, []) if worker_id else []
            logical_workers = decorated_group(group, meta, request) if group else []
            group_ids = {str(item.get("worker_id") or "") for item in group}
            primary_id = str((logical_workers[0] if logical_workers else row).get("worker_id") or worker_id)

            slot_installs: list[dict[str, Any]] = []
            for item in meta.values():
                if str(item.get("install_kind") or "") != "worker_slot":
                    continue
                parent_id = str(item.get("parent_worker_id") or "")
                parent_key = str(item.get("parent_device_key") or "")
                if not ((parent_id and parent_id in group_ids) or (parent_key and parent_key == key)):
                    continue
                value = dict(item)
                value["worker_slot"] = _slot_number(value.get("worker_slot"))
                value["install_command"] = slot_command(value, request)
                if value.get("worker_id") and str(value.get("state") or "") not in {"installed", "failed"}:
                    value["state"] = "installed"
                    value["stage"] = "complete"
                    value["message"] = "同机 Worker 已安装并注册"
                    value["enabled"] = False
                slot_installs.append(value)
            slot_installs.sort(key=lambda item: int(item.get("worker_slot") or MAX_SLOT + 1))

            used_slots = {int(item.get("worker_slot") or 0) for item in logical_workers}
            used_slots.update(int(item.get("worker_slot") or 0) for item in slot_installs if item.get("worker_slot"))
            next_slot = next((slot for slot in range(MIN_SLOT, MAX_SLOT + 1) if slot not in used_slots), None)

            row["device_key"] = key
            row["device_workers"] = logical_workers
            row["worker_count"] = len(logical_workers)
            row["slot_installations"] = slot_installs
            row["next_worker_slot"] = next_slot
            row["primary_worker_id"] = primary_id or None
            output.append(row)
        return output

    # The historical store intentionally ignores unknown enrollment facts. Keep
    # the generic contract stable and persist only the validated slot number after
    # the normal enrollment has succeeded.
    if not getattr(workers, "_chat2api_same_host_slot_persistence_v122", False):
        base_enroll = workers.enroll

        def enroll_with_slot(code: str, facts: dict[str, Any]) -> dict[str, str]:
            credentials = base_enroll(code, facts)
            slot = _slot_number(facts.get("worker_slot"))
            if slot is not None:
                with workers._lock:
                    worker = workers.data.get("workers", {}).get(str(credentials.get("worker_id") or ""))
                    if worker is not None:
                        worker["worker_slot"] = slot
                        workers._save()
            return credentials

        workers.enroll = enroll_with_slot
        workers._chat2api_same_host_slot_persistence_v122 = True

    @app.post("/api/admin/linux-workers/{worker_id}/slots")
    async def create_worker_slot(worker_id: str, body: SlotCreate, request: Request) -> dict[str, Any]:
        admin(request)
        source = workers.data.get("workers", {}).get(worker_id)
        if not source or source.get("revoked_at"):
            raise HTTPException(status_code=404, detail="设备 Worker 不存在")
        key = _physical_key(source)
        group = group_workers().get(key, [])
        if not group:
            raise HTTPException(status_code=409, detail="设备当前没有可用 Worker")
        primary = group[0]
        primary_id = str(primary.get("worker_id") or worker_id)

        meta = install_meta()
        by_worker = slot_install_by_worker(meta)
        used: set[int] = set()
        for index, worker in enumerate(group):
            install = by_worker.get(str(worker.get("worker_id") or ""), {})
            slot = _slot_number(worker.get("worker_slot")) or _slot_number(install.get("worker_slot")) or (1 if index == 0 else None)
            if slot:
                used.add(slot)
        for item in meta.values():
            if str(item.get("install_kind") or "") != "worker_slot":
                continue
            if str(item.get("parent_device_key") or "") != key:
                continue
            slot = _slot_number(item.get("worker_slot"))
            if slot and str(item.get("state") or "") not in {"failed", "disabled"}:
                used.add(slot)

        slot = int(body.slot) if body.slot is not None else next((n for n in range(MIN_SLOT, MAX_SLOT + 1) if n not in used), 0)
        if slot < MIN_SLOT or slot > MAX_SLOT:
            raise HTTPException(status_code=409, detail="此设备已经没有可用的 Worker Slot")
        if slot in used:
            raise HTTPException(status_code=409, detail=f"Worker {slot} 已存在或已有待执行安装命令")

        device_name = str(primary.get("name") or source.get("name") or source.get("hostname") or "Linux 设备").strip()
        for item in installs.list_admin():
            if str(item.get("worker_id") or "") == primary_id and str(item.get("install_kind") or "device") != "worker_slot":
                device_name = str(item.get("name") or device_name).strip() or device_name
                break
        worker_name = str(body.name or "").strip() or f"{device_name} · Worker {slot}"
        item = installs.create(worker_name)
        install_id = str(item["install_id"])
        with installs._lock:
            stored = installs.data["installs"][install_id]
            stored.update({
                "install_kind": "worker_slot",
                "parent_worker_id": primary_id,
                "parent_device_key": key,
                "worker_slot": slot,
                "message": f"等待在设备 {device_name} 执行 Worker {slot} 安装命令",
            })
            installs._save()
            item = installs.admin_public(dict(stored))

        digest = code_hash(str(item.get("code") or "").strip().upper())
        with workers._lock:
            workers.data["enrollments"][digest] = {
                "code_hash": digest,
                "name": worker_name,
                "created_at": item["created_at"],
                "expires_at": ACTIVE_EXPIRES_AT,
                "used_at": None,
                "install_id": install_id,
                "worker_slot": slot,
                "parent_worker_id": primary_id,
                "parent_device_key": key,
            }
            workers._save()

        return {
            **item,
            "device_name": device_name,
            "parent_worker_id": primary_id,
            "worker_slot": slot,
            "install_command": slot_command(item, request),
            "revision": PATCH_REVISION,
        }

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_device_workers_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_device_workers_v122.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_device_console_v122(request: Request, call_next: Callable):
        response = await call_next(request)

        if request.method == "GET" and request.url.path == "/api/admin/linux-worker-installations" and response.status_code == 200:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return response
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                payload["data"] = group_rows(payload["data"], request)
                payload["device_list"] = True
                payload["revision"] = PATCH_REVISION
                headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
                return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if request.url.path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ASSET_PATH}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    app.state.linux_worker_device_console_v122 = {
        "revision": PATCH_REVISION,
        "group_rows": group_rows,
        "physical_key": _physical_key,
    }
    return app
