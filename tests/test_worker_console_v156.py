"""Worker console v156 regression: lifecycle actions, close control and lazy loading."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_login_settings_closes_even_when_required_email_is_blank():
    ui = source("app/admin_unified_workers_v153.js")
    assert 'id="v153-close" type="button"' in ui
    assert 'byId("v153-close").addEventListener("click", () => dialog.close())' in ui
    assert 'dialog.querySelector("form").addEventListener("submit", event => event.preventDefault())' in ui
    assert 'type="submit">关闭' not in ui
    assert "dialog.addEventListener(\"close\"" in ui


def test_linux_tables_are_device_and_worker_scoped():
    ui = source("app/admin_linux_device_authority_v124.js")
    device_header = ui.split('id="linuxDeviceTableV124"', 1)[1].split('</thead>', 1)[0]
    worker_header = ui.split('id="linuxWorkerTableV155"', 1)[1].split('</thead>', 1)[0]
    for forbidden in ("安装命令", "网络", "Worker ID", "ChatGPT"):
        assert f"<th>{forbidden}</th>" not in device_header
    for forbidden in ("版本", "网络"):
        assert f"<th>{forbidden}</th>" not in worker_header
    device_rows = ui.split("function renderDevices()", 1)[1].split("function renderWorkers()", 1)[0]
    worker_rows = ui.split("function renderWorkers()", 1)[1].split("function renderManager()", 1)[0]
    assert 'data-manage-device=' not in device_rows
    assert 'data-device-upgrade=' in device_rows
    assert 'data-device-add-worker=' in device_rows
    assert 'data-proxy-worker=' in device_rows
    assert 'data-worker-action="upgrade"' not in worker_rows
    for token in ('data-linux-worker-enable=', 'data-linux-worker-delete=', 'data-worker-action="initialize"',
                  'data-worker-login-edit=', 'data-login-worker=', 'data-diagnostics='):
        assert token in worker_rows
    assert 'data-linux-worker-enable=' in ui
    assert '"/enabled"' not in ui  # endpoint uses per-Worker path, not a global toggle
    assert "data-linux-worker-delete" in ui


def test_windows_list_omits_version_network_and_has_real_lifecycle_commands():
    windows = source("app/admin_extension_columns.js")
    server = source("app/worker_auto_login_v153_patch.py")
    chrome = source("chrome_extension/background_login_recovery_v153.js")
    unified = source("app/admin_unified_workers_v153.js")
    assert '{key: "version", label: "版本"}' not in windows
    assert '{key: "network", label: "网络"}' not in windows
    for action in ("enable", "disconnect", "delete"):
        assert f'action === "{action}"' in windows
    assert 'data-windows-worker-initialize=' in windows
    assert 'data-windows-worker-remote=' in windows
    assert 'data-worker-login-edit=' in windows
    assert 'data-windows-device-update=' in unified
    assert '"/api/admin/extensions/{client_id}/initialize"' in server
    assert '"/api/admin/extensions/{client_id}/update"' in server
    assert 'admin(request)' in server
    assert '"worker.lifecycle.initialize.v156"' in chrome
    assert '"worker.lifecycle.update_check.v156"' in chrome
    assert 'chrome.runtime.reload()' in chrome
    assert 'chrome.runtime.requestUpdateCheck' in chrome
    assert 'Windows Chrome 扩展目前没有远程桌面画面通道' in windows


def test_console_startup_no_duplicate_three_second_linux_refresh():
    linux = source("app/admin_linux_device_authority_v124.js")
    unified = source("app/admin_unified_workers_v153.js")
    windows = source("app/admin_extension_columns.js")
    assert 'setInterval(()=>{if(section.classList.contains("active")' not in linux
    assert 'renamePairingHeader(); refreshAll(true)' not in linux
    assert '}, 15000);' in unified
    assert 'document.hidden' in unified
    assert '__chat2apiWindowsSnapshotV156' in unified
    assert '__chat2apiWindowsSnapshotV156' in windows


def test_linux_revocation_does_not_look_like_active_worker():
    backend = source("app/linux_worker_device_authority_v124_patch.py")
    assert 'if source.get("revoked_at"):' in backend
    assert 'continue  # Revoked Workers are not active device slots.' in backend


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_console_javascript_syntax():
    for p in ("app/admin_linux_device_authority_v124.js",
              "app/admin_unified_workers_v153.js",
              "app/admin_extension_columns.js",
              "chrome_extension/background_login_recovery_v153.js"):
        result = subprocess.run(["node", "--check", str(ROOT / p)],
                                capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
