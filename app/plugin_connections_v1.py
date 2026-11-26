"""Per-user OAuth connections and bounded read-only tools for Web Chat plugins.

Provider endpoints are fixed in code. OAuth client secrets and user tokens are
encrypted with the installation data secret and are never returned to browsers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .admin_audit import AdminAuditError, AdminAuditStore
from .admin_auth import SESSION_COOKIE, same_origin_admin_request
from .api_keys import _fernet, load_or_create_data_secret
from .private_storage import secure_private_sqlite_files
from .user_commerce import USER_SESSION_COOKIE


_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_GMAIL_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

PROVIDERS: dict[str, dict[str, Any]] = {
    "github": {
        "name": "GitHub",
        "description": "搜索仓库、查看仓库信息和 Issues。仅开放读取操作。",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "api_base": "https://api.github.com",
    },
    "gmail": {
        "name": "Gmail",
        "description": "搜索邮件并查看邮件摘要。仅开放读取操作。",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "api_base": "https://gmail.googleapis.com/gmail/v1",
    },
}


class PluginConnectionError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = []
    return sorted({item.strip() for item in values if item and len(item) <= 256})


class PluginOAuthStore:
    """Private SQLite store for provider config, one-time OAuth state and user tokens."""

    def __init__(self, data_dir: Path | str) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "plugin_oauth_v1.sqlite3"
        self.lock = threading.RLock()
        self.cipher = _fernet(load_or_create_data_secret(root))
        if self.cipher is None:
            raise RuntimeError("Plugin credential encryption is unavailable")
        secure_private_sqlite_files(self.path, create_database=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS plugin_providers (
              provider_id TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL,
              client_id TEXT NOT NULL,
              client_secret_cipher TEXT NOT NULL,
              private_repos INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plugin_oauth_states (
              state_hash TEXT PRIMARY KEY,
              provider_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              verifier_cipher TEXT NOT NULL,
              expires_at INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS plugin_oauth_states_expiry
              ON plugin_oauth_states(expires_at);
            CREATE TABLE IF NOT EXISTS plugin_user_connections (
              user_id TEXT NOT NULL,
              provider_id TEXT NOT NULL,
              provider_subject TEXT NOT NULL,
              account_label TEXT NOT NULL,
              access_token_cipher TEXT NOT NULL,
              refresh_token_cipher TEXT NOT NULL DEFAULT '',
              scopes_json TEXT NOT NULL,
              expires_at INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, provider_id)
            );
        """)
        self.db.commit()
        secure_private_sqlite_files(self.path)

    def _encrypt(self, value: str) -> str:
        return self.cipher.encrypt(str(value).encode("utf-8")).decode("ascii") if value else ""

    def _decrypt(self, value: str) -> str:
        if not value:
            return ""
        return self.cipher.decrypt(str(value).encode("ascii")).decode("utf-8")

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM plugin_providers WHERE provider_id=?", (provider_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "provider_id": row["provider_id"],
            "enabled": bool(row["enabled"]),
            "client_id": row["client_id"],
            "client_secret": self._decrypt(row["client_secret_cipher"]),
            "private_repos": bool(row["private_repos"]),
            "updated_at": row["updated_at"],
        }

    def save_provider(
        self, provider_id: str, *, enabled: bool, client_id: str,
        client_secret: str, private_repos: bool = False,
    ) -> dict[str, Any]:
        if provider_id not in PROVIDERS:
            raise PluginConnectionError("Unsupported plugin provider")
        previous = self.provider(provider_id) or {}
        requested_client_id = str(client_id or "").strip()
        requested_secret = str(client_secret or "").strip()
        if (
            requested_client_id and requested_client_id != str(previous.get("client_id") or "")
            and not requested_secret
        ):
            raise PluginConnectionError("更换 OAuth Client ID 时必须同时填写新的 Client Secret")
        client_id = requested_client_id or str(previous.get("client_id") or "").strip()
        client_secret = requested_secret or str(previous.get("client_secret") or "").strip()
        if len(client_id) > 256 or len(client_secret) > 2048:
            raise PluginConnectionError("OAuth credentials exceed the allowed length")
        if enabled and (not client_id or not client_secret):
            raise PluginConnectionError("启用插件前必须配置 OAuth Client ID 和 Client Secret")
        if provider_id == "gmail":
            private_repos = False
        now = _now_iso()
        with self.lock:
            with self.db:
                self.db.execute("""
                    INSERT INTO plugin_providers
                      (provider_id, enabled, client_id, client_secret_cipher, private_repos, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                      enabled=excluded.enabled, client_id=excluded.client_id,
                      client_secret_cipher=excluded.client_secret_cipher,
                      private_repos=excluded.private_repos, updated_at=excluded.updated_at
                """, (
                    provider_id, int(bool(enabled)), client_id, self._encrypt(client_secret),
                    int(bool(private_repos)), now,
                ))
            secure_private_sqlite_files(self.path)
        return self.provider(provider_id) or {}

    def public_provider(self, provider_id: str, redirect_uri: str) -> dict[str, Any]:
        spec = PROVIDERS[provider_id]
        config = self.provider(provider_id) or {}
        scopes = (
            ["read:user", "repo"] if config.get("private_repos")
            else ["read:user"]
        ) if provider_id == "github" else [
            "openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"
        ]
        return {
            "id": provider_id,
            "name": spec["name"],
            "description": spec["description"],
            "enabled": bool(config.get("enabled")),
            "configured": bool(config.get("client_id") and config.get("client_secret")),
            "client_id": str(config.get("client_id") or ""),
            "client_secret_configured": bool(config.get("client_secret")),
            "private_repos": bool(config.get("private_repos")),
            "redirect_uri": redirect_uri,
            "scopes": scopes,
        }

    def public_catalog(self) -> list[dict[str, Any]]:
        rows = []
        for provider_id, spec in PROVIDERS.items():
            config = self.provider(provider_id) or {}
            if not config.get("enabled") or not config.get("client_id") or not config.get("client_secret"):
                continue
            rows.append({
                "id": provider_id, "name": spec["name"],
                "description": spec["description"], "enabled": True,
            })
        return rows

    def create_state(self, provider_id: str, user_id: str, verifier: str) -> str:
        state = secrets.token_urlsafe(32)
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        now = int(time.time())
        with self.lock:
            with self.db:
                self.db.execute(
                    "DELETE FROM plugin_oauth_states WHERE expires_at < ?", (now,)
                )
                self.db.execute("""
                    INSERT INTO plugin_oauth_states
                      (state_hash, provider_id, user_id, verifier_cipher, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    state_hash, provider_id, user_id, self._encrypt(verifier),
                    now + 600, _now_iso(),
                ))
            secure_private_sqlite_files(self.path)
        return state

    def consume_state(self, state: str, provider_id: str, user_id: str) -> str | None:
        if not state or len(state) > 256:
            return None
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        now = int(time.time())
        with self.lock:
            with self.db:
                row = self.db.execute(
                    "SELECT * FROM plugin_oauth_states WHERE state_hash=?", (state_hash,)
                ).fetchone()
                self.db.execute(
                    "DELETE FROM plugin_oauth_states WHERE state_hash=?", (state_hash,)
                )
            secure_private_sqlite_files(self.path)
        if (
            row is None or row["provider_id"] != provider_id
            or row["user_id"] != user_id or int(row["expires_at"]) < now
        ):
            return None
        return self._decrypt(row["verifier_cipher"])

    def save_connection(
        self, user_id: str, provider_id: str, provider_subject: str,
        account_label: str, access_token: str, refresh_token: str | None,
        scopes: list[str], expires_at: int | None,
    ) -> None:
        if provider_id not in PROVIDERS or not user_id or not access_token:
            raise PluginConnectionError("Invalid plugin connection")
        existing = self.connection(user_id, provider_id)
        refresh = str(refresh_token or (existing or {}).get("refresh_token") or "")
        now = _now_iso()
        with self.lock:
            with self.db:
                self.db.execute("""
                    INSERT INTO plugin_user_connections
                      (user_id, provider_id, provider_subject, account_label,
                       access_token_cipher, refresh_token_cipher, scopes_json,
                       expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, provider_id) DO UPDATE SET
                      provider_subject=excluded.provider_subject,
                      account_label=excluded.account_label,
                      access_token_cipher=excluded.access_token_cipher,
                      refresh_token_cipher=excluded.refresh_token_cipher,
                      scopes_json=excluded.scopes_json,
                      expires_at=excluded.expires_at,
                      updated_at=excluded.updated_at
                """, (
                    user_id, provider_id, str(provider_subject)[:256],
                    str(account_label)[:256], self._encrypt(access_token),
                    self._encrypt(refresh), json.dumps(_scope_list(scopes)),
                    expires_at, now, now,
                ))
            secure_private_sqlite_files(self.path)

    def connection(self, user_id: str, provider_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("""
                SELECT * FROM plugin_user_connections
                WHERE user_id=? AND provider_id=?
            """, (user_id, provider_id)).fetchone()
        if row is None:
            return None
        try:
            scopes = json.loads(row["scopes_json"])
        except (TypeError, ValueError):
            scopes = []
        return {
            "user_id": row["user_id"], "provider_id": row["provider_id"],
            "provider_subject": row["provider_subject"], "account_label": row["account_label"],
            "access_token": self._decrypt(row["access_token_cipher"]),
            "refresh_token": self._decrypt(row["refresh_token_cipher"]),
            "scopes": _scope_list(scopes), "expires_at": row["expires_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def update_access_token(
        self, user_id: str, provider_id: str, access_token: str,
        refresh_token: str | None, expires_at: int | None,
    ) -> None:
        current = self.connection(user_id, provider_id)
        if current is None:
            raise PluginConnectionError("Plugin account is not connected")
        refresh = str(refresh_token or current["refresh_token"] or "")
        with self.lock:
            with self.db:
                self.db.execute("""
                    UPDATE plugin_user_connections
                    SET access_token_cipher=?, refresh_token_cipher=?, expires_at=?, updated_at=?
                    WHERE user_id=? AND provider_id=?
                """, (
                    self._encrypt(access_token), self._encrypt(refresh), expires_at,
                    _now_iso(), user_id, provider_id,
                ))
            secure_private_sqlite_files(self.path)

    def list_connections(self, user_id: str) -> dict[str, dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT provider_id FROM plugin_user_connections WHERE user_id=?",
                (user_id,),
            ).fetchall()
        return {
            str(row["provider_id"]): self.connection(user_id, str(row["provider_id"]))
            for row in rows
        }

    def disconnect(self, user_id: str, provider_id: str) -> None:
        with self.lock:
            with self.db:
                self.db.execute(
                    "DELETE FROM plugin_user_connections WHERE user_id=? AND provider_id=?",
                    (user_id, provider_id),
                )
            secure_private_sqlite_files(self.path)

    def close(self) -> None:
        self.db.close()


def install_plugin_connections_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "plugin_connections_v1_installed", False):
        return app
    store = PluginOAuthStore(app.state.settings.data_dir)
    app.state.plugin_oauth_store = store

    def current_user(request: Request) -> dict[str, Any]:
        accounts = getattr(request.app.state, "user_accounts", None)
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE)) if accounts else None
        if not user:
            raise HTTPException(401, "请先登录账户", headers=_PRIVATE_HEADERS)
        if user.get("is_guest"):
            raise HTTPException(403, "游客账户暂不支持连接外部插件", headers=_PRIVATE_HEADERS)
        from .web_chat_identity_v1 import web_chat_session_policy_allows
        if not web_chat_session_policy_allows(request.app, user):
            raise HTTPException(403, "Web Chat 当前不可用", headers=_PRIVATE_HEADERS)
        return user

    def admin_actor(request: Request) -> str:
        sessions = getattr(request.app.state, "admin_sessions", None)
        token = request.cookies.get(SESSION_COOKIE)
        if sessions is None or not sessions.authenticate(token):
            raise HTTPException(401, "Administrator login required")
        identity = getattr(sessions, "identity", None)
        username = identity(token) if callable(identity) else app.state.settings.admin_username
        return str(username or app.state.settings.admin_username)

    def callback_uri(request: Request, provider_id: str) -> str:
        control = getattr(request.app.state, "control_plane", None)
        configured = control.public_client().get("client_domain") if control is not None else ""
        base = str(configured or request.base_url).strip()
        if base and not base.startswith(("https://", "http://")):
            base = "https://" + base
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"https", "http"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "testserver"})
        ):
            raise HTTPException(503, "请先在客户端配置中设置有效的 HTTPS 客户端域名")
        return f"{parsed.scheme}://{parsed.netloc}/api/user/plugins/oauth/{provider_id}/callback"

    def public_provider(request: Request, provider_id: str) -> dict[str, Any]:
        return store.public_provider(provider_id, callback_uri(request, provider_id))

    @app.get("/api/admin/plugins/providers")
    async def admin_plugin_providers(request: Request) -> dict[str, Any]:
        admin_actor(request)
        return {
            "data": [public_provider(request, provider_id) for provider_id in PROVIDERS],
        }

    @app.put("/api/admin/plugins/providers/{provider_id}")
    async def update_admin_plugin_provider(provider_id: str, request: Request) -> dict[str, Any]:
        actor = admin_actor(request)
        if not same_origin_admin_request(request):
            raise HTTPException(403, "请求来源无效")
        if provider_id not in PROVIDERS:
            raise HTTPException(404, "Unknown plugin provider")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise PluginConnectionError("Invalid provider configuration")
            enabled = payload.get("enabled", False)
            private_repos = payload.get("private_repos", False)
            if type(enabled) is not bool or type(private_repos) is not bool:
                raise PluginConnectionError("enabled and private_repos must be booleans")
        except (PluginConnectionError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        audit = getattr(app.state, "admin_audit", None)
        if audit is None:
            audit = AdminAuditStore(app.state.settings.data_dir)
            app.state.admin_audit = audit
        request_id = str(request.headers.get("x-request-id") or "")
        try:
            audit.append(
                actor=actor, action="plugin.provider.update_requested",
                target_type="plugin_provider", target_id=provider_id,
                details={"enabled": enabled, "private_repos": private_repos},
                request_id=request_id,
            )
        except AdminAuditError as exc:
            raise HTTPException(503, "Administrator audit unavailable; provider update denied") from exc
        try:
            saved = store.save_provider(
                provider_id, enabled=enabled,
                client_id=str(payload.get("client_id") or ""),
                client_secret=str(payload.get("client_secret") or ""),
                private_repos=private_repos,
            )
        except PluginConnectionError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit_pending = False
        try:
            audit.append(
                actor=actor, action="plugin.provider.update_completed",
                target_type="plugin_provider", target_id=provider_id,
                details={"enabled": bool(saved.get("enabled")), "private_repos": bool(saved.get("private_repos"))},
                request_id=request_id,
            )
        except AdminAuditError:
            # The configuration is already committed. Preserve success and
            # expose the pending audit completion for the administrator.
            app.state.admin_audit_completion_pending = True
            audit_pending = True
        return {"provider": public_provider(request, provider_id), "audit_completion_pending": audit_pending}

    @app.get("/api/user/plugins")
    async def user_plugin_catalog(request: Request) -> JSONResponse:
        user = current_user(request)
        connections = store.list_connections(str(user["user_id"]))
        rows = []
        for provider_id in PROVIDERS:
            public = public_provider(request, provider_id)
            connection = connections.get(provider_id)
            public["connected"] = bool(connection)
            public["account_label"] = connection.get("account_label") if connection else ""
            public["scopes"] = connection.get("scopes") if connection else public["scopes"]
            public["connected_at"] = connection.get("created_at") if connection else None
            rows.append(public)
        return JSONResponse({"data": rows}, headers=_PRIVATE_HEADERS)

    @app.get("/api/user/plugins/oauth/{provider_id}/start")
    async def start_plugin_oauth(provider_id: str, request: Request) -> RedirectResponse:
        user = current_user(request)
        if provider_id not in PROVIDERS:
            raise HTTPException(404, "Unknown plugin provider")
        config = store.provider(provider_id)
        if not config or not config.get("enabled"):
            raise HTTPException(404, "插件尚未由管理员启用")
        uri = callback_uri(request, provider_id)
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        state = store.create_state(provider_id, str(user["user_id"]), verifier)
        spec = PROVIDERS[provider_id]
        scopes = (
            ["read:user", "repo"] if config.get("private_repos")
            else ["read:user"]
        ) if provider_id == "github" else [
            "openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"
        ]
        params = {
            "client_id": config["client_id"], "redirect_uri": uri,
            "response_type": "code", "scope": " ".join(scopes),
            "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if provider_id == "gmail":
            params.update({"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})
        return RedirectResponse(spec["authorize_url"] + "?" + urlencode(params), status_code=303, headers=_PRIVATE_HEADERS)

    async def exchange_oauth_code(
        provider_id: str, config: dict[str, Any], code: str,
        uri: str, verifier: str,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        spec = PROVIDERS[provider_id]
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if provider_id == "github":
                response = await client.post(
                    spec["token_url"],
                    data={
                        "client_id": config["client_id"], "client_secret": config["client_secret"],
                        "code": code, "redirect_uri": uri, "code_verifier": verifier,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                token_payload = response.json()
                access_token = str(token_payload.get("access_token") or "")
                if not access_token or token_payload.get("error"):
                    raise PluginConnectionError("GitHub OAuth token exchange failed")
                profile_response = await client.get(
                    spec["api_base"] + "/user",
                    headers={"Authorization": "Bearer " + access_token, "Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28"},
                )
                profile_response.raise_for_status()
                profile = profile_response.json()
                scopes = _scope_list(profile_response.headers.get("X-OAuth-Scopes", ""))
                expires_in = token_payload.get("expires_in")
                expires_at = int(time.time()) + max(60, int(expires_in)) if expires_in else None
                return (
                    {
                        "access_token": access_token,
                        "refresh_token": str(token_payload.get("refresh_token") or ""),
                        "expires_at": expires_at,
                    },
                    {"subject": str(profile.get("id") or profile.get("login") or ""),
                     "label": str(profile.get("login") or profile.get("name") or "GitHub account")},
                    scopes,
                )
            response = await client.post(
                spec["token_url"],
                data={
                    "client_id": config["client_id"], "client_secret": config["client_secret"],
                    "code": code, "redirect_uri": uri, "code_verifier": verifier,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_payload = response.json()
            access_token = str(token_payload.get("access_token") or "")
            if not access_token or token_payload.get("error"):
                raise PluginConnectionError("Google OAuth token exchange failed")
            profile_response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": "Bearer " + access_token},
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            if profile.get("email_verified") is False:
                raise PluginConnectionError("Google account email is not verified")
            expires_in = int(token_payload.get("expires_in") or 3600)
            return (
                {
                    "access_token": access_token,
                    "refresh_token": str(token_payload.get("refresh_token") or ""),
                    "expires_at": int(time.time()) + max(60, expires_in),
                },
                {"subject": str(profile.get("sub") or profile.get("email") or ""),
                 "label": str(profile.get("email") or profile.get("name") or "Google account")},
                _scope_list(token_payload.get("scope") or " ".join([
                    "openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"
                ])),
            )

    @app.get("/api/user/plugins/oauth/{provider_id}/callback")
    async def finish_plugin_oauth(provider_id: str, request: Request) -> RedirectResponse:
        if provider_id not in PROVIDERS:
            return RedirectResponse("/chat?tab=plugins&plugin_error=unknown_provider", status_code=303, headers=_PRIVATE_HEADERS)
        user = getattr(request.app.state, "user_accounts", None)
        authenticated = user.authenticate(request.cookies.get(USER_SESSION_COOKIE)) if user else None
        if not authenticated or authenticated.get("is_guest"):
            return RedirectResponse("/chat?tab=plugins&plugin_error=login_required", status_code=303, headers=_PRIVATE_HEADERS)
        from .web_chat_identity_v1 import web_chat_session_policy_allows
        if not web_chat_session_policy_allows(request.app, authenticated):
            return RedirectResponse("/chat?tab=plugins&plugin_error=provider_disabled", status_code=303, headers=_PRIVATE_HEADERS)
        state = str(request.query_params.get("state") or "")
        verifier = store.consume_state(state, provider_id, str(authenticated["user_id"]))
        if not verifier:
            return RedirectResponse("/chat?tab=plugins&plugin_error=invalid_state", status_code=303, headers=_PRIVATE_HEADERS)
        code = str(request.query_params.get("code") or "")
        if not code or len(code) > 4096 or request.query_params.get("error"):
            return RedirectResponse("/chat?tab=plugins&plugin_error=authorization_denied", status_code=303, headers=_PRIVATE_HEADERS)
        config = store.provider(provider_id)
        if not config or not config.get("enabled"):
            return RedirectResponse("/chat?tab=plugins&plugin_error=provider_disabled", status_code=303, headers=_PRIVATE_HEADERS)
        try:
            uri = callback_uri(request, provider_id)
            token, profile, scopes = await exchange_oauth_code(provider_id, config, code, uri, verifier)
            if not profile.get("subject"):
                raise PluginConnectionError("Provider account identity is missing")
            store.save_connection(
                str(authenticated["user_id"]), provider_id, profile["subject"],
                profile["label"], token["access_token"], token.get("refresh_token"),
                scopes, token.get("expires_at"),
            )
        except (httpx.HTTPError, ValueError, KeyError, AttributeError, PluginConnectionError):
            return RedirectResponse("/chat?tab=plugins&plugin_error=token_exchange_failed", status_code=303, headers=_PRIVATE_HEADERS)
        return RedirectResponse("/chat?tab=plugins&plugin=connected&provider=" + provider_id, status_code=303, headers=_PRIVATE_HEADERS)

    @app.post("/api/user/plugins/{provider_id}/disconnect")
    async def disconnect_plugin(provider_id: str, request: Request) -> JSONResponse:
        user = current_user(request)
        if not same_origin_admin_request(request):
            raise HTTPException(403, "请求来源无效", headers=_PRIVATE_HEADERS)
        if provider_id not in PROVIDERS:
            raise HTTPException(404, "Unknown plugin provider", headers=_PRIVATE_HEADERS)
        user_id = str(user["user_id"])
        connection = store.connection(user_id, provider_id)
        config = store.provider(provider_id)
        if connection and config:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False) as client:
                    if provider_id == "gmail":
                        await client.post(
                            "https://oauth2.googleapis.com/revoke",
                            data={"token": connection["access_token"]},
                        )
                    else:
                        await client.delete(
                            "https://api.github.com/applications/" + config["client_id"] + "/grant",
                            auth=(config["client_id"], config["client_secret"]),
                            json={"access_token": connection["access_token"]},
                            headers={"Accept": "application/vnd.github+json"},
                        )
            except httpx.HTTPError:
                # Local credential removal is authoritative even if the
                # provider's revocation endpoint is temporarily unavailable.
                pass
        store.disconnect(user_id, provider_id)
        return JSONResponse({"ok": True}, headers=_PRIVATE_HEADERS)

    async def access_token_for(user_id: str, provider_id: str) -> str:
        connection = store.connection(user_id, provider_id)
        if not connection:
            raise HTTPException(409, "请先连接此插件账户", headers=_PRIVATE_HEADERS)
        expires_at = connection.get("expires_at")
        if not expires_at or int(expires_at) > int(time.time()) + 60:
            return str(connection["access_token"])
        if not connection.get("refresh_token"):
            raise HTTPException(401, "插件授权已过期，请重新连接账户", headers=_PRIVATE_HEADERS)
        config = store.provider(provider_id)
        if not config or not config.get("enabled"):
            raise HTTPException(403, "插件已由管理员停用", headers=_PRIVATE_HEADERS)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False) as client:
            response = await client.post(
                PROVIDERS[provider_id]["token_url"],
                data={
                    "client_id": config["client_id"], "client_secret": config["client_secret"],
                    "refresh_token": connection["refresh_token"], "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise HTTPException(401, "插件授权已失效，请重新连接账户", headers=_PRIVATE_HEADERS)
        new_expiry = int(time.time()) + max(60, int(payload.get("expires_in") or 3600))
        store.update_access_token(
            user_id, provider_id, token, payload.get("refresh_token"), new_expiry,
        )
        return token

    @app.post("/api/user/plugins/{provider_id}/tools/{tool_name}")
    async def run_plugin_tool(
        provider_id: str, tool_name: str, request: Request,
    ) -> JSONResponse:
        user = current_user(request)
        if not same_origin_admin_request(request):
            raise HTTPException(403, "请求来源无效", headers=_PRIVATE_HEADERS)
        if provider_id not in PROVIDERS:
            raise HTTPException(404, "Unknown plugin provider", headers=_PRIVATE_HEADERS)
        config = store.provider(provider_id)
        if not config or not config.get("enabled"):
            raise HTTPException(403, "插件当前不可用", headers=_PRIVATE_HEADERS)
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            raise HTTPException(422, "Invalid plugin tool input", headers=_PRIVATE_HEADERS)
        try:
            token = await access_token_for(str(user["user_id"]), provider_id)
        except httpx.HTTPError as exc:
            raise HTTPException(502, "插件授权刷新暂时失败，请稍后重试", headers=_PRIVATE_HEADERS) from exc
        timeout = httpx.Timeout(15.0, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                if provider_id == "github" and tool_name == "search_repositories":
                    query = str(payload.get("query") or "").strip()
                    if not query or len(query) > 200:
                        raise HTTPException(422, "query must contain 1-200 characters", headers=_PRIVATE_HEADERS)
                    response = await client.get(
                        "https://api.github.com/search/repositories",
                        params={"q": query, "per_page": 10, "sort": "updated", "order": "desc"},
                        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                 "X-GitHub-Api-Version": "2022-11-28"},
                    )
                    response.raise_for_status()
                    raw = response.json()
                    data = [{
                        "full_name": row.get("full_name"), "description": row.get("description"),
                        "url": row.get("html_url"), "stars": row.get("stargazers_count"),
                        "language": row.get("language"),
                    } for row in raw.get("items", [])[:10]]
                elif provider_id == "github" and tool_name == "list_issues":
                    repository = str(payload.get("repository") or "").strip()
                    if not _GITHUB_REPO.fullmatch(repository):
                        raise HTTPException(422, "repository must be owner/repo", headers=_PRIVATE_HEADERS)
                    state = str(payload.get("state") or "open")
                    if state not in {"open", "closed", "all"}:
                        raise HTTPException(422, "state must be open, closed or all", headers=_PRIVATE_HEADERS)
                    response = await client.get(
                        "https://api.github.com/repos/" + repository + "/issues",
                        params={"state": state, "per_page": 20},
                        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                 "X-GitHub-Api-Version": "2022-11-28"},
                    )
                    response.raise_for_status()
                    data = [{
                        "number": row.get("number"), "title": row.get("title"),
                        "state": row.get("state"), "url": row.get("html_url"),
                        "body": str(row.get("body") or "")[:2000],
                    } for row in response.json()[:20] if isinstance(row, dict) and not row.get("pull_request")]
                elif provider_id == "gmail" and tool_name == "search_messages":
                    query = str(payload.get("query") or "").strip()
                    if not query or len(query) > 500:
                        raise HTTPException(422, "query must contain 1-500 characters", headers=_PRIVATE_HEADERS)
                    response = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        params={"q": query, "maxResults": 10},
                        headers={"Authorization": "Bearer " + token},
                    )
                    response.raise_for_status()
                    messages = response.json().get("messages") or []
                    data = []
                    for item in messages[:10]:
                        message_id = str(item.get("id") or "")
                        if not _GMAIL_ID.fullmatch(message_id):
                            continue
                        detail = await client.get(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message_id,
                            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                            headers={"Authorization": "Bearer " + token},
                        )
                        detail.raise_for_status()
                        message = detail.json()
                        message_payload = message.get("payload") or {}
                        message_headers = message_payload.get("headers") or []
                        headers = {
                            str(header.get("name") or "").lower(): str(header.get("value") or "")[:500]
                            for header in message_headers if isinstance(header, dict)
                        }
                        data.append({
                            "id": message_id, "thread_id": message.get("threadId"),
                            "subject": headers.get("subject", "(无主题)"),
                            "from": headers.get("from", ""), "date": headers.get("date", ""),
                            "snippet": str(message.get("snippet") or "")[:1200],
                        })
                else:
                    raise HTTPException(404, "Unsupported plugin tool", headers=_PRIVATE_HEADERS)
    
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(502, "插件服务暂时不可用，请稍后重试", headers=_PRIVATE_HEADERS) from exc
        return JSONResponse({
            "provider": provider_id, "tool": tool_name, "data": data,
        }, headers=_PRIVATE_HEADERS)

    app.state.plugin_connections_v1_installed = True
    app.state.plugin_connections_v1_version = 1
    return app
