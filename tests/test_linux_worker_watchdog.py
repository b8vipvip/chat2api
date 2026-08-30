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
        'grep -F -- "--user-data-dir=${PROFILE_DIR}"',
        "grep -E '[Cc]hrome'",
        'GENERATION_PROBE="${GENERATION_PROBE:-${REPO_DIR}/scripts/linux_worker_generation_probe.sh}"',
        'GENERATION_HEALTH_FILE="${GENERATION_HEALTH_FILE:-${STATE_DIR%/}/generation-backend-health.json}"',
        'CHAT2API_PROXY_PORT="${PROXY_PORT}" "${GENERATION_PROBE}"',
        '"${CHAT2API_SERVER_URL%/}/healthz"',
        'persistent Chrome profile is missing; refusing automatic recovery',
        'Chrome Bridge source is missing; refusing automatic Chrome restart',
        'local services were left running',
    ):
        assert token in source

    generation_probe = source.index("if ! generation_backend_ready; then")
    server_probe = source.index("if ! server_health_ready; then")
    tail = source[generation_probe:]
    assert "restart_unit" not in tail
    assert generation_probe < server_probe


def test_watchdog_persists_generation_health_without_credentials():
    source = read(ROOT / "scripts" / "linux_worker_watchdog.sh")
    assert '"ready":%s' in source
    assert '"checked_at_epoch":%s' in source
    assert '"source":"linux-worker-watchdog-generation-v54"' in source
    assert "worker_token" not in source.lower()
    assert "pairing" not in source.lower()


def test_watchdog_never_recreates_or_reowns_the_persistent_profile():
    source = read(ROOT / "scripts" / "linux_worker_watchdog.sh")
    assert '[[ ! -d "${PROFILE_DIR}" ]]' in source
    assert "stat -c '%U'" in source
    assert 'expected ${WORKER_USER}; refusing automatic recovery' in source
    assert 'mkdir -p "${PROFILE_DIR}"' not in source
    assert "chown" not in source


def test_installer_is_idempotent_when_live_xray_already_uses_captured_config():
    source = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    for token in (
        'captured_config="${WORKER_CONFIG_DIR}/xray-config.json"',
        'source_config_real="$(readlink -f "${source_config}")"',
        'captured_config_real="$(readlink -m "${captured_config}")"',
        'if [[ "${source_config_real}" == "${captured_config_real}" ]]; then',
        'chmod 600 "${captured_config}"',
        'install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 600 "${source_config}" "${captured_config}"',
    ):
        assert token in source

    same_file_branch = source.index('if [[ "${source_config_real}" == "${captured_config_real}" ]]; then')
    guarded_install = source.index(
        'install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 600 "${source_config}" "${captured_config}"'
    )
    assert same_file_branch < guarded_install


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


def test_extension_autoreload_uses_git_tree_version_and_single_attempt_failure_guard():
    source = read(ROOT / "scripts" / "linux_extension_autoreload.sh")

    for token in (
        'git -C "${repo_real}" rev-parse "HEAD:${extension_rel}"',
        'status --porcelain --untracked-files=all',
        'rev-parse --git-path index.lock',
        'data.get("version")',
        'systemctl restart "${CHROME_UNIT}"',
        'grep -F -- "--user-data-dir=${PROFILE_DIR}"',
        "grep -E '[Cc]hrome'",
        'EXTENSION_VERSION=${version}',
        'GIT_COMMIT=${commit}',
        'previously failed to reload; suppressing repeated restart until source changes',
        'Chrome Bridge source has local changes; refusing automatic reload until the worktree is clean',
        'LOCK_FILE="${STATE_DIR}/extension-autoreload.lock"',
        'command -v flock',
        'exec 9>"${LOCK_FILE}"',
        'flock -n 9',
        'another Chrome Bridge reload transaction is already running; skipping this trigger',
    ):
        assert token in source

    assert 'printf \'%s\\n\' "${fingerprint}" >"${FAILED_FILE}"' in source
    assert 'printf \'%s\\n\' "${fingerprint}" >"${APPLIED_FILE}"' in source
    assert "git pull" not in source


def test_installer_adds_extension_autoreload_service_and_timer():
    source = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")

    for token in (
        'AUTORELOAD_SOURCE="${SCRIPT_DIR}/linux_extension_autoreload.sh"',
        'AUTORELOAD_BIN="/usr/local/sbin/chat2api-linux-extension-autoreload"',
        'install -o root -g root -m 755 "${AUTORELOAD_SOURCE}" "${AUTORELOAD_BIN}"',
        "chat2api-extension-autoreload.service",
        "chat2api-extension-autoreload.timer",
        "OnBootSec=2min",
        "OnUnitActiveSec=1min",
        "ExecStart=${AUTORELOAD_BIN}",
        "systemctl start chat2api-extension-autoreload.service",
        "systemctl restart chat2api-extension-autoreload.timer",
    ):
        assert token in source

    unit_start = source.index("cat >/etc/systemd/system/chat2api-extension-autoreload.service <<EOF")
    unit_end = source.index("\nEOF", unit_start)
    unit_block = source[unit_start:unit_end]
    assert "After=chat2api-chrome.service" in unit_block
    assert "Wants=chat2api-chrome.service" in unit_block
    assert "Requires=chat2api-chrome.service" not in unit_block
    assert "hard Requires= dependency would" in unit_block


def test_installer_persists_runtime_paths_without_credentials():
    source = read(ROOT / "scripts" / "install_linux_worker_autostart.sh")
    marker = 'cat >"${WATCHDOG_ENV}" <<EOF'
    marker_start = source.index(marker)
    env_block_start = source.index("\n", marker_start) + 1
    env_block_end = source.index("\nEOF", env_block_start)
    env_block = source[env_block_start:env_block_end]

    for token in (
        "REPO_DIR=${REPO_DIR}",
        "WORKER_USER=${WORKER_USER}",
        "PROFILE_DIR=${PROFILE_DIR}",
        "EXTENSION_DIR=${EXTENSION_DIR}",
        "PROXY_PORT=${PROXY_PORT}",
        "CHATGPT_URL=${CHATGPT_URL}",
        "CHAT2API_SERVER_URL=${CHAT2API_SERVER_URL}",
        "CHROME_UNIT=chat2api-chrome.service",
        "STATE_DIR=${AUTORELOAD_STATE_DIR}",
    ):
        assert token in env_block

    for forbidden in ("PASSWORD", "TOKEN", "PAIRING_CODE", "API_KEY"):
        assert forbidden not in env_block


def test_linux_worker_scripts_have_valid_bash_syntax():
    for script in (
        ROOT / "scripts" / "install_linux_worker_autostart.sh",
        ROOT / "scripts" / "linux_worker_watchdog.sh",
        ROOT / "scripts" / "linux_worker_generation_probe.sh",
        ROOT / "scripts" / "linux_extension_autoreload.sh",
    ):
        subprocess.run(["bash", "-n", str(script)], check=True)
