from pathlib import Path
import subprocess

from app.linux_worker_installs import LinuxWorkerInstallStore
from app.linux_worker_repair_command_patch import _patch_admin_linux_workers_js


ROOT = Path(__file__).resolve().parents[1]


def test_failed_linked_worker_keeps_terminal_recovery_path(tmp_path):
    store = LinuxWorkerInstallStore(tmp_path)
    item = store.create("Repair Worker")
    code = item["code"]
    store.link_worker(code, "wrk_repair_001")

    failed = store.record_progress(code, stage="health", state="failed", message="browser timeout")
    assert failed["state"] == "failed"
    assert failed["enabled"] is False
    assert failed["worker_id"] == "wrk_repair_001"

    completed = store.record_progress(code, stage="complete", state="installed", message="repair complete")
    assert completed["state"] == "installed"
    assert completed["completed_at"]


def test_admin_asset_keeps_failed_linked_install_command_copyable_with_feedback(tmp_path):
    source = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    patched = _patch_admin_linux_workers_js(source)

    for token in (
        'const repairable = inactive && failed && Boolean(String(row.worker_id || "").trim())',
        'repairable ? "修复安装"',
        'repairable ? "复制修复命令"',
        '当前已注册 Worker 的原服务器幂等修复',
        'window.isSecureContext',
        'document.execCommand("copy")',
        'target.textContent = "已复制"',
        '复制失败，请展开安装命令后手动选择复制。',
    ):
        assert token in patched

    assert 'data-copy-install="${esc(row.install_id)}" ${disabled ? "disabled" : ""}' not in patched
    assert 'if(row?.install_command) await navigator.clipboard.writeText(row.install_command)' not in patched

    target = tmp_path / "admin_linux_workers.patched.js"
    target.write_text(patched, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_runtime_and_entry_keep_repair_patch_before_diagnostics_patch():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.22"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert "from .linux_worker_repair_command_patch import install_linux_worker_repair_command_patch" in entry
    assert "install_linux_worker_repair_command_patch(app)" in entry
    assert "install_linux_worker_diagnostics_patch(app)" in entry
    assert entry.index("install_linux_worker_install_ux_patch(app)") < entry.index("install_linux_worker_repair_command_patch(app)")
    assert entry.index("install_linux_worker_repair_command_patch(app)") < entry.index("install_linux_worker_diagnostics_patch(app)")
