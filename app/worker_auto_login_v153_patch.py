"""Encrypted per-Worker ChatGPT login profiles and login-recovery dispatch.

Login secrets never enter Worker public metadata, API responses, or logs. A paired
Chrome extension receives a profile only over its authenticated websocket.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


ASSET_PATH = "/assets/chat2api-unified-workers-v153.js"
BASE32 = re.compile(r"^[A-Z2-7]+=*$")
COOLDOWN_SECONDS = 300


def normalize_totp(value: str) -> str:
    value = re.sub(r"\s|-", "", str(value or "")).upper()
    if not value:
        return ""
    if not (16 <= len(value) <= 128) or not BASE32.fullmatch(value):
        raise ValueError("验证器密钥应为 Base32 字符串（至少 16 位）")
    try:
        if len(base64.b32decode(value + "=" * (-len(value) % 8), casefold=True)) < 10:
            raise ValueError("验证器密钥长度不足")
    except Exception as exc:
        raise ValueError("验证器密钥不是有效的 Base32") from exc
    return value.rstrip("=")


def totp_code(seed: str, now: float | None = None) -> str:
    raw = base64.b32decode(normalize_totp(seed) + "=" * (-len(normalize_totp(seed)) % 8))
    counter = int(time.time() if now is None else now) // 30
    digest = hmac.new(raw, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff) % 1000000).zfill(6)


class WorkerLoginVault:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "worker_login_profiles_v153.json"
        self.key_path = data_dir / "worker_login_key_v153"
        self.lock = threading.RLock()
        self.crypto = Fernet(self._key())
        self.entries: dict[str, str] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = {str(k): str(v) for k, v in data.get("profiles", {}).items()}

    def _key(self) -> bytes:
        configured = os.environ.get("CHAT2API_WORKER_LOGIN_KEY", "").strip()
        if configured:
            try:
                Fernet(configured.encode("ascii"))
                return configured.encode("ascii")
            except (ValueError, TypeError) as exc:
                raise RuntimeError("CHAT2API_WORKER_LOGIN_KEY must be a Fernet key") from exc
        # Store a random key separately from the encrypted profiles. Both files
        # must be backed up together; operators may instead supply an env key.
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.key_path.read_bytes().strip()
        with os.fdopen(fd, "wb") as stream:
            key = Fernet.generate_key()
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
            return key

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "profiles": self.entries}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def get(self, worker_id: str) -> dict[str, Any] | None:
        with self.lock:
            encrypted = self.entries.get(worker_id)
            if not encrypted:
                return None
            try:
                return json.loads(self.crypto.decrypt(encrypted.encode("ascii")))
            except (InvalidToken, ValueError) as exc:
                raise RuntimeError("Worker login vault key mismatch") from exc

    def put(self, worker_id: str, profile: dict[str, Any]) -> None:
        with self.lock:
            self.entries[worker_id] = self.crypto.encrypt(json.dumps(profile).encode("utf-8")).decode("ascii")
            self._save()

    def remove(self, worker_id: str) -> None:
        with self.lock:
            self.entries.pop(worker_id, None)
            self._save()

    def public(self, worker_id: str) -> dict[str, Any]:
        profile = self.get(worker_id)
        return {
            "worker_id": worker_id,
            "configured": profile is not None,
            "enabled": bool(profile and profile.get("enabled")),
            "username": str(profile.get("username") or "") if profile else "",
            "has_password": bool(profile and profile.get("password")),
            "has_totp": bool(profile and profile.get("totp_secret")),
        }


def install_worker_auto_login_v153_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "worker_auto_login_v153", False):
        return app
    app.state.worker_auto_login_v153 = True
    vault = WorkerLoginVault(Path(app.state.settings.data_dir))
    app.state.worker_login_vault = vault
    registry = app.state.registry
    linux = app.state.linux_workers
    attempts: dict[str, dict[str, Any]] = {}
    observations: dict[str, int] = {}
    lock = asyncio.Lock()

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def extension_for(worker_id: str) -> str:
        if worker_id in linux.data.get("workers", {}):
            worker = linux.data["workers"][worker_id]
            if worker.get("revoked_at"):
                raise HTTPException(409, "Linux Worker 已禁用")
            client_id = str(worker.get("extension_client_id") or "")
            if not client_id:
                raise HTTPException(409, "Linux Worker 的 Chrome Bridge 尚未绑定；首次请先手工登录完成绑定")
            return client_id
        client = registry.clients.get(worker_id)
        if not client:
            raise HTTPException(404, "Worker ID 不存在")
        if not client.connection_enabled:
            raise HTTPException(409, "Worker 已断开连接")
        if str(client.metadata.get("linux_worker_id") or ""):
            raise HTTPException(409, "Linux Worker 请使用设备列表中的 Worker ID 进行配置")
        return worker_id

    def known_worker(worker_id: str) -> None:
        if worker_id in linux.data.get("workers", {}):
            return
        if worker_id in registry.clients and not registry.clients[worker_id].metadata.get("linux_worker_id"):
            return
        raise HTTPException(404, "Worker ID 不存在或已归属 Linux Worker")

    def view(worker_id: str) -> dict[str, Any]:
        return {**vault.public(worker_id), "runtime": attempts.get(worker_id, {}).get("status", "idle"),
                "last_attempt_at": attempts.get(worker_id, {}).get("at")}

    async def dispatch(worker_id: str, *, forced: bool = False) -> dict[str, Any]:
        profile = vault.get(worker_id)
        if not profile or not profile.get("enabled"):
            raise HTTPException(409, "请先启用 Worker 自动登录并配置账户")
        client_id = extension_for(worker_id)
        if client_id not in registry.online_client_ids():
            raise HTTPException(409, "Worker Chrome Bridge 当前离线")
        async with lock:
            now = time.monotonic()
            latest = attempts.get(worker_id, {})
            if not forced and now - float(latest.get("monotonic") or 0) < COOLDOWN_SECONDS:
                return {"queued": False, "reason": "cooldown", "status": latest.get("status", "cooldown")}
            attempts[worker_id] = {"monotonic": now, "at": int(time.time()), "status": "starting"}
            # We deliberately DO NOT forward third-party credentials or TOTP
            # secrets over this channel. Recovery opens the login surface and
            # requires the operator to complete authentication.
            try:
                await registry.send(client_id, {
                    "type": "worker.login.open.v153",
                    "worker_id": worker_id,
                })
            except Exception:
                attempts[worker_id]["status"] = "offline"
                raise HTTPException(503, "自动登录指令发送失败，请检查 Worker 连接") from None
            attempts[worker_id]["status"] = "manual_required"
            return {"queued": True, "status": "manual_required"}

    @app.get("/api/admin/worker-login")
    async def list_worker_logins(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": [view(i) for i in vault.entries]}

    @app.get("/api/admin/worker-login/{worker_id}")
    async def get_worker_login(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        known_worker(worker_id)
        return view(worker_id)

    @app.get("/api/admin/worker-login/{worker_id}/totp")
    async def get_worker_totp(worker_id: str, request: Request) -> Response:
        admin(request)
        known_worker(worker_id)
        profile = vault.get(worker_id)
        if not profile or not profile.get("totp_secret"):
            raise HTTPException(409, "请先保存此 Worker 的验证器密钥")
        now = time.time()
        payload = {"code": totp_code(profile["totp_secret"], now), "valid_for_seconds": 30 - int(now) % 30}
        return Response(json.dumps(payload), media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.put("/api/admin/worker-login/{worker_id}")
    async def save_worker_login(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        known_worker(worker_id)
        body = await request.json()
        previous = vault.get(worker_id) or {}
        username = str(body.get("username") or previous.get("username") or "").strip()
        password = str(body.get("password") or previous.get("password") or "")
        raw_totp = body.get("totp_secret")
        try:
            seed = normalize_totp(raw_totp) if raw_totp is not None else str(previous.get("totp_secret") or "")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not username or len(username) > 254 or len(password) < 1 or len(password) > 1024:
            raise HTTPException(422, "请输入有效的账户邮箱和密码")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", username):
            raise HTTPException(422, "账户必须是有效邮箱")
        vault.put(worker_id, {"username": username, "password": password,
                              "totp_secret": seed, "enabled": body.get("enabled") is not False})
        attempts.pop(worker_id, None)
        return view(worker_id)

    @app.delete("/api/admin/worker-login/{worker_id}")
    async def delete_worker_login(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        vault.remove(worker_id)
        attempts.pop(worker_id, None)
        return {"deleted": True}

    @app.post("/api/admin/worker-login/{worker_id}/trigger")
    async def trigger_worker_login(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        return await dispatch(worker_id, forced=True)

    previous_touch = registry.touch

    async def touch_with_relogin(client_id: str, metadata: dict[str, Any] | None = None) -> None:
        await previous_touch(client_id, metadata)
        if not isinstance(metadata, dict):
            return
        worker = linux.worker_for_extension(client_id)
        worker_id = str(worker.get("worker_id") or "") if worker else client_id
        state = str(metadata.get("chatgpt_login_state") or "").lower()
        if state == "ready" and metadata.get("chatgpt_login_composer_ready") is True:
            observations.pop(worker_id, None)
            if worker_id in attempts:
                attempts[worker_id]["status"] = "logged_in"
            return
        checked_at = int(metadata.get("chatgpt_login_checked_at_ms") or 0)
        fresh = 0 <= time.time() * 1000 - checked_at <= 45000
        confidence = str(metadata.get("chatgpt_login_confidence") or "").lower()
        if not fresh or state != "login_required" or confidence not in {"high", "medium"}:
            observations.pop(worker_id, None)
            return
        observations[worker_id] = observations.get(worker_id, 0) + 1
        if observations[worker_id] < 2:
            return
        if not vault.get(worker_id) or not vault.get(worker_id).get("enabled"):
            return
        try:
            await dispatch(worker_id)
        except (HTTPException, RuntimeError):
            pass

    registry.touch = touch_with_relogin

    @app.get(ASSET_PATH, include_in_schema=False)
    async def unified_worker_asset() -> Response:
        script = Path(__file__).with_name("admin_unified_workers_v153.js").read_text(encoding="utf-8")
        return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def inject_unified_worker_console(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
            return response
        content = b""
        async for chunk in response.body_iterator:
            content += chunk
        html = content.decode("utf-8", errors="replace")
        marker = '<script src="/assets/chat2api-unified-workers-v153.js?v=153"></script>'
        if marker not in html:
            html = html.replace("</body>", marker + "</body>")
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store"
        return Response(html, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
