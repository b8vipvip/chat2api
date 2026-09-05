from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.linux_worker_upgrade_patch import TARGET_AGENT_VERSION
from app.linux_workers import LinuxWorkerStore
from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION
from app.server_worker_sync_patch import ServerWorkerSyncCoordinator, worker_update_path

PREVIOUS = "1" * 40
TARGET = "2" * 40


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _worker(*, agent: str = TARGET_AGENT_VERSION, bundle: str = CHROME_BRIDGE_BUNDLE_VERSION) -> dict:
    return {"worker_id":"wrk_test","name":"ubuntu03","token_hash":"test","revoked_at":None,"created_at":"2026-08-29T00:00:00Z","last_seen_at":"2026-08-29T00:00:00Z","status":"ready","network_status":"ok","proxy_status":"connected","chatgpt_status":"ready","extension_client_id":"ext_test","extension_device_id":"device_test","hostname":"ubuntu03","platform":"linux","arch":"x86_64","os_version":"Ubuntu","agent_version":agent,"chrome_bridge_version":bundle,"metadata":{}}


def _coordinator(tmp_path: Path, *, worker: dict, online: bool = True):
    store = LinuxWorkerStore(tmp_path)
    store.data["workers"][worker["worker_id"]] = worker
    store._save()
    calls: list[tuple[str, str]] = []
    async def send(worker_id: str, command: str, arguments: dict, *, wait: bool, timeout: float):
        calls.append((worker_id, command))
        return {"accepted": True, "result": {"ok": True, "scheduled": True, "unit": "chat2api-upgrade"}}
    state = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), linux_workers=store, worker_sockets={worker["worker_id"]: object()} if online else {}, send_linux_worker_command=send)
    app = SimpleNamespace(state=state)
    _write(tmp_path / "deployment.json", {"commit": TARGET, "previous_commit": PREVIOUS})
    _write(tmp_path / "admin-update-status.json", {"status": "succeeded"})
    return ServerWorkerSyncCoordinator(app), store, calls


def _decision(required: bool):
    async def value(previous: str, target: str) -> dict:
        assert previous == PREVIOUS
        assert target == TARGET
        return {"known":True,"required":required,"changed_paths":["chrome_extension/background_entry.js"] if required else ["app/playground_random_prompt_patch.py"],"worker_changed_paths":["chrome_extension/background_entry.js"] if required else [],"compare_error":"","truncated":False}
    return value


def test_worker_update_path_distinguishes_worker_payload_from_server_only_changes() -> None:
    assert worker_update_path("chrome_extension/background_entry.js")
    assert worker_update_path("scripts/linux_worker_upgrade.sh")
    assert worker_update_path("scripts/linux_extension_autoreload.sh")
    assert worker_update_path("scripts/bootstrap_linux_worker.sh")
    assert worker_update_path("app/linux_worker_upgrade_patch.py")
    assert not worker_update_path("app/playground_random_prompt_patch.py")
    assert not worker_update_path("app/admin_server_update.js")
    assert not worker_update_path("README.md")


def test_server_health_check_must_finish_before_worker_sync(tmp_path: Path) -> None:
    coordinator, _, calls = _coordinator(tmp_path, worker=_worker(bundle="0.8.7"), online=True)
    _write(tmp_path / "admin-update-status.json", {"status": "running", "stage": "health"})
    result = asyncio.run(coordinator.run_once())
    assert result["state"] == "waiting-server"
    assert calls == []


def test_stale_worker_is_upgraded_even_when_server_commit_is_server_only(tmp_path: Path) -> None:
    coordinator, store, calls = _coordinator(tmp_path, worker=_worker(bundle="0.8.7"), online=True)
    coordinator._compare_decision = _decision(False)
    result = asyncio.run(coordinator.run_once())
    assert calls == [("wrk_test", "upgrade_worker")]
    assert result["state"] == "syncing"
    assert result["scheduled"] == 1
    sync = store.data["workers"]["wrk_test"]["metadata"]["server_update_sync"]
    assert sync["state"] == "scheduled"
    assert sync["reason"] == "version-mismatch"
    assert sync["target_commit"] == TARGET


def test_current_worker_is_not_touched_for_server_only_update(tmp_path: Path) -> None:
    coordinator, _, calls = _coordinator(tmp_path, worker=_worker(), online=True)
    coordinator._compare_decision = _decision(False)
    result = asyncio.run(coordinator.run_once())
    assert calls == []
    assert result["state"] == "completed"
    assert result["up_to_date"] == 1
    assert result["worker_update_required_by_commit"] is False


def test_worker_payload_change_forces_refresh_even_when_versions_match(tmp_path: Path) -> None:
    coordinator, store, calls = _coordinator(tmp_path, worker=_worker(), online=True)
    coordinator._compare_decision = _decision(True)
    result = asyncio.run(coordinator.run_once())
    assert calls == [("wrk_test", "upgrade_worker")]
    assert result["worker_update_required_by_commit"] is True
    sync = store.data["workers"]["wrk_test"]["metadata"]["server_update_sync"]
    assert sync["reason"] == "worker-payload-changed"
    assert sync["state"] == "scheduled"


def test_offline_worker_stays_pending_and_updates_after_reconnect(tmp_path: Path) -> None:
    coordinator, store, calls = _coordinator(tmp_path, worker=_worker(bundle="0.8.7"), online=False)
    coordinator._compare_decision = _decision(False)
    first = asyncio.run(coordinator.run_once())
    assert calls == []
    assert first["pending_offline"] == 1
    assert store.data["workers"]["wrk_test"]["metadata"]["server_update_sync"]["state"] == "pending-offline"
    coordinator.app.state.worker_sockets["wrk_test"] = object()
    second = asyncio.run(coordinator.run_once())
    assert calls == [("wrk_test", "upgrade_worker")]
    assert second["scheduled"] == 1


def test_force_refresh_is_completed_only_after_upgrade_terminal_success(tmp_path: Path) -> None:
    coordinator, store, calls = _coordinator(tmp_path, worker=_worker(), online=True)
    coordinator._compare_decision = _decision(True)
    first = asyncio.run(coordinator.run_once())
    assert first["scheduled"] == 1
    assert calls == [("wrk_test", "upgrade_worker")]
    with store._lock:
        upgrade = store.data["workers"]["wrk_test"]["metadata"]["worker_upgrade"]
        upgrade["state"] = "succeeded"
        upgrade["stage"] = "completed"
        upgrade["percent"] = 100
        upgrade["completed_at"] = "2026-08-29T23:59:59Z"
        store._save()
    second = asyncio.run(coordinator.run_once())
    sync = store.data["workers"]["wrk_test"]["metadata"]["server_update_sync"]
    assert second["state"] == "completed"
    assert second["up_to_date"] == 1
    assert sync["state"] == "synced"
    assert sync["last_synced_commit"] == TARGET
    assert len(calls) == 1


def test_runtime_and_entry_expose_auto_sync_contract() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.64"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.28"
    entry = Path(__file__).resolve().parents[1].joinpath("app", "entry.py").read_text(encoding="utf-8")
    assert "install_server_worker_sync_patch(app)" in entry
    assert entry.index("install_linux_worker_upgrade_patch(app)") < entry.index("install_server_worker_sync_patch(app)")
    assert entry.index("install_server_update_patch(app)") < entry.index("install_server_worker_sync_patch(app)")
