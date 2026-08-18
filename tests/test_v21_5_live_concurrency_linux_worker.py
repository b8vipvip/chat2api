from pathlib import Path

from app.v21_5_patch import _active_api_calls


ROOT = Path(__file__).resolve().parents[1]


class FakeBroker:
    client_active_requests = {
        "ext_a": {"req_1": 1, "req_2": 1},
        "ext_b": {},
    }


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_active_api_calls_counts_current_requests_not_sticky_api_keys():
    broker = FakeBroker()
    assert _active_api_calls(broker, "ext_a") == 2
    assert _active_api_calls(broker, "ext_b") == 0
    assert _active_api_calls(broker, "missing") == 0


def test_v21_5_is_installed_after_concurrency_and_before_runtime_contract():
    entry = read(ROOT / "app" / "entry.py")
    assert "install_v21_5_patch(app)" in entry
    assert entry.index("install_v21_4_model_contract_patch(app)") < entry.index("install_v21_5_patch(app)")
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_runtime_contract(app)")


def test_extension_console_uses_live_concurrency_and_one_second_polling():
    source = read(ROOT / "app" / "admin_v21_5.js")
    assert '"API 调用数（实时并发）"' in source
    assert "active_api_calls" in source
    assert "capacity?.active_requests" in source
    assert "const POLL_MS = 1000" in source
    assert 'api("/api/admin/extensions")' in source
    assert "bound_api_keys" not in source


def test_linux_worker_installer_uses_systemd_xray_xvfb_and_persistent_chrome_profile():
    script = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    for token in (
        "chat2api-xray.service",
        "chat2api-xvfb.service",
        "chat2api-chrome.service",
        "127.0.0.1:${PROXY_PORT}",
        "--password-store=basic",
        "--proxy-server=socks5://127.0.0.1:${PROXY_PORT}",
        "--user-data-dir=${PROFILE_DIR}",
        "systemctl enable chat2api-xray.service chat2api-xvfb.service chat2api-chrome.service",
    ):
        assert token in script
    assert "--no-sandbox" not in script
    assert "pkill -TERM -u \"${WORKER_USER}\" -f \"user-data-dir=${PROFILE_DIR}\"" in script


def test_linux_worker_installer_captures_geodata_before_stopping_live_xray():
    script = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    for token in (
        "find_geodata()",
        "geosite.dat",
        "geoip.dat",
        'xray_dir="$(dirname "${xray_bin}")"',
        '"${xray_bin}" run -test -c "${WORKER_CONFIG_DIR}/xray-config.json"',
        "The live proxy has not been stopped.",
    ):
        assert token in script
    copy_index = script.index('"${xray_dir}/geosite.dat"')
    preflight_index = script.index('run -test -c "${WORKER_CONFIG_DIR}/xray-config.json"')
    kill_index = script.index('kill "${proxy_pid}"')
    assert copy_index < preflight_index < kill_index


def test_ci_checks_new_admin_js_and_linux_installer():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    assert "node --check app/admin_v21_5.js" in workflow
    assert "bash -n scripts/install_linux_worker_autostart.sh" in workflow
