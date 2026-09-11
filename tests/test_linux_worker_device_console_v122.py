from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_v122_source_is_retained_only_as_history_and_not_installed():
    presentation = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    historical = (ROOT / "app" / "linux_worker_device_console_v122_patch.py").read_text(encoding="utf-8")
    assert "def install_linux_worker_device_console_v122_patch" in historical
    assert "install_linux_worker_device_console_v122_patch" not in presentation
    assert "install_linux_worker_console_v123_patch" not in presentation
    assert "install_linux_worker_device_authority_v124_patch(app)" in presentation


def test_v124_no_longer_groups_devices_by_hostname_or_physical_key_guessing():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "_physical_key" not in source
    assert "hostname" not in source
    assert '"device_id": device_id' in source
    assert '"parent_device_id": parent_device_id' in source
    assert '"device_authority_revision": PATCH_REVISION' in source


def test_slot_installer_stays_isolated_and_reports_progress():
    authority = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts" / "linux_worker_slot_install_reported.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "linux_worker_slot_install_reported.sh" in authority
    assert "/opt/chat2api-worker/scripts/linux_worker_slot_install.sh" in wrapper
    assert "/api/workers/install-progress" in wrapper
    assert 'report "installed" "complete"' in wrapper
    assert "linux_worker_slot_install_reported.sh" in dockerfile
    shell = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "linux_worker_slot_install_reported.sh")], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    assert shell.returncode == 0, shell.stderr


def test_worker_management_keeps_device_name_from_pairing_code():
    backend = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    frontend = (ROOT / "app" / "admin_worker_presentation_v66.js").read_text(encoding="utf-8")
    authority_ui = (ROOT / "app" / "admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    assert 'row["device_name"] = by_pairing.get(pairing_id) or fallback_name or None' in backend
    assert 'th.textContent = "设备名称"' in frontend
    assert 'row?.device_name' in frontend
    assert 'first.textContent="设备名称"' in authority_ui


def test_device_authority_does_not_change_request_or_window_capacity_authority():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "max_concurrency" not in source
    assert "max_windows" not in source
    assert "conversation_routing" not in source
