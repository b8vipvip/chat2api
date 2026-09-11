from pathlib import Path
import subprocess

from app.linux_worker_console_v123_patch import PATCH_REVISION, _pairing_meta


ROOT = Path(__file__).resolve().parents[1]


def test_v123_device_identity_comes_from_pairing_metadata():
    assert PATCH_REVISION == 123
    worker = {
        "metadata": {
            "worker_pairing": {
                "pairing_id": "pair_demo",
                "name": "TX03",
                "status": "bound",
            }
        }
    }
    assert _pairing_meta(worker)["name"] == "TX03"


def test_new_linux_device_requires_pairing_and_saved_proxy_before_command():
    source = (ROOT / "app" / "linux_worker_console_v123_patch.py").read_text(encoding="utf-8")
    assert 'pairing_id: str' in source
    assert 'proxy_id: str' in source
    assert '@app.post("/api/admin/linux-device-installations")' in source
    assert "请选择一个已启用的配对码" in source
    assert "请选择代理管理中已保存的代理" in source
    assert '"name": str(pairing.name)' in source
    assert '"setup_pairing_id"' in source
    assert '"setup_proxy_id"' in source
    assert '"apply_proxy_config"' in source
    assert 'worker["name"] = str(enrollment_before.get("setup_pairing_name")' in source


def test_visible_linux_device_table_is_single_stable_surface_with_inline_proxy_settings():
    source = (ROOT / "app" / "admin_linux_worker_console_v123.js").read_text(encoding="utf-8")
    assert 'legacyTable.style.display = "none"' in source
    assert 'id="linuxDeviceTableV123"' in source
    assert '<th>代理</th>' in source
    assert 'data-v123-proxy-worker' in source
    assert 'title="代理设置">⚙</button>' in source
    assert 'linuxProxyQuickApplyV123' in source
    assert 'linuxProxyQuickTestV123' in source
    assert 'proxyManager.textContent = "代理管理"' in source
    assert 'first.textContent = "设备名称"' in source
    assert 'createButton.textContent = "新增设备"' in source
    assert '配对码 / 设备名称' in source
    assert 'data-v123-relay="代理"' not in source
    assert 'data-v123-relay="配对码"' not in source


def test_device_name_rename_propagates_to_linux_worker_compatibility_caches():
    source = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    assert 'pairing_meta["name"] = payload.get("name")' in source
    assert 'worker["name"] = str(payload.get("name")' in source
    assert 'install["setup_pairing_name"] = payload.get("name")' in source
    assert 'install_linux_worker_console_v123_patch(app)' in source


def test_window_observer_discovers_physical_chatgpt_window_without_becoming_authority():
    source = (ROOT / "chrome_extension" / "background_window_observer_v90.js").read_text(encoding="utf-8")
    assert 'chrome.windows.getAll({ populate: true })' in source
    assert '"physical-observer-v90"' in source
    assert 'routeByWindow' in source
    assert 'decision_authority: false' in source
    assert 'chrome.windows.create' not in source
    assert 'chrome.windows.remove' not in source
    assert 'conversation_routing.js stays the sole lifecycle owner' in source


def test_v123_javascript_parses():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_worker_console_v123.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
