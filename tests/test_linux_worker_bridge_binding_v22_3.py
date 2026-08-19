import asyncio
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.linux_worker_bridge_binding import BINDING_TICKET_TTL_SECONDS, WorkerBridgeBindingTicketStore, install_linux_worker_bridge_binding_patch
from app.linux_worker_patch import install_linux_worker_patch
from app.linux_workers import LinuxWorkerStore
from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class Settings:
    def __init__(self, path: Path):
        self.data_dir = path

    def resolved_public_url(self, _: str) -> str:
        return "https://chat2api.example"


class Sessions:
    def authenticate(self, token):
        return token == "admin-ok"


def app_for(path: Path) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(path)
    app.state.admin_sessions = Sessions()
    app.state.registry = ClientRegistry(path)
    install_linux_worker_patch(app)
    install_linux_worker_bridge_binding_patch(app)
    return app


def enrolled(store: LinuxWorkerStore, name: str = "worker") -> dict[str, str]:
    enrollment = store.create_enrollment(name)
    return store.enroll(enrollment["code"], {"hostname": name, "platform": "linux", "agent_version": "0.3.1"})


def test_binding_ticket_is_digest_only_one_time_worker_scoped_and_expires():
    clock = Clock()
    store = WorkerBridgeBindingTicketStore(now=clock, ttl_seconds=120)
    raw = store.issue("wrk_one")
    assert raw.startswith("wbind_")
    assert raw not in repr(store._tickets)
    assert store.require(raw).worker_id == "wrk_one"
    assert store.consume(raw).worker_id == "wrk_one"
    with pytest.raises(KeyError):
        store.require(raw)

    second = store.issue("wrk_one")
    third = store.issue("wrk_one")
    assert second != third
    with pytest.raises(KeyError):
        store.require(second)
    clock.value += 121
    with pytest.raises(KeyError):
        store.require(third)
    assert BINDING_TICKET_TTL_SECONDS == 180


def test_unpaired_extension_claims_worker_identity_without_pairing_code_and_ticket_cannot_replay(tmp_path):
    app = app_for(tmp_path)
    store = app.state.linux_workers
    credentials = enrolled(store, "fresh")
    headers = {"X-Worker-ID": credentials["worker_id"], "X-Worker-Token": credentials["worker_token"]}

    with TestClient(app) as client:
        issued = client.post("/api/workers/extension-binding-ticket", headers=headers)
        assert issued.status_code == 200
        ticket = issued.json()["ticket"]
        assert ticket.startswith("wbind_")
        assert ticket not in (tmp_path / "linux_workers.json").read_text(encoding="utf-8")

        claimed = client.post(
            "/api/extensions/worker-bind",
            json={
                "ticket": ticket,
                "device_id": "device-fresh-123",
                "name": "Worker Chrome",
                "browser_name": "Chrome",
                "version": "0.8.1",
                "metadata": {"runtime_id": "runtime-test"},
            },
        )
        assert claimed.status_code == 200
        payload = claimed.json()
        assert payload["bound"] is True
        assert payload["reused"] is False
        assert payload["client_id"].startswith("ext_")
        assert payload["token"]

        worker = store.data["workers"][credentials["worker_id"]]
        assert worker["extension_client_id"] == payload["client_id"]
        assert worker["extension_device_id"] == "device-fresh-123"
        registered = app.state.registry.clients[payload["client_id"]]
        assert registered.metadata["linux_worker_id"] == credentials["worker_id"]
        assert registered.metadata["linux_worker_binding_version"] == 30

        replay = client.post(
            "/api/extensions/worker-bind",
            json={"ticket": ticket, "device_id": "device-fresh-123", "client_id": payload["client_id"], "client_token": payload["token"]},
        )
        assert replay.status_code == 401

        offline = client.post("/api/workers/extension-binding-ticket", headers=headers)
        assert offline.status_code == 200
        assert offline.json()["bound"] is False
        assert offline.json()["current_client_id"] == payload["client_id"]

        app.state.registry.sockets[payload["client_id"]] = object()
        online = client.post("/api/workers/extension-binding-ticket", headers=headers)
        assert online.status_code == 200
        assert online.json()["bound"] is True
        assert online.json()["client_id"] == payload["client_id"]


