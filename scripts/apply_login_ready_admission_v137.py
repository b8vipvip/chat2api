from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, marker: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    if old not in text:
        raise RuntimeError(f"missing patch anchor in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


(ROOT / "app/login_readiness.py").write_text('''from __future__ import annotations

from typing import Any


READY_STATES = frozenset({"ready", "logged_in", "authenticated"})


def login_readiness(metadata: Any) -> dict[str, Any]:
    """Normalize the fail-closed ChatGPT login admission signal.

    Transport/WebSocket online state only proves that the Worker bridge is alive.
    API traffic is safe only after the browser reports a logged-in ChatGPT page
    with a usable composer. Unknown/checking/login_required are never routable.
    """
    source = metadata if isinstance(metadata, dict) else {}
    bridge = source.get("bridge") if isinstance(source.get("bridge"), dict) else {}
    state = str(
        source.get("chatgpt_login_state")
        or bridge.get("login_state")
        or source.get("chatgpt_status")
        or "unknown"
    ).strip().lower()
    if "chatgpt_login_composer_ready" in source:
        composer_ready = source.get("chatgpt_login_composer_ready") is True
    else:
        composer_ready = bridge.get("composer_ready") is True
    checked_at = source.get("chatgpt_login_checked_at_ms")
    if checked_at is None:
        checked_at = bridge.get("login_checked_at_ms")
    try:
        checked_at_ms = max(0, int(checked_at or 0))
    except (TypeError, ValueError):
        checked_at_ms = 0
    ready = state in READY_STATES and composer_ready
    return {
        "state": state or "unknown",
        "composer_ready": composer_ready,
        "checked_at_ms": checked_at_ms,
        "ready": ready,
        "reason": "ready" if ready else (
            "login_required" if state == "login_required" else
            "composer_not_ready" if state in READY_STATES else
            state or "unknown"
        ),
    }


def chatgpt_routing_ready(metadata: Any) -> bool:
    return bool(login_readiness(metadata)["ready"])
''', encoding="utf-8")

replace_once(
    "app/registry.py",
    "from .timezone_utils import beijing_now_iso, to_beijing_iso\n",
    "from .timezone_utils import beijing_now_iso, to_beijing_iso\nfrom .login_readiness import chatgpt_routing_ready\n",
    "from .login_readiness import chatgpt_routing_ready",
)
replace_once(
    "app/registry.py",
    '''    def online_client_ids(self) -> list[str]:
        return sorted(
            client_id for client_id in self.sockets
            if self.clients.get(client_id) and self.clients[client_id].connection_enabled
        )

    def resolve_client(self, requested: str | None) -> str:
''',
    '''    def online_client_ids(self) -> list[str]:
        return sorted(
            client_id for client_id in self.sockets
            if self.clients.get(client_id) and self.clients[client_id].connection_enabled
        )

    def chatgpt_routing_ready(self, client_id: str) -> bool:
        client = self.clients.get(str(client_id))
        return bool(client and chatgpt_routing_ready(client.metadata))

    def resolve_client(self, requested: str | None) -> str:
''',
    "def chatgpt_routing_ready(self, client_id: str)",
)
replace_once(
    "app/registry.py",
    '''        if requested:
            if requested not in self.clients:
                raise KeyError("Unknown client_id")
            client = self.clients[requested]
            if not client.connection_enabled:
                raise ConnectionError("Requested Chrome extension is disabled by administrator")
            if requested not in self.sockets:
                raise ConnectionError("Requested Chrome extension is offline")
            self._remember_route(key_id, requested)
            return requested

        online = self.online_client_ids()
        if not online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
''',
    '''        if requested:
            if requested not in self.clients:
                raise KeyError("Unknown client_id")
            client = self.clients[requested]
            if not client.connection_enabled:
                raise ConnectionError("Requested Chrome extension is disabled by administrator")
            if requested not in self.sockets:
                raise ConnectionError("Requested Chrome extension is offline")
            if not self.chatgpt_routing_ready(requested):
                raise ConnectionError(
                    "Requested Chrome extension is online but ChatGPT is not logged in or the composer is not ready"
                )
            self._remember_route(key_id, requested)
            return requested

        transport_online = self.online_client_ids()
        if not transport_online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
        online = [client_id for client_id in transport_online if self.chatgpt_routing_ready(client_id)]
        if not online:
            raise ConnectionError(
                "No ChatGPT-ready Chrome extension is available. Log in to ChatGPT on an enabled Worker first."
            )
''',
    "No ChatGPT-ready Chrome extension is available.",
)
replace_once(
    "app/registry.py",
    "        client_ids = self.online_client_ids() if online_only else [\n",
    "        client_ids = [client_id for client_id in self.online_client_ids() if self.chatgpt_routing_ready(client_id)] if online_only else [\n",
    "client_ids = [client_id for client_id in self.online_client_ids() if self.chatgpt_routing_ready(client_id)]",
)
replace_once(
    "app/registry.py",
    '''                "metadata": item.metadata,
            }
''',
    '''                "metadata": item.metadata,
                "chatgpt_routing_ready": self.chatgpt_routing_ready(item.client_id),
            }
''',
    '"chatgpt_routing_ready": self.chatgpt_routing_ready(item.client_id)',
)

replace_once(
    "app/model_capability_routing_patch.py",
    '''def _compatible(registry: Any, client_id: str, model: str) -> bool:
    model = str(model or "").strip().lower()
    account = _account_type(registry, client_id)
''',
    '''def _compatible(registry: Any, client_id: str, model: str) -> bool:
    checker = getattr(registry, "chatgpt_routing_ready", None)
    if callable(checker):
        try:
            if not bool(checker(client_id)):
                return False
        except Exception:
            return False
    else:
        item = getattr(registry, "clients", {}).get(str(client_id))
        metadata = getattr(item, "metadata", None) if item else None
        from .login_readiness import chatgpt_routing_ready
        if not chatgpt_routing_ready(metadata):
            return False
    model = str(model or "").strip().lower()
    account = _account_type(registry, client_id)
''',
    'checker = getattr(registry, "chatgpt_routing_ready", None)',
)

replace_once(
    "app/window_manager_v88_patch.py",
    "from .admin_auth import SESSION_COOKIE\n",
    "from .admin_auth import SESSION_COOKIE\nfrom .login_readiness import login_readiness\n",
    "from .login_readiness import login_readiness",
)
replace_once(
    "app/window_manager_v88_patch.py",
    '''            bundle_version = _worker_bundle_version(row)
            online = bool(row.get("online"))
            refresh_capable = _version_tuple(bundle_version) >= LIVE_TRUTH_MIN_BUNDLE
            live_verified = client_id in verified
            cached_active = [item for item in (snapshot.get("active") or []) if isinstance(item, dict)]
            if live_verified:
                truth_status = "verified"
''',
    '''            bundle_version = _worker_bundle_version(row)
            online = bool(row.get("online"))
            login = login_readiness(metadata)
            login_ready = bool(login.get("ready"))
            refresh_capable = _version_tuple(bundle_version) >= LIVE_TRUTH_MIN_BUNDLE
            live_verified = client_id in verified
            cached_active = [item for item in (snapshot.get("active") or []) if isinstance(item, dict)]
            if live_verified and not login_ready:
                truth_status = "login-required" if login.get("state") == "login_required" else "login-not-ready"
            elif live_verified:
                truth_status = "verified"
''',
    "login = login_readiness(metadata)",
)
replace_once(
    "app/window_manager_v88_patch.py",
    '''                "cached_active_count": len(cached_active),
                "live_verified": live_verified,
                "truth_status": truth_status,
                "truth_revision": LIVE_TRUTH_REVISION,
''',
    '''                "cached_active_count": len(cached_active),
                "live_verified": live_verified,
                "chatgpt_login_state": login.get("state"),
                "chatgpt_login_composer_ready": bool(login.get("composer_ready")),
                "chatgpt_routing_ready": login_ready,
                "reception_ready": bool(online and live_verified and login_ready),
                "truth_status": truth_status,
                "truth_revision": LIVE_TRUTH_REVISION,
''',
    '"reception_ready": bool(online and live_verified and login_ready)',
)
replace_once(
    "app/window_manager_v88_patch.py",
    '''            if live_verified:
                for raw in cached_active:
''',
    '''            if live_verified and login_ready:
                for raw in cached_active:
''',
    "if live_verified and login_ready:",
)
replace_once(
    "app/window_manager_v88_patch.py",
    '''        unverified = [row for row in workers if row.get("online") and not row.get("live_verified")]
        return {
''',
    '''        unverified = [row for row in workers if row.get("online") and not row.get("live_verified")]
        login_blocked = [row for row in workers if row.get("online") and row.get("chatgpt_routing_ready") is not True]
        reception_ready = [row for row in workers if row.get("reception_ready") is True]
        return {
''',
    "login_blocked = [row for row in workers",
)
replace_once(
    "app/window_manager_v88_patch.py",
    '''                "unverified_workers": len(unverified),
                "cached_active_rows_suppressed": sum(int(row.get("cached_active_count") or 0) for row in unverified),
''',
    '''                "unverified_workers": len(unverified),
                "reception_ready_workers": len(reception_ready),
                "login_blocked_workers": len(login_blocked),
                "login_blocked_active_rows_suppressed": sum(int(row.get("cached_active_count") or 0) for row in login_blocked),
                "cached_active_rows_suppressed": sum(int(row.get("cached_active_count") or 0) for row in unverified),
''',
    '"login_blocked_workers": len(login_blocked)',
)

replace_once(
    "app/admin_window_manager_v88.js",
    '''    const unverified = Math.max(0, Number(truth.unverified_workers || 0));
    const suppressed = Math.max(0, Number(truth.cached_active_rows_suppressed || 0));
''',
    '''    const unverified = Math.max(0, Number(truth.unverified_workers || 0));
    const receptionReady = Math.max(0, Number(truth.reception_ready_workers || 0));
    const loginBlocked = Math.max(0, Number(truth.login_blocked_workers || 0));
    const loginSuppressed = Math.max(0, Number(truth.login_blocked_active_rows_suppressed || 0));
    const suppressed = Math.max(0, Number(truth.cached_active_rows_suppressed || 0));
''',
    "const loginBlocked = Math.max",
)
replace_once(
    "app/admin_window_manager_v88.js",
    '''      box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验；${unverified} 个未核验。已抑制 ${suppressed} 条历史缓存窗口，不计入“接待中窗口”。${reasons ? ` ${reasons}` : ""}`;
      return;
    }
    box.className = "muted";
    box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验。当前“接待中窗口”只显示本次从 Chrome 实际窗口图重新确认存在的窗口。`;
''',
    '''      const loginNote = loginBlocked > 0 ? ` ChatGPT 未登录/未就绪 Worker ${loginBlocked} 个，已屏蔽其 ${loginSuppressed} 个物理窗口。` : "";
      box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验；${unverified} 个未核验。已抑制 ${suppressed} 条历史缓存窗口，不计入“接待中窗口”。${loginNote}${reasons ? ` ${reasons}` : ""}`;
      return;
    }
    box.className = loginBlocked > 0 ? "warnText" : "muted";
    box.textContent = `实时物理核验：${verified}/${online} 个在线 Worker 已核验；ChatGPT 可接待 Worker ${receptionReady} 个。${loginBlocked > 0 ? ` 未登录/未就绪 ${loginBlocked} 个，其 ${loginSuppressed} 个物理窗口已屏蔽，不计入“接待中窗口”，也不会参与 API 路由。` : " 当前“接待中窗口”只显示本次从 Chrome 实际窗口图重新确认存在且 ChatGPT 已登录的窗口。"}`;
''',
    "ChatGPT 可接待 Worker ${receptionReady} 个",
)

replace_once(
    "app/runtime_contract.py",
    '            "model_capability_routing_v2": True,\n',
    '            "model_capability_routing_v2": True,\n            "chatgpt_login_ready_admission_v137": True,\n            "window_manager_login_ready_filter_v137": True,\n',
    '"chatgpt_login_ready_admission_v137": True',
)

(ROOT / "tests/test_login_ready_admission_v137.py").write_text(r'''from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.model_capability_routing_patch import _compatible
from app.registry import ClientRegistry, PersistedClient
from app.window_manager_v88_patch import install_window_manager_v88_patch


def _client(client_id: str, *, state: str, composer: bool) -> PersistedClient:
    return PersistedClient(
        client_id=client_id,
        name=client_id,
        browser_name="Chrome",
        version="0.8.37",
        token_hash="x",
        created_at="2026-09-13T20:00:00+08:00",
        metadata={
            "extension_version": "0.8.37",
            "account_type": "free",
            "chatgpt_login_state": state,
            "chatgpt_login_composer_ready": composer,
            "models": [{"id": "gpt-5.5-mini"}],
        },
    )


def test_registry_routes_only_confirmed_logged_in_workers(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    ready = _client("ext_ready", state="ready", composer=True)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    unknown = _client("ext_unknown", state="unknown", composer=False)
    registry.clients = {item.client_id: item for item in (ready, logged_out, unknown)}
    registry.sockets = {item.client_id: object() for item in (ready, logged_out, unknown)}

    assert registry.chatgpt_routing_ready("ext_ready") is True
    assert registry.chatgpt_routing_ready("ext_logged_out") is False
    assert registry.chatgpt_routing_ready("ext_unknown") is False

    registry.set_routing_key("key_qnbot")
    registry.api_key_routes["key_qnbot"] = "ext_logged_out"
    assert registry.resolve_client(None) == "ext_ready"
    assert registry.api_key_routes["key_qnbot"] == "ext_ready"

    with pytest.raises(ConnectionError, match="ChatGPT is not logged in"):
        registry.resolve_client("ext_logged_out")

    assert _compatible(registry, "ext_ready", "gpt-5.5-mini") is True
    assert _compatible(registry, "ext_logged_out", "gpt-5.5-mini") is False
    assert _compatible(registry, "ext_unknown", "gpt-5.5-mini") is False

    mini = next(item for item in registry.model_catalog(online_only=True) if item["id"] == "gpt-5.5-mini")
    assert mini["clients"] == ["ext_ready"]


def test_registry_fails_closed_when_only_transport_online_worker_is_logged_out(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    registry.clients = {logged_out.client_id: logged_out}
    registry.sockets = {logged_out.client_id: object()}
    assert registry.online_client_ids() == ["ext_logged_out"]
    with pytest.raises(ConnectionError, match="No ChatGPT-ready Chrome extension"):
        registry.resolve_client(None)


class _Sessions:
    def authenticate(self, _cookie) -> bool:
        return True


class _WindowRegistry:
    def __init__(self) -> None:
        self.clients = {"ext_ready": object(), "ext_logged_out": object()}
        self.sockets = dict(self.clients)
        self.rows = [
            self._row("ext_ready", "ready", True, 11),
            self._row("ext_logged_out", "login_required", False, 22),
        ]

    @staticmethod
    def _row(client_id: str, state: str, composer: bool, window_id: int) -> dict:
        return {
            "client_id": client_id,
            "device_name": "TX03",
            "online": True,
            "version": "0.8.37",
            "metadata": {
                "extension_version": "0.8.37",
                "chatgpt_login_state": state,
                "chatgpt_login_composer_ready": composer,
                "window_manager_revision": 90,
                "window_manager_v88": {
                    "updated_at_ms": 100,
                    "active": [{
                        "window_no": 1 if client_id == "ext_ready" else 2,
                        "window_id": window_id,
                        "tab_id": window_id + 100,
                        "status": "ready",
                        "opened_at_ms": 1000,
                    }],
                    "closed": [],
                },
            },
        }

    def summaries(self):
        return deepcopy(self.rows)

    async def send(self, client_id: str, payload: dict) -> None:
        assert payload["type"] == "window.manager.refresh"
        for row in self.rows:
            if row["client_id"] == client_id:
                row["metadata"]["window_manager_v88"]["updated_at_ms"] += 1


def test_window_manager_hides_logged_out_physical_windows() -> None:
    app = FastAPI()
    app.state.registry = _WindowRegistry()
    app.state.admin_sessions = _Sessions()
    install_window_manager_v88_patch(app)

    response = TestClient(app).get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["client_id"] for row in payload["active"]] == ["ext_ready"]
    assert payload["truth"]["online_workers"] == 2
    assert payload["truth"]["verified_workers"] == 2
    assert payload["truth"]["reception_ready_workers"] == 1
    assert payload["truth"]["login_blocked_workers"] == 1
    assert payload["truth"]["login_blocked_active_rows_suppressed"] == 1
    blocked = next(row for row in payload["workers"] if row["client_id"] == "ext_logged_out")
    assert blocked["live_verified"] is True
    assert blocked["chatgpt_routing_ready"] is False
    assert blocked["reception_ready"] is False
    assert blocked["truth_status"] == "login-required"


def test_runtime_contract_advertises_v137_login_guard() -> None:
    from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["chatgpt_login_ready_admission_v137"] is True
    assert payload["features"]["window_manager_login_ready_filter_v137"] is True
''', encoding="utf-8")

print("login-ready admission v137 patch prepared")
