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


def test_extension_console_uses_canonical_worker_settings_column():
    retired = read(ROOT / "app" / "admin_v21_5.js")
    canonical = read(ROOT / "app" / "admin_extension_columns.js")
    behavior = read(ROOT / "app" / "admin_worker_limits_clipboard_v121.js")

    assert 'retired: true' in retired
    assert 'structural_owner: false' in retired
    assert 'delegated_to: "admin_extension_columns-v152"' in retired
    assert 'renderer: "canonical-worker-list-v152"' in retired
    assert "data-worker-window-editor" not in retired
    assert "data-worker-max" not in retired
    assert "data-worker-reserve" not in retired
    assert "data-worker-save" not in retired
    assert "data-worker-refresh" not in retired
    assert 'api("/api/admin/extensions")' not in retired
    assert 'api("/api/admin/capacity-v57")' in retired  # API-key editor only
    assert "data-key-max" in retired
    assert "data-key-save" in retired
    assert "showActionResult" in retired
    assert "setInterval(" not in retired

    assert '{key: "worker_settings", label: "并发 / 备用设置"}' in canonical
    assert '{key: "occupancy", label: "请求 / 备用窗口"}' in canonical
    assert 'data-chat2api-structural-owner="worker-settings-v152"' in canonical
    assert "data-v121-concurrency" in canonical
    assert "data-v121-windows" in canonical
    assert "data-v121-save-limits" in canonical
    assert 'data-worker-live' not in canonical
    assert 'data-worker-platform' not in canonical
    assert '{key: "bound_api_keys"' not in canonical
    assert 'data-chat2api-column-key="bound_api_keys"' not in canonical

    assert '/api/admin/extensions/${encodeURIComponent(clientId)}/concurrency' in behavior
    assert '/api/admin/extensions/${encodeURIComponent(clientId)}/capacity/apply' in behavior
    assert '/api/admin/extensions/${encodeURIComponent(clientId)}/windows/limit' in behavior
    assert 'method:"PUT"' in behavior
    assert 'method:"POST"' in behavior
    assert 'renderer: "canonical-worker-list-v152"' in behavior


def test_live_concurrency_summary_uses_runtime_limit_for_each_client():
    source = read(ROOT / "app" / "v21_5_patch.py")
    assert 'limit_for = runtime.get("limit_for")' in source
    assert "configured_limit = int(limit_for(client_id))" in source
    assert 'row["concurrency_limit_source"]' in source
    assert 'row["default_max_concurrency"]' in source


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
        "systemctl enable \\",
    ):
        assert token in script
    enable_index = script.index("systemctl enable \\")
    enable_block_end = script.index("systemctl restart chat2api-xray.service", enable_index)
    enable_block = script[enable_index:enable_block_end]
    for unit in (
        "chat2api-xray.service",
        "chat2api-xvfb.service",
        "chat2api-chrome.service",
    ):
        assert unit in enable_block
    assert "--no-sandbox" not in script
    assert "pkill -TERM -u \"${WORKER_USER}\" -f \"user-data-dir=${PROFILE_DIR}\"" in script


def test_linux_worker_installer_captures_geodata_before_stopping_live_xray():
    script = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    for token in (
        "find_geodata()",
        "geosite.dat",
        "geoip.dat",
        'xray_dir="$(dirname "${xray_bin}")"',
        'captured_config="${WORKER_CONFIG_DIR}/xray-config.json"',
        '"${xray_bin}" run -test -c "${captured_config}"',
        "The live proxy has not been stopped.",
    ):
        assert token in script
    copy_index = script.index('"${xray_dir}/geosite.dat"')
    preflight_index = script.index('run -test -c "${captured_config}"')
    kill_index = script.index('kill "${proxy_pid}"')
    assert copy_index < preflight_index < kill_index


def test_ci_checks_new_admin_js_and_linux_installer():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    assert "node --check app/admin_v21_5.js" in workflow
    assert "bash -n scripts/install_linux_worker_autostart.sh" in workflow