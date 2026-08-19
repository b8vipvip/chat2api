from pathlib import Path
import re


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_bootstrap_preserves_worker_bundle_traversal_and_enrollment_json():
    source = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert 'chmod 755 "${WORKER_DIR}.new"' in source
    assert 'ENROLL_RESPONSE="$(mktemp)"' in source
    assert "jq -e '.worker_id and .worker_token and .websocket_url' \"$ENROLL_RESPONSE\"" in source
    assert 'install -o root -g chat2api -m 640 "$ENROLL_RESPONSE" /etc/chat2api-worker/worker.json' in source
    assert "| jq -e '.worker_id and .worker_token and .websocket_url' >/etc/chat2api-worker/worker.json" not in source
    assert 'historical five-byte "true\\n"' in source


def test_bootstrap_prepares_chrome_xdg_dirs_and_quotes_proxy_bypass():
    source = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    launcher = Path("scripts/linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")
    assert 'install -d -o chat2api -g chat2api -m 700 /home/chat2api/.config /home/chat2api/.cache' in source
    assert 'Environment=XDG_CONFIG_HOME=/home/chat2api/.config' in source
    assert 'Environment=XDG_CACHE_HOME=/home/chat2api/.cache' in source
    assert '"--proxy-bypass-list=localhost;127.0.0.1;${server_host}"' in launcher
    assert "\\;127.0.0.1\\;" not in launcher


def test_health_waits_for_services_instead_of_single_instant_check():
    source = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert 'for attempt in $(seq 1 180); do' in source
    assert '等待 Worker 核心服务启动' in source
    assert 'systemctl reset-failed chat2api-chrome.service chat2api-worker-agent.service' in source
    assert '核心服务未正常运行' in source


def test_runtime_marks_bootstrap_recovery_release():
    runtime = Path("app/runtime_contract.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 7)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
