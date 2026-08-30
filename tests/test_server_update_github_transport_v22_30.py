from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_host_updater_prefers_bounded_ssh_and_keeps_https_fallback():
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")

    for token in (
        'GITHUB_SSH_URL="git@github.com:b8vipvip/chat2api.git"',
        'HostName=ssh.github.com',
        'Port=443',
        'HostKeyAlias=ssh.github.com',
        'HostKeyAlias=github.com',
        'BatchMode=yes',
        'ConnectTimeout=${GITHUB_SSH_CONNECT_SECONDS}',
        'StrictHostKeyChecking=yes',
        'HostKeyAlgorithms=ssh-ed25519',
        'GITHUB_HTTPS_URL="https://github.com/b8vipvip/chat2api.git"',
        '-c http.version=HTTP/1.1',
        '-c http.lowSpeedLimit=1024',
        'http.lowSpeedTime=${GITHUB_HTTPS_LOW_SPEED_SECONDS}',
        'timeout --signal=TERM --kill-after=5s "${GITHUB_SSH_FETCH_SECONDS}s"',
        'timeout --signal=TERM --kill-after=5s "${GITHUB_HTTPS_FETCH_SECONDS}s"',
        'GitHub fetch transport cycle ${cycle}/2: SSH-443 -> SSH-22 -> HTTPS',
        'run_fetch_transport "ssh-443"',
        'run_fetch_transport "ssh-22"',
        'run_fetch_transport "https"',
        'fetch --prune "$GITHUB_SSH_URL" "$refspec"',
        'fetch --prune "$GITHUB_HTTPS_URL" "$refspec"',
        'FETCH_TRANSPORT="$transport"',
        'FETCH_ELAPSED_MS="$elapsed_ms"',
        'FETCH_ATTEMPTS="$((FETCH_ATTEMPTS + 1))"',
        '通道=${FETCH_TRANSPORT}',
        '耗时=${FETCH_ELAPSED_MS}ms',
    ):
        assert token in updater

    assert updater.index('run_fetch_transport "ssh-443"') < updater.index('run_fetch_transport "ssh-22"')
    assert updater.index('run_fetch_transport "ssh-22"') < updater.index('run_fetch_transport "https"')


def test_github_host_key_is_pinned_and_not_accept_new():
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")
    assert "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU" not in updater
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in updater
    assert "ssh.github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in updater
    assert "StrictHostKeyChecking=accept-new" not in updater
    assert "StrictHostKeyChecking=no" not in updater


def test_updater_accepts_supported_origin_forms_and_installer_matches():
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_chat2api_server_updater.sh").read_text(encoding="utf-8")
    ssh443 = "ssh://git@ssh.github.com:443/b8vipvip/chat2api.git"
    for source in (updater, installer):
        assert "https://github.com/b8vipvip/chat2api.git" in source
        assert "git@github.com:b8vipvip/chat2api.git" in source
        assert ssh443 in source


def test_transport_release_contract_and_shell_syntax():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.37"' in runtime
    assert '"github_transport_failover": True' in runtime
    assert "github-transport-failover-v22-30" in runtime

    for filename in (
        "scripts/chat2api_server_update.sh",
        "scripts/install_chat2api_server_updater.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr