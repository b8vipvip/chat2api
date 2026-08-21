import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from linux_worker_proxy import build_xray_config  # noqa: E402


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_real_world_vless_ws_tls_link_shape_builds_expected_xray_stream_settings():
    link = (
        "vless://11111111-1111-1111-1111-111111111111@us03.example.com:443"
        "?encryption=none&security=tls&sni=us03.example.com&fp=chrome"
        "&type=ws&host=us03.example.com&path=%2Fabc123#us03"
    )
    config, summary = build_xray_config(link)
    outbound = config["outbounds"][0]
    assert outbound["protocol"] == "vless"
    assert outbound["settings"]["vnext"][0]["address"] == "us03.example.com"
    assert outbound["settings"]["vnext"][0]["port"] == 443
    assert outbound["streamSettings"]["network"] == "ws"
    assert outbound["streamSettings"]["security"] == "tls"
    assert outbound["streamSettings"]["tlsSettings"]["serverName"] == "us03.example.com"
    assert outbound["streamSettings"]["tlsSettings"]["fingerprint"] == "chrome"
    assert outbound["streamSettings"]["wsSettings"]["path"] == "/abc123"
    assert outbound["streamSettings"]["wsSettings"]["host"] == "us03.example.com"
    assert "headers" not in outbound["streamSettings"]["wsSettings"]
    assert summary == {
        "protocol": "vless",
        "server": "us03.example.com",
        "port": 443,
        "transport": "ws",
        "security": "tls",
    }
    assert "11111111-1111" not in str(summary)


def test_worker_proxy_test_rejects_default_freedom_and_login_has_worker_side_gate():
    source = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    assert 'AGENT_VERSION = "0.3.4"' in source
    assert 'if not proxy_configured():' in source
    assert '"error": "proxy_not_configured"' in source
    assert 'if command == "open_login_session":' in source
    assert 'args.get("proxy_prevalidated") is True and proxy_configured()' in source
    assert '"error": "proxy_required_for_login"' in source


def test_worker_bridge_binding_is_independent_from_chatgpt_proxy_probe():
    source = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    binding = source.split("async def _binding_loop", 1)[1].split("async def main", 1)[0]
    assert "_request_binding_ticket" in binding
    assert "inject_worker_binding" in binding
    assert "if not proxy_configured():" not in binding
    assert "proxy = await asyncio.to_thread(_proxy_test)" not in binding
    assert "if not proxy.get(\"ok\")" not in binding
    assert "session_active()" in binding
    assert 'service_active("chat2api-chrome.service")' in binding


def test_bootstrap_upgrade_preserves_identity_and_chrome_starts_chatgpt():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")
    assert "--upgrade) UPGRADE_ONLY=1" in source
    assert "use --upgrade only on an already enrolled Worker" in source
    assert "升级模式要求现有有效 Worker 身份" in source
    assert 'agent_version:"0.3.2"' in source
    chrome_unit = source.split("cat >/etc/systemd/system/chat2api-chrome.service", 1)[1].split("\nUNIT\n", 1)[0]
    assert "linux_worker_chrome_launcher.sh" in chrome_unit
    assert "https://chatgpt.com/" not in chrome_unit
    assert 'CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"' in launcher
    assert '"$CHATGPT_URL"' in launcher
    assert "PROFILE_DIR" in source
    assert 'rm -rf /opt/chat2api-worker-venv "$WORKER_DIR" /etc/chat2api-worker /var/lib/chat2api-worker' in source


def test_proxy_helper_reports_safe_stage_diagnostics_without_candidate_contents():
    source = (ROOT / "scripts" / "linux_worker_proxy_apply.sh").read_text(encoding="utf-8")
    for stage in (
        "privilege_check",
        "xray_config_test",
        "restart_xray",
        "wait_socks_listener",
        "chatgpt_connectivity_test",
        "complete",
    ):
        assert stage in source
    assert '"stage":"%s"' in source
    assert "proxy_helper_failed" in source
    assert "xray_restart_failed_rollback_failed" in source
    assert "proxy_connectivity_test_failed_rollback_failed" in source
    assert 'cat >"${candidate}"' in source
    assert 'cat "${candidate}"' not in source


def test_server_enforces_persisted_proxy_and_live_preflight_before_opening_login():
    source = (ROOT / "app" / "linux_worker_patch.py").read_text(encoding="utf-8")
    assert 'PATCH_VERSION = "0.22.8"' in source
    assert "PROXY_LOGIN_REQUIRED" in source
    assert "_worker_has_configured_proxy" in source
    block = source.split("async def open_worker_login_session", 1)[1].split("@app.get", 1)[0]
    assert 'if not _worker_has_configured_proxy(worker):' in block
    assert '"test_proxy"' in block
    assert '"open_login_session"' in block
    assert '"proxy_prevalidated": True' in block
    assert block.index('"test_proxy"') < block.index('"open_login_session"')
    assert '"proxy_verified": True' in block
    assert "_proxy_error_text" in source
    assert "helper_exit_code" in source


def test_admin_disables_login_until_proxy_is_recorded_and_preflights_before_modal():
    source = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    assert "const proxyReady = worker =>" in source
    assert 'canLogin ? "登录" : "登录（先配代理）"' in source
    assert 'canLogin ? "" : "disabled"' in source
    assert "检查代理…" in source
    assert "代理已验证，现在可以登录" in source
    block = source.split("const openLoginDialog = async", 1)[1].split("const remotePoint", 1)[0]
    assert "/login-session" in block
    assert "loginDialog.showModal()" in block
    assert block.index("await request(") < block.index("loginDialog.showModal()")


def test_runtime_bumped_without_chrome_bridge_bump():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 8)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
