from pathlib import Path
import subprocess

from app.linux_worker_diagnostics_patch import _patch_bootstrap as patch_diagnostics_bootstrap
from app.linux_worker_initialize_patch import _patch_bootstrap as patch_initialize_bootstrap
from app.linux_worker_install_ux_patch import _patch_bootstrap as patch_install_ux_bootstrap
from app.linux_worker_upgrade_patch import _patch_bootstrap as patch_upgrade_bootstrap


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_installs_v44_agent_and_fixed_online_upgrade_helper():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    patched = patch_upgrade_bootstrap(
        patch_initialize_bootstrap(
            patch_diagnostics_bootstrap(
                patch_install_ux_bootstrap(source)
            )
        )
    )

    assert "${WORKER_DIR}/scripts/linux_worker_agent_v44.py" in patched
    assert 'install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_upgrade.sh" /usr/local/sbin/chat2api-worker-upgrade' in patched
    assert "/usr/local/sbin/chat2api-worker-upgrade" in patched
    assert 'echo "Worker Agent: 0.3.6 (支持后台一键更新 / 实时进度)"' in patched


def test_online_upgrade_helper_and_agent_shim_have_valid_syntax():
    shell = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "linux_worker_upgrade.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr

    python = subprocess.run(
        ["python", "-m", "py_compile", str(ROOT / "scripts" / "linux_worker_agent_v44.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert python.returncode == 0, python.stderr


def test_worker_upgrade_ui_has_update_button_live_polling_and_one_time_compatibility_path():
    source = (ROOT / "app" / "admin_linux_worker_upgrade_v44.js").read_text(encoding="utf-8")
    for token in (
        'button.textContent = "更新"',
        "/upgrade-status",
        "setTimeout(tick, 1000)",
        'button.textContent = `更新 ${',
        "needs_bootstrap_once",
        "复制启用命令",
        "实时进度记录",
    ):
        assert token in source

    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_worker_upgrade_v44.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_online_upgrade_control_plane_is_worker_authenticated_and_progress_survives_agent_restart():
    source = (ROOT / "app" / "linux_worker_upgrade_patch.py").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "linux_worker_upgrade.sh").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")

    for token in (
        '@app.post("/api/workers/{worker_id}/upgrade-progress")',
        'store.authenticate(worker_id, token)',
        '@app.post("/api/admin/linux-workers/{worker_id}/upgrade")',
        '"upgrade_worker"',
        'needs_bootstrap_once',
        'TARGET_AGENT_VERSION = "0.3.6"',
    ):
        assert token in source
    assert 'systemd-run --quiet --collect --no-block' in helper
    assert 'X-Worker-Token' in helper
    assert 'bash "$bootstrap" --server "$SERVER_URL" --upgrade' in helper
    assert "install_linux_worker_upgrade_patch(app)" in entry
    assert entry.index("install_linux_worker_initialize_patch(app)") < entry.index("install_linux_worker_upgrade_patch(app)")
    assert 'SERVER_RUNTIME_VERSION = "0.22.33"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.6"' in runtime
    assert '"linux_worker_online_upgrade": True' in runtime
    assert '"linux_worker_upgrade_live_progress": True' in runtime
    assert '"linux_worker_sudoers_guard": True' in runtime