def test_existing_extension_identity_is_reused_and_cannot_bind_to_two_active_workers(tmp_path):
    app = app_for(tmp_path)
    store = app.state.linux_workers
    first = enrolled(store, "first")
    second = enrolled(store, "second")
    client_id, client_token = asyncio.run(
        app.state.registry.register("Existing", "Chrome", "0.8.1", {"device_id": "existing-device-123"}, device_id="existing-device-123")
    )

    with TestClient(app) as client:
        first_ticket = client.post(
            "/api/workers/extension-binding-ticket",
            headers={"X-Worker-ID": first["worker_id"], "X-Worker-Token": first["worker_token"]},
        ).json()["ticket"]
        claimed = client.post(
            "/api/extensions/worker-bind",
            json={"ticket": first_ticket,"device_id":"existing-device-123","client_id":client_id,"client_token":client_token,"version":"0.8.1"},
        )
        assert claimed.status_code == 200
        assert claimed.json()["reused"] is True
        assert "token" not in claimed.json()

        second_ticket = client.post(
            "/api/workers/extension-binding-ticket",
            headers={"X-Worker-ID": second["worker_id"], "X-Worker-Token": second["worker_token"]},
        ).json()["ticket"]
        conflict = client.post(
            "/api/extensions/worker-bind",
            json={"ticket":second_ticket,"device_id":"existing-device-123","client_id":client_id,"client_token":client_token,"version":"0.8.1"},
        )
        assert conflict.status_code == 409
        assert store.data["workers"][second["worker_id"]]["extension_client_id"] == ""


def test_bridge_readiness_is_authoritative_and_agent_heartbeat_cannot_downgrade_ready(tmp_path):
    store = LinuxWorkerStore(tmp_path)
    credentials = enrolled(store, "ready-worker")
    worker_id = credentials["worker_id"]
    store.record_proxy_success(worker_id, {"protocol":"vless","server":"proxy.example","port":443})
    store.bind_extension(worker_id, "ext_ready", "device-ready-123")
    ready = store.record_extension_status(worker_id, {
        "client_id":"ext_ready","device_id":"device-ready-123","version":"0.8.1","online":True,"connection_enabled":True,
        "metadata":{"chatgpt_login_state":"ready","chatgpt_login_composer_ready":True,"chatgpt_login_confidence":"high","chatgpt_login_strategy":"composer-ready","network_probe_status":"external","network_country_code":"US","account_type":"paid","reserve_window_total":10,"reserve_window_active":2,"reserve_window_target":10,"reserve_window_idle_close_seconds":900},
    })
    assert ready["status"] == "ready"
    assert ready["chatgpt_status"] == "ready"
    assert ready["chrome_bridge_version"] == "0.8.1"

    heartbeat = store.heartbeat(worker_id, {"status":"waiting_login","proxy_status":"connected","chrome_bridge_version":"0.8.1","metadata":{"services":{"xray":True,"xvfb":True,"chrome":True},"host_fact":"preserved"}})
    assert heartbeat["status"] == "ready"
    assert heartbeat["chatgpt_status"] == "ready"
    assert heartbeat["metadata"]["host_fact"] == "preserved"
    assert heartbeat["metadata"]["bridge"]["reserve_window_total"] == 10
    assert heartbeat["metadata"]["bridge"]["reserve_window_active"] == 2
    assert heartbeat["metadata"]["bridge"]["account_type"] == "paid"


def test_extension_binding_uses_session_storage_about_blank_scrub_and_no_pairing_secret():
    source = (ROOT / "chrome_extension" / "background_worker_binding_v30.js").read_text(encoding="utf-8")
    for token in (
        'chrome.storage.session.set({ [PENDING_KEY]: binding })','chrome.storage.session.remove(PENDING_KEY)','url.protocol !== "about:" || url.pathname !== "blank"','chrome.tabs.update(tabId, { url: "about:blank" })','/api/extensions/worker-bind','pairingCode: ""','linuxWorkerBindingVersion: 30','await restoreChatGpt(binding.tabId)',
    ):
        assert token in source
    assert 'chrome.storage.local.set({ [PENDING_KEY]' not in source
    assert "X-Pairing-Code" not in source
    assert "console.log" not in source
    assert "console.warn" not in source
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"background_worker_binding_v30.js"' in entry


def test_agent_binding_ticket_never_enters_argv_or_chatgpt_page_and_has_no_new_listener():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    agent = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    assert 'binding_url = f"about:blank#chat2api-worker-bind={raw_ticket}' in helper
    assert '["xdotool", "-"]' in helper
    assert '_type_url_into_focused_chrome(binding_url, error_name="binding_injection_failed")' in helper
    assert 'script = " ".join(shlex.quote(str(part)) for part in parts)' in helper
    assert "https://chatgpt.com/#chat2api-worker-bind=" not in helper
    assert '"X-Worker-Token": str(config.get("worker_token") or "")' in agent
    assert "/api/workers/extension-binding-ticket" in agent
    assert "inject_worker_binding" in agent
    assert 'AGENT_VERSION = "0.3.2"' in agent
    assert "BINDING_BOUND_POLL_SECONDS = 60.0" in agent
    lowered = agent.lower()
    assert "http.server" not in lowered
    assert ".listen(" not in lowered


def test_binding_versions_are_aligned():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    manifest = (ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 4)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert '"version": "0.8.1"' in manifest
    assert 'agent_version:"0.3.2"' in bootstrap
    assert "install_linux_worker_bridge_binding_patch(app)" in entry
    assert entry.index("install_linux_worker_patch(app)") < entry.index("install_linux_worker_bridge_binding_patch(app)") < entry.index("install_runtime_contract(app)")
