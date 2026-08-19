from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_proxy_helper_workspace_is_writable_inside_agent_systemd_sandbox():
    bootstrap = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "linux_worker_proxy_apply.sh").read_text(encoding="utf-8")

    agent_unit = bootstrap.split("cat >/etc/systemd/system/chat2api-worker-agent.service", 1)[1].split("\nUNIT\n", 1)[0]
    assert "ProtectSystem=strict" in agent_unit
    assert "ReadWritePaths=/etc/chat2api-worker" in agent_unit

    assert 'WORKSPACE_PARENT="/etc/chat2api-worker"' in helper
    assert 'mktemp -d "${WORKSPACE_PARENT}/.proxy-apply.XXXXXX"' in helper
    assert "/tmp/chat2api-proxy-apply" not in helper
    assert '[[ -n "$work_dir" ]] && rm -rf "$work_dir"' in helper


def test_proxy_helper_keeps_candidate_contents_private_and_ephemeral():
    helper = (ROOT / "scripts" / "linux_worker_proxy_apply.sh").read_text(encoding="utf-8")

    assert "umask 077" in helper
    assert 'candidate="${work_dir}/xray.candidate.json"' in helper
    assert 'backup="${work_dir}/xray.previous.json"' in helper
    assert 'cat >"${candidate}"' in helper
    assert 'cat "${candidate}"' not in helper
