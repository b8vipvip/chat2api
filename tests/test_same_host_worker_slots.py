from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.linux_workers import LinuxWorkerStore
from app.same_host_worker_sync_patch import WAIT_STATE, install_same_host_worker_sync_patch


ROOT = Path(__file__).resolve().parents[1]


def _enroll(store: LinuxWorkerStore, name: str, hostname: str) -> str:
    enrollment = store.create_enrollment(name)
    result = store.enroll(
        enrollment["code"],
        {
            "device_id": hostname,
            "hostname": hostname,
            "platform": "linux",
            "arch": "x86_64",
            "os_version": "Ubuntu 24.04",
            "agent_version": "0.3.6",
            "chrome_bridge_version": "0.8.35",
        },
    )
    return result["worker_id"]


def test_same_physical_host_can_own_distinct_worker_extension_identities(tmp_path: Path) -> None:
    store = LinuxWorkerStore(tmp_path)
    hostname = "shared-worker-host"
    first = _enroll(store, "Host slot 1", hostname)
    second = _enroll(store, "Host slot 2", hostname)

    store.bind_extension(first, "ext_slot_one", "profile-device-slot-one")
    store.bind_extension(second, "ext_slot_two", "profile-device-slot-two")

    first_row = store.data["workers"][first]
    second_row = store.data["workers"][second]
    assert first_row["device_id"] == hostname
    assert second_row["device_id"] == hostname
    assert first_row["extension_client_id"] == "ext_slot_one"
    assert second_row["extension_client_id"] == "ext_slot_two"
    assert first_row["extension_device_id"] != second_row["extension_device_id"]


def test_same_extension_identity_cannot_be_shared_by_two_active_workers(tmp_path: Path) -> None:
    store = LinuxWorkerStore(tmp_path)
    hostname = "shared-worker-host"
    first = _enroll(store, "Host slot 1", hostname)
    second = _enroll(store, "Host slot 2", hostname)

    store.bind_extension(first, "ext_exclusive", "profile-device-slot-one")
    with pytest.raises(ValueError, match="already bound to another active Linux Worker"):
        store.bind_extension(second, "ext_exclusive", "profile-device-slot-two")


def test_same_host_sync_coalesces_secondary_worker_until_leader_finishes() -> None:
    class Store:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.data = {
                "workers": {
                    "wrk_primary": {
                        "worker_id": "wrk_primary",
                        "device_id": "shared-host",
                        "hostname": "shared-host",
                        "platform": "linux",
                        "created_at": "2026-09-11T00:00:00Z",
                        "metadata": {},
                        "versions_current": False,
                    },
                    "wrk_secondary": {
                        "worker_id": "wrk_secondary",
                        "device_id": "shared-host",
                        "hostname": "shared-host",
                        "platform": "linux",
                        "created_at": "2026-09-11T00:01:00Z",
                        "metadata": {},
                        "versions_current": True,
                    },
                }
            }

        def _save(self) -> None:
            return None

    class Coordinator:
        def __init__(self) -> None:
            self.store = Store()
            self.base_scheduled: list[tuple[str, str, str]] = []

        @staticmethod
        def _sync_meta(worker):
            return dict((worker.get("metadata") or {}).get("server_worker_sync") or {})

        @staticmethod
        def _versions_current(worker):
            return worker.get("versions_current") is True

        def _update_worker_meta(self, worker_id, **values):
            worker = self.store.data["workers"][worker_id]
            metadata = dict(worker.get("metadata") or {})
            sync = dict(metadata.get("server_worker_sync") or {})
            sync.update(values)
            metadata["server_worker_sync"] = sync
            worker["metadata"] = metadata
            return dict(sync)

        def _write_upgrade_state(self, worker_id, *, state, stage, message, percent, reset=False):
            worker = self.store.data["workers"][worker_id]
            metadata = dict(worker.get("metadata") or {})
            metadata["worker_upgrade"] = {
                "state": state,
                "stage": stage,
                "message": message,
                "percent": percent,
                "reset": reset,
            }
            worker["metadata"] = metadata

        async def _schedule_worker(self, worker_id, target_commit, reason):
            self.base_scheduled.append((worker_id, target_commit, reason))

        async def run_once(self):
            return {"ok": True}

    app = FastAPI()
    coordinator = Coordinator()
    app.state.server_worker_sync = coordinator
    app.state.worker_sockets = {
        "wrk_primary": SimpleNamespace(),
        "wrk_secondary": SimpleNamespace(),
    }
    install_same_host_worker_sync_patch(app)

    asyncio.run(coordinator._schedule_worker("wrk_secondary", "commit-1", "worker-payload-changed"))
    secondary = coordinator.store.data["workers"]["wrk_secondary"]
    sync = coordinator._sync_meta(secondary)
    assert coordinator.base_scheduled == []
    assert sync["state"] == WAIT_STATE
    assert sync["shared_runtime_leader"] == "wrk_primary"

    primary = coordinator.store.data["workers"]["wrk_primary"]
    coordinator._update_worker_meta(primary["worker_id"], last_synced_commit="commit-1", state="synced")
    asyncio.run(coordinator._schedule_worker("wrk_secondary", "commit-1", "worker-payload-changed"))
    sync = coordinator._sync_meta(secondary)
    assert coordinator.base_scheduled == []
    assert sync["state"] == "synced"
    assert sync["last_synced_commit"] == "commit-1"


def test_slot_runtime_contract_isolated_and_packaged() -> None:
    installer = (ROOT / "scripts/linux_worker_slot_install.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/linux_worker_slot_chrome_launcher.sh").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts/linux_worker_slot_agent.py").read_text(encoding="utf-8")
    proxy_helper = (ROOT / "scripts/linux_worker_proxy_apply.sh").read_text(encoding="utf-8")
    upgrade_helper = (ROOT / "scripts/linux_worker_upgrade.sh").read_text(encoding="utf-8")
    initialize_helper = (ROOT / "scripts/linux_worker_initialize.sh").read_text(encoding="utf-8")
    diagnostics_helper = (ROOT / "scripts/linux_worker_diagnostics.sh").read_text(encoding="utf-8")
    sync_lifespan = (ROOT / "app/server_worker_sync_lifespan_patch.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    # Deterministic, non-overlapping host-local resources for slot N.
    assert 'PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-$(printf \'%02d\' "$SLOT")"' in installer
    assert 'PROXY_PORT=$((10807 + SLOT))' in installer
    assert 'DISPLAY_NUM=$((98 + SLOT))' in installer
    assert 'CDP_PORT=$((9221 + SLOT))' in installer
    assert 'CONFIG_DIR="/etc/chat2api-worker/${INSTANCE}"' in installer
    assert 'STATE_DIR="/var/lib/chat2api-worker/${INSTANCE}"' in installer

    # Extra slots share extension source read-only; only the primary slot performs
    # central source replacement, avoiding races between multiple autoreload jobs.
    assert "CHAT2API_EXTENSION_CENTRAL_SYNC=0" in installer

    # Every Chrome slot owns its own persistent login/profile and debug endpoint.
    assert '--user-data-dir="$PROFILE_DIR"' in launcher
    assert '--proxy-server="socks5://127.0.0.1:${PROXY_PORT}"' in launcher
    assert '--remote-debugging-port="$CDP_PORT"' in launcher

    # Agent is the current v44 runtime, while local restart/Xray operations are
    # redirected to the slot units and private SOCKS port.
    assert "import linux_worker_agent_v44" in wrapper
    assert 'f"chat2api-xray-{_SUFFIX}.service"' in wrapper
    assert 'f"chat2api-xvfb-{_SUFFIX}.service"' in wrapper
    assert 'f"chat2api-chrome-{_SUFFIX}.service"' in wrapper
    assert 'inbounds[0]["port"] = int(agent.PROXY_PORT)' in wrapper

    # Privileged helpers derive the slot only from a fixed sudo-allowlisted
    # basename and the server coalesces one shared-runtime update per host.
    assert "chat2api-worker-proxy-apply-slot([0-9]+)" in proxy_helper
    assert "chat2api-worker-upgrade-slot([0-9]+)" in upgrade_helper
    assert "chat2api-worker-initialize-slot([0-9]+)" in initialize_helper
    assert "chat2api-worker-diagnostics-slot([0-9]+)" in diagnostics_helper
    assert "CHAT2API_INITIALIZE_HELPER=${INITIALIZE_HELPER}" in installer
    assert "CHAT2API_DIAGNOSTICS_HELPER=${DIAGNOSTICS_HELPER}" in installer
    assert "repair_same_host_slot_privileged_helpers" in upgrade_helper
    assert "restart_same_host_slots" in upgrade_helper
    assert "install_same_host_worker_sync_patch" in sync_lifespan

    # A server deployment must ship all slot helpers in the verified Worker bundle.
    assert "scripts/linux_worker_slot_agent.py" in dockerfile
    assert "scripts/linux_worker_slot_chrome_launcher.sh" in dockerfile
    assert "scripts/linux_worker_slot_install.sh" in dockerfile
