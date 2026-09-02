from __future__ import annotations

import subprocess
from pathlib import Path

from app.linux_worker_diagnostics_patch import _patch_bootstrap as patch_diagnostics_bootstrap
from app.linux_worker_initialize_patch import _patch_bootstrap as patch_initialize_bootstrap
from app.linux_worker_upgrade_patch import _patch_bootstrap as patch_upgrade_bootstrap


ROOT = Path(__file__).resolve().parents[1]


def test_initialize_helper_restarts_full_worker_and_keeps_one_chatgpt_page() -> None:
    source = (ROOT / "scripts" / "linux_worker_initialize.sh").read_text(encoding="utf-8")
    for token in ("chat2api-worker-agent.service", "chat2api-chrome.service", "chat2api-xray.service", "chat2api-xvfb.service", 'rm -rf "${PROFILE_DIR}/Default/Service Worker"', "/json/new?", "--keep 1", "service_worker", "systemd-run --quiet --collect --no-block"):
        assert token in source
    result = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "linux_worker_initialize.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_agent_v43_exposes_only_bounded_initialize_command() -> None:
    source = (ROOT / "scripts" / "linux_worker_agent_v43.py").read_text(encoding="utf-8")
    assert 'AGENT_VERSION = "0.3.5"' in source
    assert '{"initialize_worker"}' in source
    assert 'INITIALIZE_HELPER' in source
    assert '["sudo", "-n", str(INITIALIZE_HELPER), "--schedule"]' in source
    assert "shell=True" not in source
    result = subprocess.run(["python", "-m", "py_compile", str(ROOT / "scripts" / "linux_worker_agent_v43.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_autoreload_v43_does_not_accept_running_chrome_without_bridge_runtime() -> None:
    source = (ROOT / "scripts" / "linux_extension_autoreload_v43.sh").read_text(encoding="utf-8")
    for token in ("service_worker", "chrome-extension://", 'rm -rf "${PROFILE_DIR}/Default/Service Worker"', 'rm -f "$APPLIED_FILE"', "Bridge Service Worker is unavailable", "repair_base_script()", "/bootstrap/linux-worker-bundle.tar.gz"):
        assert token in source
    result = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "linux_extension_autoreload_v43.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_final_bootstrap_installs_v43_agent_initializer_and_runtime_validator() -> None:
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    patched = patch_initialize_bootstrap(patch_diagnostics_bootstrap(source))
    assert "scripts/linux_worker_agent_v43.py" in patched
    assert 'scripts/linux_extension_autoreload_v43.sh" /usr/local/sbin/chat2api-linux-extension-autoreload' in patched
    assert 'scripts/linux_worker_initialize.sh" /usr/local/sbin/chat2api-worker-initialize' in patched
    assert "/usr/local/sbin/chat2api-worker-diagnostics" in patched
    assert "/usr/local/sbin/chat2api-worker-initialize" in patched
    assert 'rm -rf "$PROFILE_DIR/Default/Service Worker"' in patched


def test_initialize_patch_never_splits_sudoers_restart_allowlist() -> None:
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    patched = patch_upgrade_bootstrap(patch_initialize_bootstrap(patch_diagnostics_bootstrap(source)))
    sudo_header = 'cat >"$SUDOERS_TMP" <<\'SUDO\''
    assert sudo_header in patched
    sudo_block = patched.split(sudo_header + "\n", 1)[1].split("\nSUDO\n", 1)[0]
    sudo_lines = [line for line in sudo_block.splitlines() if line.strip()]
    assert len(sudo_lines) == 1
    rule = sudo_lines[0]
    assert rule.startswith("chat2api ALL=(root) NOPASSWD:")
    assert "/bin/systemctl restart chat2api-chrome.service" in rule
    assert "/usr/local/sbin/chat2api-worker-diagnostics" in rule
    assert "/usr/local/sbin/chat2api-worker-initialize" in rule
    assert "/usr/local/sbin/chat2api-worker-upgrade" in rule
    assert "PROFILE_DIR" not in rule
    lines = patched.splitlines()
    restart_index = lines.index("systemctl restart chat2api-chrome.service")
    assert lines[restart_index - 1] == 'rm -rf "$PROFILE_DIR/Default/Service Worker" 2>/dev/null || true'
    assert 'visudo -cf "$SUDOERS_TMP"' in patched
    assert 'install -o root -g root -m 440 "$SUDOERS_TMP" /etc/sudoers.d/chat2api-worker' in patched


def test_docker_bundle_ships_full_initialize_payload() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for token in ("scripts/linux_worker_agent_v43.py", "scripts/linux_extension_autoreload_v43.sh", "scripts/linux_worker_initialize.sh"):
        assert token in dockerfile


def test_admin_initialize_patch_has_full_and_compatibility_paths() -> None:
    source = (ROOT / "app" / "linux_worker_initialize_patch.py").read_text(encoding="utf-8")
    assert '@app.post("/api/admin/linux-workers/{worker_id}/initialize")' in source
    assert '"initialize_worker"' in source
    assert '("restart_xray", "restart_xvfb", "restart_chrome")' in source
    assert '"needs_worker_upgrade": True' in source
    assert "worker_control.ALLOWED_COMMANDS" in source
    ui = (ROOT / "app" / "admin_linux_worker_initialize_v43.js").read_text(encoding="utf-8")
    assert 'button.textContent = "初始化"' in ui
    assert "/initialize`" in ui
    assert "data-worker-diagnostics-v2222" in ui
    result = subprocess.run(["node", "--check", str(ROOT / "app" / "admin_linux_worker_initialize_v43.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_runtime_contract_publishes_worker_initialize_v43() -> None:
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.45"' in runtime
    assert "worker-initialize-v43" in runtime
    assert "worker-sudoers-guard-v22-33" in runtime
    assert '"linux_worker_initialize": True' in runtime
    assert '"linux_worker_bridge_runtime_recovery": True' in runtime
    assert '"linux_worker_sudoers_guard": True' in runtime
    assert '"linux_worker_autoreload_self_heal": True' in runtime
    assert '"linux_worker_disable_authority": True' in runtime
