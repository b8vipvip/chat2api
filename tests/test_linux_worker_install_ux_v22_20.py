from pathlib import Path
import subprocess

from app.linux_worker_install_ux_patch import _patch_bootstrap, _patch_stable_table_js


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_waits_long_enough_for_first_chrome_for_testing_download():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    patched = _patch_bootstrap(source)

    assert 'BROWSER_READY_TIMEOUT="${CHAT2API_BROWSER_READY_TIMEOUT:-900}"' in patched
    assert 'for attempt in $(seq 1 "$BROWSER_READY_TIMEOUT"); do' in patched
    assert 'attempt % 30 == 0' in patched
    assert '（${attempt}/${BROWSER_READY_TIMEOUT}）' in patched
    assert 'Chrome for Testing：${CFT_BINARY_STATE}' in patched
    assert '浏览器进程：${CHROME_PROCESS_STATE}' in patched
    assert 'for attempt in $(seq 1 180); do' not in patched


def test_progress_details_gain_one_click_copy_without_changing_base_asset_file(tmp_path):
    source = (ROOT / "app" / "admin_linux_worker_stable_table.js").read_text(encoding="utf-8")
    patched = _patch_stable_table_js(source)

    for token in (
        'data-install-progress-detail-v2220="1"',
        'data-copy-install-progress-v2220="1"',
        '>复制详情</button>',
        'const copyProgress = event.target.closest?.("[data-copy-install-progress-v2220]")',
        'navigator.clipboard?.writeText',
        '安装进度：${summary}',
        'copyProgress.textContent = "已复制"',
    ):
        assert token in patched

    rendered = tmp_path / "stable-table-v22-20.js"
    rendered.write_text(patched, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(rendered)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_v22_20_ux_patch_remains_before_newer_worker_patches():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    assert 'SERVER_RUNTIME_VERSION = "0.22.38"' in runtime
    assert 'from .linux_worker_install_ux_patch import install_linux_worker_install_ux_patch' in entry
    assert 'install_linux_worker_install_ux_patch(app)' in entry
    assert entry.index('install_linux_worker_table_stability_patch(app)') < entry.index('install_linux_worker_install_ux_patch(app)')
    assert entry.index('install_linux_worker_install_ux_patch(app)') < entry.index('install_linux_worker_repair_command_patch(app)')
    assert entry.index('install_linux_worker_repair_command_patch(app)') < entry.index('install_linux_worker_diagnostics_patch(app)')
    assert entry.index('install_linux_worker_diagnostics_patch(app)') < entry.index('install_linux_worker_initialize_patch(app)')