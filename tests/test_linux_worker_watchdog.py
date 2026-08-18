import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_watchdog_repairs_only_definitive_local_worker_failures():
    source = read(ROOT / "scripts" / "linux_worker_watchdog.sh")

    for token in (
        'systemctl is-active --quiet "$1"',
        'systemctl restart "${unit}"',
        '/dev/tcp/127.0.0.1/"${PROXY_PORT}"',
        'pgrep -u "${WORKER_USER}" -f "google-chrome.*user-data-dir=${PROFILE_DIR}"',
        '--proxy "socks5h://127.0.0.1:${PROXY_PORT}"',
        '"${CHAT2API_SERVER_URL%/}/healthz"',
        'persistent Chrome profile is missing; refusing automatic recovery',
        'Chrome Bridge source is missing; refusing automatic Chrome restart',
        'local services were left running',
    ):
        assert token in source

    # Upstream ChatGPT/server failures must be observable without creating a
    # two-minute Xray/Chrome restart storm.
    chatgpt_probe = source.index("if ! chatgpt_transport_ready; then")
    server_probe = source.index("if ! server_health_ready; then")
    tail = source[chatgpt_probe:]
    assert "restart_unit" not in tail
    assert chatgpt_probe < server_probe


def test_watchdog_never_recreates_or_reowns_the_persistent_profile():
    source = read(ROOT / "scripts" / "linux_worker_watchdog.sh")
    assert '[[ ! -d "${PROFILE_DIR}" ]]' in source
    assert "stat -c '%U'" in source
    assert 'expected ${WORKER_USER}; refusing automatic recovery' in source
    assert 'mkdir -p "${PROFILE_DIR}"' not in source
    assert "chown" not in source


def test_installer_adds_systemd_watchdog_service_and_timer():
    source = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")

    for token in (
        'WATCHDOG_SOURCE="${SCRIPT_DIR}/linux_worker_watchdog.sh"',
        'WATCHDOG_BIN="/usr/local/sbin/chat2api-linux-worker-watchdog"',
        'WATCHDOG_ENV="/etc/default/chat2api-worker-watchdog"',
        'install -o root -g root -m 755 "${WATCHDOG_SOURCE}" "${WATCHDOG_BIN}"',
        "chat2api-worker-watchdog.service",
        "chat2api-worker-watchdog.timer",
        "OnBootSec=90s",
        "OnUnitActiveSec=2min",
        "Persistent=true",
        "EnvironmentFile=-${WATCHDOG_ENV}",
        "ExecStart=${WATCHDOG_BIN}",
        "systemctl restart chat2api-worker-watchdog.timer",
    ):
        assert token in source

    assert "cron" not in source.lower()
    assert "crontab" not in source.lower()


def test_installer_persists_watchdog_runtime_paths_without_credentials():
    source = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    env_block_start = source.index('cat >"${WATCHDOG_ENV}" <<EOF')
    env_block_end = source.index("EOF", env_block_start + 1)
    env_block = source[env_block_start:env_block_end]

    for token in (
        "WORKER_USER=${WORKER_USER}",
        "PROFILE_DIR=${PROFILE_DIR}",
        "EXTENSION_DIR=${EXTENSION_DIR}",
        "PROXY_PORT=${PROXY_PORT}",
        "CHATGPT_URL=${CHATGPT_URL}",
        "CHAT2API_SERVER_URL=${CHAT2API_SERVER_URL}",
    ):
        assert token in env_block

    for forbidden in ("PASSWORD", "TOKEN", "PAIRING_CODE", "API_KEY"):
        assert forbidden not in env_block


def test_linux_worker_scripts_have_valid_bash_syntax():
    for script in (
        ROOT / "scripts" / "install_linux_worker_autostart.sh",
        ROOT / "scripts" / "linux_worker_watchdog.sh",
    ):
        subprocess.run(["bash", "-n", str(script)], check=True)
