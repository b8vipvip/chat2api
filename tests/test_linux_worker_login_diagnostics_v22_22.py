from pathlib import Path
import subprocess

from app.linux_worker_diagnostics_patch import _patch_bootstrap as patch_diagnostics_bootstrap
from app.linux_worker_diagnostics_patch import _patch_stable_table as patch_diagnostics_table
from app.linux_worker_install_ux_patch import _patch_bootstrap as patch_install_ux_bootstrap
from app.linux_worker_install_ux_patch import _patch_stable_table_js as patch_install_ux_table


ROOT = Path(__file__).resolve().parents[1]


def test_remote_login_uses_loopback_cdp_and_fails_closed_when_navigation_cannot_start():
    login = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")

    for token in (
        'CHROME_DEBUG_URL = os.environ.get("CHAT2API_LOGIN_CHROME_DEBUG_URL", "http://127.0.0.1:9222")',
        'Request(endpoint, data=b"", method="PUT")',
        'return {"ok": True, "method": "cdp"',
        'for _ in range(6):',
        'SESSION.close()',
        '"error": "login_navigation_failed"',
    ):
        assert token in login
    assert '_type_url_into_focused_chrome(LOGIN_URL' not in login
    assert '--remote-debugging-address=127.0.0.1' in launcher
    assert '--remote-debugging-port=9222' in launcher


def test_worker_agent_implements_bounded_diagnostics_command_and_bundle_ships_helper():
    agent = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "linux_worker_diagnostics.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert 'AGENT_VERSION = "0.3.4"' in agent
    assert 'DIAGNOSTICS_HELPER = Path(' in agent
    assert 'if command == "get_logs":' in agent
    assert 'MAX_DIAGNOSTIC_CHARS = 450_000' in agent
    assert 'scripts/linux_worker_diagnostics.sh' in dockerfile
    assert '!scripts/linux_worker_diagnostics.sh' in dockerignore
    assert "last 30 minutes" in helper
    assert "wbind_[REDACTED]" in helper
    assert "journalctl -u \"$unit\" --since '-30 min' -n 220" in helper

    shell = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "linux_worker_diagnostics.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr


def test_bootstrap_installs_fixed_scope_diagnostics_helper_and_sudo_rule():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    patched = patch_diagnostics_bootstrap(patch_install_ux_bootstrap(source))

    assert 'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_diagnostics.sh" /usr/local/sbin/chat2api-worker-diagnostics' in patched
    assert '/usr/local/sbin/chat2api-worker-proxy-apply, /usr/local/sbin/chat2api-worker-diagnostics' in patched
    assert 'rm -f /usr/local/sbin/chat2api-linux-worker-watchdog /usr/local/sbin/chat2api-linux-extension-autoreload /usr/local/sbin/chat2api-worker-proxy-apply /usr/local/sbin/chat2api-worker-diagnostics' in patched


def test_worker_table_adds_one_click_diagnostic_log_download(tmp_path):
    source = (ROOT / "app" / "admin_linux_worker_stable_table.js").read_text(encoding="utf-8")
    patched = patch_diagnostics_table(patch_install_ux_table(source))

    for token in (
        'data-worker-diagnostics-v2222',
        'diagnostics.textContent = "诊断日志"',
        '/api/admin/linux-worker/${encodeURIComponent(workerId)}/diagnostics/logs',
        'await response.blob()',
        'diagnostics.zip',
        'anchor.download = filename',
        'diagnostics.textContent = "已下载"',
        '诊断日志获取失败',
    ):
        assert token in patched

    rendered = tmp_path / "stable-table-v22-22.js"
    rendered.write_text(patched, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(rendered)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_diagnostics_endpoint_is_admin_only_bounded_zip_with_named_logs():
    source = (ROOT / "app" / "linux_worker_diagnostics_patch.py").read_text(encoding="utf-8")

    for token in (
        '@app.get("/api/admin/linux-worker/{worker_id}/diagnostics/logs")',
        'raise HTTPException(401, "Administrator session required")',
        'MAX_DIAGNOSTICS_BYTES = 50 * 1024 * 1024',
        '"worker-runtime.log"',
        '"chrome.log"',
        '"xvfb.log"',
        '"pairing.log"',
        '"extension-sync.log"',
        'media_type="application/zip"',
    ):
        assert token in source


def test_runtime_and_entry_publish_v22_22_diagnostics_patch_last():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    assert 'SERVER_RUNTIME_VERSION = "0.22.22"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'from .linux_worker_diagnostics_patch import install_linux_worker_diagnostics_patch' in entry
    assert 'install_linux_worker_diagnostics_patch(app)' in entry
    assert entry.index('install_linux_worker_repair_command_patch(app)') < entry.index('install_linux_worker_diagnostics_patch(app)')
