from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_linux_device_rows_are_physical_device_level_only() -> None:
    ui = source("app/admin_linux_device_authority_v124.js")
    device_header = ui.split('id="linuxDeviceTableV124"', 1)[1].split("</thead>", 1)[0]
    assert "<th>设备名称</th>" in device_header
    assert "<th>Worker数量</th>" in device_header
    assert "<th>Worker ID</th>" not in device_header
    assert "<th>ChatGPT</th>" not in device_header
    device = ui.split("function renderDevices()", 1)[1].split("function renderWorkers()", 1)[0]
    assert "state.devices.map(" in device
    assert "workers.forEach" not in device
    assert "data-linux-device-id" in device
    assert "data-device-upgrade" in device
    assert "data-device-add-worker" in device
    assert "data-manage-device" not in device
    assert 'data-worker-action="initialize"' not in device
    assert "data-login-worker" not in device
    assert "data-diagnostics" not in device


def test_linux_worker_list_owns_worker_actions_without_manager_modal_duplication() -> None:
    ui = source("app/admin_linux_device_authority_v124.js")
    workers = ui.split("function renderWorkers()", 1)[1].split("function renderManager()", 1)[0]
    manager = ui.split("function renderManager()", 1)[1].split("function renamePairingHeader", 1)[0]
    assert 'id="linuxWorkerTableV155"' in ui
    assert "data-linux-worker-id" in workers
    for action in ('data-worker-action="initialize"', 'data-linux-worker-enable',
                   'data-login-worker', 'data-diagnostics', 'data-worker-login-edit', 'data-linux-worker-delete'):
        assert action in workers
        assert action not in manager
    assert "远程</button>" in workers
    assert 'body:{mode:"remote"}' in ui
    assert "setTimeout(closeLogin,650)" not in ui
    assert 'state.selectedDevice=String(t.dataset.manageDevice' in ui


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


def test_v127_release_contract() -> None:
    runtime = source("app/runtime_contract.py")
    manifest = source("chrome_extension/manifest.json")
    controller = source("scripts/linux_worker_device_controller.py")
    assert 'SERVER_RUNTIME_VERSION = "0.22.114"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.114"' in runtime
    assert '"linux_worker_isolated_management_v126": False' in runtime
    assert '"linux_worker_slot_isolated_ops_v126": False' in runtime
    assert '"linux_worker_device_controller_agent_v127": True' in runtime
    assert '"linux_worker_shared_device_pairing_v127": True' in runtime
    assert '"linux_worker_shared_device_proxy_v127": True' in runtime
    assert '"linux_worker_profile_only_isolation_v127": True' in runtime
    assert '"version": "0.22.114"' in manifest
    assert 'AGENT_VERSION = "0.3.10"' in controller
