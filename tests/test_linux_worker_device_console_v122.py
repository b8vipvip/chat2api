from pathlib import Path
import subprocess

from app.linux_worker_device_console_v122_patch import (
    MAX_SLOT,
    MIN_SLOT,
    PATCH_REVISION,
    _physical_key,
    _slot_number,
)


ROOT = Path(__file__).resolve().parents[1]


def test_device_console_revision_and_slot_bounds():
    assert PATCH_REVISION == 122
    assert MIN_SLOT == 2
    assert MAX_SLOT == 32
    assert _slot_number("slot2") == 2
    assert _slot_number("3") == 3
    assert _slot_number("slot32") == 32
    assert _slot_number("slot33") is None


def test_physical_device_key_groups_same_host_workers():
    first = {"worker_id": "wrk_a", "platform": "linux", "device_id": "TX03", "hostname": "tx03"}
    second = {"worker_id": "wrk_b", "platform": "linux", "device_id": "TX03", "hostname": "tx03"}
    other = {"worker_id": "wrk_c", "platform": "linux", "device_id": "TX04", "hostname": "tx04"}
    assert _physical_key(first) == _physical_key(second)
    assert _physical_key(first) != _physical_key(other)


def test_console_labels_linux_hosts_as_devices_and_exposes_worker_manager():
    source = (ROOT / "app" / "admin_linux_device_workers_v122.js").read_text(encoding="utf-8")
    assert 'title.textContent = "设备列表"' in source
    assert 'nameInput.placeholder = "设备名称"' in source
    assert 'createDevice.textContent = "新增设备"' in source
    assert 'manage.textContent = "管理设备 Worker"' in source
    assert 'document.getElementById("addDeviceWorkerV122")' in source
    assert 'data-v122-action="login"' in source
    assert 'data-v122-action="proxy"' in source
    assert 'data-v122-action="pairing"' in source


def test_device_console_javascript_and_reported_installer_parse():
    js = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_device_workers_v122.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert js.returncode == 0, js.stderr
    shell = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "linux_worker_slot_install_reported.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert shell.returncode == 0, shell.stderr


def test_slot_install_uses_isolated_existing_slot_runtime_and_reports_progress():
    patch = (ROOT / "app" / "linux_worker_device_console_v122_patch.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts" / "linux_worker_slot_install_reported.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "linux_worker_slot_install_reported.sh" in patch
    assert "/opt/chat2api-worker/scripts/linux_worker_slot_install.sh" in wrapper
    assert "/api/workers/install-progress" in wrapper
    assert 'report "installed" "complete"' in wrapper
    assert "linux_worker_slot_install_reported.sh" in dockerfile


def test_worker_management_keeps_pairing_code_device_name_column():
    backend = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    frontend = (ROOT / "app" / "admin_worker_presentation_v66.js").read_text(encoding="utf-8")
    assert 'row["device_name"] = device_name or None' in backend
    assert 'th.textContent = "设备名称"' in frontend
    assert 'row?.device_name' in frontend


def test_device_console_does_not_change_worker_capacity_authority():
    source = (ROOT / "app" / "linux_worker_device_console_v122_patch.py").read_text(encoding="utf-8")
    assert "max_concurrency" not in source
    assert "max_windows" not in source
    assert "conversation_routing" not in source