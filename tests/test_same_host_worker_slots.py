from __future__ import annotations

from pathlib import Path

import pytest

from app.linux_workers import LinuxWorkerStore


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
            "agent_version": "0.3.4",
            "chrome_bridge_version": "0.8.31",
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


def test_slot_runtime_contract_isolated_and_packaged() -> None:
    installer = (ROOT / "scripts/linux_worker_slot_install.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/linux_worker_slot_chrome_launcher.sh").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts/linux_worker_slot_agent.py").read_text(encoding="utf-8")
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

    # Agent restarts and generated Xray configs are redirected to the slot units
    # and slot SOCKS port rather than the legacy slot-1 service names/10808.
    assert 'f"chat2api-xray-{_SUFFIX}.service"' in wrapper
    assert 'f"chat2api-xvfb-{_SUFFIX}.service"' in wrapper
    assert 'f"chat2api-chrome-{_SUFFIX}.service"' in wrapper
    assert 'inbounds[0]["port"] = int(agent.PROXY_PORT)' in wrapper

    # A server deployment must ship all slot helpers in the verified Worker bundle.
    assert "scripts/linux_worker_slot_agent.py" in dockerfile
    assert "scripts/linux_worker_slot_chrome_launcher.sh" in dockerfile
    assert "scripts/linux_worker_slot_install.sh" in dockerfile
