import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from linux_worker_proxy import ProxyConfigError, build_xray_config  # noqa: E402
from app.linux_workers import LinuxWorkerStore  # noqa: E402


VMESS_LINK = "vmess://eyJ2IjoiMiIsInBzIjoidGVzdCIsImFkZCI6InZtLmV4YW1wbGUuY29tIiwicG9ydCI6IjQ0MyIsImlkIjoiMTExMTExMTEtMTExMS0xMTExLTExMTEtMTExMTExMTExMTExIiwiYWlkIjoiMCIsInNjeSI6ImF1dG8iLCJuZXQiOiJ3cyIsInR5cGUiOiJub25lIiwiaG9zdCI6ImNkbi5leGFtcGxlLmNvbSIsInBhdGgiOiIvd3MiLCJ0bHMiOiJ0bHMiLCJzbmkiOiJ2bS5leGFtcGxlLmNvbSJ9"


def _proxy_outbound(config):
    assert config["inbounds"][0]["listen"] == "127.0.0.1"
    assert config["inbounds"][0]["port"] == 10808
    assert config["outbounds"][1] == {"protocol": "freedom", "tag": "direct"}
    return config["outbounds"][0]


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_vless_reality_share_link_builds_xray_without_leaking_credentials_in_summary():
    link = "vless://11111111-1111-1111-1111-111111111111@vless.example.com:443?type=tcp&security=reality&sni=www.example.com&fp=chrome&pbk=PUBLIC_KEY&sid=abcd&flow=xtls-rprx-vision#node"
    config, summary = build_xray_config(link)
    outbound = _proxy_outbound(config)
    assert outbound["protocol"] == "vless"
    assert outbound["settings"]["vnext"][0]["users"][0]["id"] == "11111111-1111-1111-1111-111111111111"
    assert outbound["streamSettings"]["security"] == "reality"
    assert outbound["streamSettings"]["realitySettings"]["publicKey"] == "PUBLIC_KEY"
    assert summary == {"protocol":"vless","server":"vless.example.com","port":443,"transport":"tcp","security":"reality"}
    assert "11111111" not in json.dumps(summary)
    assert "PUBLIC_KEY" not in json.dumps(summary)


def test_vmess_ws_tls_share_link_builds_xray():
    config, summary = build_xray_config(VMESS_LINK)
    outbound = _proxy_outbound(config)
    assert outbound["protocol"] == "vmess"
    assert outbound["settings"]["vnext"][0]["address"] == "vm.example.com"
    assert outbound["streamSettings"]["network"] == "ws"
    assert outbound["streamSettings"]["wsSettings"]["host"] == "cdn.example.com"
    assert "headers" not in outbound["streamSettings"]["wsSettings"]
    assert outbound["streamSettings"]["tlsSettings"]["serverName"] == "vm.example.com"
    assert summary["protocol"] == "vmess"
    assert summary["security"] == "tls"


def test_trojan_ws_tls_share_link_builds_xray_and_sanitized_summary():
    link = "trojan://super-secret-password@trojan.example.com:443?type=ws&security=tls&sni=edge.example.com&host=cdn.example.com&path=%2Ftr#node"
    config, summary = build_xray_config(link)
    outbound = _proxy_outbound(config)
    assert outbound["protocol"] == "trojan"
    assert outbound["settings"]["servers"][0]["password"] == "super-secret-password"
    assert outbound["streamSettings"]["wsSettings"]["path"] == "/tr"
    assert outbound["streamSettings"]["wsSettings"]["host"] == "cdn.example.com"
    assert "super-secret-password" not in json.dumps(summary)
    assert summary["server"] == "trojan.example.com"


