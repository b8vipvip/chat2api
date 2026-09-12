from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_device_table_owns_only_device_actions_and_worker_version() -> None:
    ui = source("app/admin_linux_device_authority_v124.js")
    assert "管理设备 Worker" not in ui
    assert "manageDeviceWorkersV124" not in ui
    assert "<th>Worker版本</th>" in ui
    assert "data-manage-device" in ui
    render_device = ui.split("function renderDevices()", 1)[1].split("function renderManager()", 1)[0]
    assert 'data-worker-action="upgrade"' in render_device
    assert 'data-worker-action="initialize"' not in render_device
    assert "data-login-worker" not in render_device
    assert "data-diagnostics" not in render_device
    assert "管理Worker" in render_device


def test_worker_manager_is_pinned_to_clicked_device_and_routes_by_worker_id() -> None:
    ui = source("app/admin_linux_device_authority_v124.js")
    assert "managerDeviceV124" not in ui
    assert "state.selectedDevice=String(t.dataset.manageDevice" in ui
    manager = ui.split("function renderManager()", 1)[1].split("function renamePairingHeader", 1)[0]
    assert 'data-worker-action="initialize"' in manager
    assert "data-login-worker" in manager
    assert "data-diagnostics" in manager
    assert 'data-worker="${esc(w.worker_id)}"' in manager
    assert 'data-login-worker="${esc(w.worker_id)}"' in manager
    assert 'data-diagnostics="${esc(w.worker_id)}"' in manager


def test_slot_privileged_actions_are_fixed_to_the_worker_slot() -> None:
    slot = source("scripts/linux_worker_slot_install.sh")
    wrapper = source("scripts/linux_worker_slot_agent.py")
    initialize = source("scripts/linux_worker_initialize.sh")
    diagnostics = source("scripts/linux_worker_diagnostics.sh")
    upgrade = source("scripts/linux_worker_upgrade.sh")
    assert "chat2api-worker-initialize-slot([0-9]+)" in initialize
    assert "chat2api-worker-diagnostics-slot([0-9]+)" in diagnostics
    assert 'systemctl stop "$AGENT_UNIT" "$CHROME_UNIT"' in initialize
    assert 'DEBUG_URL="http://127.0.0.1:$((9221 + SLOT))"' in initialize
    assert 'BASE = sys.argv[1].rstrip("/")' in diagnostics
    assert 'agent.DIAGNOSTICS_HELPER = Path(f"/usr/local/sbin/chat2api-worker-diagnostics-{_SUFFIX}")' in wrapper
    assert 'initialize_shim.INITIALIZE_HELPER = Path(f"/usr/local/sbin/chat2api-worker-initialize-{_SUFFIX}")' in wrapper
    assert "${INITIALIZE_HELPER}, ${DIAGNOSTICS_HELPER}" in slot
    assert "repair_same_host_slot_privileged_helpers" in upgrade


def test_v126_release_contract() -> None:
    runtime = source("app/runtime_contract.py")
    manifest = source("chrome_extension/manifest.json")
    agent = source("scripts/linux_worker_agent_v44.py")
    assert 'SERVER_RUNTIME_VERSION = "0.22.82"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.35"' in runtime
    assert '"linux_worker_isolated_management_v126": True' in runtime
    assert '"linux_worker_slot_isolated_ops_v126": True' in runtime
    assert '"version": "0.8.35"' in manifest
    assert 'AGENT_VERSION = "0.3.8"' in agent
