from pathlib import Path
import subprocess

from app.linux_worker_device_authority_v124_patch import MAX_SLOT, MIN_SLOT, PATCH_REVISION, _is_device_install, _is_slot_install


ROOT = Path(__file__).resolve().parents[1]


def test_v124_install_records_are_the_only_linux_device_authority():
    assert PATCH_REVISION == 124
    assert MIN_SLOT == 2
    assert MAX_SLOT == 32
    device = {"metadata": {"device_authority_revision": 124, "install_kind": "device"}}
    slot = {"metadata": {"device_authority_revision": 124, "install_kind": "worker_slot"}}
    legacy = {"metadata": {"device_authority_revision": 123, "install_kind": "device"}}
    assert _is_device_install(device) is True
    assert _is_slot_install(slot) is True
    assert _is_device_install(legacy) is False


def test_v124_new_device_requires_pairing_and_saved_proxy_before_command():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert '@app.post("/api/admin/linux-devices")' in source
    assert 'pairing_id = str(body.get("pairing_id")' in source
    assert 'proxy_id = str(body.get("proxy_id")' in source
    assert "请选择启用且未配对的设备码" in source
    assert "代理不存在，请先在代理管理中添加" in source
    assert '"device_name": device_name' in source
    assert '"device_authority_revision": PATCH_REVISION' in source
    assert '"install_kind": "device" if slot == 1 else "worker_slot"' in source


def test_v124_console_is_not_a_hidden_legacy_table_or_action_relay():
    source = (ROOT / "app" / "admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    assert 'id="linuxDeviceTableV124"' in source
    assert '<th>代理</th>' in source
    assert 'title.textContent = "设备名称"' not in source
    assert 'first.textContent="设备名称"' in source
    assert '代理管理' in source
    assert '新增设备' in source
    assert '管理设备 Worker' in source
    assert 'data-proxy-worker' in source
    assert '⚙' in source
    assert 'relayLegacyAction' not in source
    assert 'linuxWorkerRows' not in source
    assert 'legacyTable.style.display' not in source


def test_worker_presentation_installs_v124_and_retires_v122_v123_runtime_owners():
    source = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    assert "install_linux_worker_device_authority_v124_patch(app)" in source
    assert "install_linux_worker_device_console_v122_patch" not in source
    assert "install_linux_worker_console_v123_patch" not in source
    assert "v122/v123" in source


def test_v124_html_middleware_removes_historical_linux_console_assets_instead_of_hiding_them():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "LEGACY_LINUX_ASSET_RE.sub" in source
    assert "cannot poll, render, relay actions, or make device decisions" in source
    assert "style.display" not in source


def test_legacy_cleanup_is_explicit_and_preserves_pairing_and_proxy_catalogs():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert '@app.delete("/api/admin/linux-legacy-records")' in source
    assert '"PURGE_LEGACY_LINUX"' in source
    assert "_delete_worker" in source
    assert "_delete_install" in source
    assert "pairings.delete" not in source
    assert "linux_worker_proxy_catalog.delete" not in source


def test_v124_javascript_and_python_parse():
    js = subprocess.run(["node", "--check", str(ROOT / "app" / "admin_linux_device_authority_v124.js")], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    assert js.returncode == 0, js.stderr
    py = subprocess.run(["python", "-m", "py_compile", str(ROOT / "app" / "linux_worker_device_authority_v124_patch.py")], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    assert py.returncode == 0, py.stderr


def test_window_observer_still_only_observes_physical_chatgpt_windows():
    source = (ROOT / "chrome_extension" / "background_window_observer_v90.js").read_text(encoding="utf-8")
    assert 'chrome.windows.getAll({ populate: true })' in source
    assert '"physical-observer-v90"' in source
    assert 'decision_authority: false' in source
    assert 'chrome.windows.create' not in source
    assert 'chrome.windows.remove' not in source