def test_shadowsocks_sip002_and_legacy_share_links_build_xray():
    sip002 = "ss://YWVzLTI1Ni1nY206c2VjcmV0LXBhc3M@ss.example.com:8388#node"
    config, summary = build_xray_config(sip002)
    outbound = _proxy_outbound(config)
    assert outbound["protocol"] == "shadowsocks"
    server = outbound["settings"]["servers"][0]
    assert server["method"] == "aes-256-gcm"
    assert server["password"] == "secret-pass"
    assert summary["protocol"] == "ss"
    assert "secret-pass" not in json.dumps(summary)

    legacy = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXRAc3MuZXhhbXBsZS5jb206ODM4OA"
    config2, summary2 = build_xray_config(legacy)
    server2 = _proxy_outbound(config2)["settings"]["servers"][0]
    assert server2["method"] == "chacha20-ietf-poly1305"
    assert server2["address"] == "ss.example.com"
    assert summary2["port"] == 8388


def test_unsupported_or_multiline_proxy_links_are_rejected():
    for link in ("https://subscription.example/config", "socks5://example.com:1080", "vless://x\nsecret"):
        try:
            build_xray_config(link)
        except ProxyConfigError:
            pass
        else:
            raise AssertionError(f"expected ProxyConfigError for {link!r}")


def test_worker_store_persists_only_sanitized_proxy_summary(tmp_path):
    store = LinuxWorkerStore(tmp_path)
    enrollment = store.create_enrollment("US Proxy")
    credentials = store.enroll(enrollment["code"], {"hostname": "worker-1"})
    worker_id = credentials["worker_id"]
    public = store.record_proxy_success(worker_id, {"protocol":"vless","server":"proxy.example.com","port":443,"transport":"ws","security":"tls","password":"must-not-persist","uuid":"must-not-persist-either"})
    assert public["proxy_status"] == "connected"
    assert public["status"] == "waiting_login"
    raw = store.path.read_text(encoding="utf-8")
    assert "must-not-persist" not in raw
    assert "proxy.example.com" in raw


def test_proxy_apply_helper_is_transactional_fixed_path_and_secret_safe():
    source = (ROOT / "scripts" / "linux_worker_proxy_apply.sh").read_text(encoding="utf-8")
    for token in (
        'XRAY_CONFIG="/etc/chat2api-worker/xray.json"',
        '"${XRAY_BIN}" run -test -c "${candidate}"',
        'cp -p "${XRAY_CONFIG}" "${backup}"',
        'install -o root -g chat2api -m 640 "${candidate}" "${XRAY_CONFIG}"',
        'rollback()',
        'socks5h://127.0.0.1:${PROXY_PORT}',
        'systemctl restart "${CHROME_UNIT}"',
    ):
        assert token in source
    assert "CHAT2API_XRAY_CONFIG" not in source
    assert "echo \"${candidate}" not in source


def test_bootstrap_installs_only_exact_privileged_proxy_helper_and_writable_mount_exception():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert 'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" /usr/local/sbin/chat2api-worker-proxy-apply' in source
    assert "/usr/local/sbin/chat2api-worker-proxy-apply" in source
    assert "NOPASSWD: ALL" not in source
    agent_unit = source.split("cat >/etc/systemd/system/chat2api-worker-agent.service", 1)[1].split("\nUNIT\n", 1)[0]
    assert "ProtectSystem=strict" in agent_unit
    assert "ReadWritePaths=/etc/chat2api-worker" in agent_unit
    assert "\nNoNewPrivileges=true\n" not in agent_unit


def test_control_plane_waits_for_worker_results_and_exposes_proxy_endpoints():
    source = (ROOT / "app" / "linux_worker_patch.py").read_text(encoding="utf-8")
    for token in (
        "worker_command_waiters",
        'message_type == "command.result"',
        'waiter[0] == worker_id',
        '"/api/admin/linux-workers/{worker_id}/proxy"',
        '"/api/admin/linux-workers/{worker_id}/proxy/test"',
        '"apply_proxy_config"',
        '"test_proxy"',
    ):
        assert token in source


def test_admin_proxy_ui_clears_secret_and_runtime_versions_are_aligned():
    admin = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert "配置 Worker 代理" in admin
    assert "VLESS、VMess、Trojan、Shadowsocks" in admin
    assert re.search(r'input\.value\s*=\s*""', admin)
    assert "/proxy/test" in admin
    assert _runtime_version(runtime) >= (0, 22, 1)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
