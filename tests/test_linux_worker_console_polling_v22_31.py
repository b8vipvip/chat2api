from pathlib import Path
import subprocess

from app.linux_worker_console_polling_patch import _patch_base_asset, _patch_stable_asset


ROOT = Path(__file__).resolve().parents[1]


def test_base_worker_console_serializes_refresh_and_skips_unchanged_dom(tmp_path):
    source = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    patched = _patch_base_asset(source)

    assert patched != source
    for token in (
        "let loadInFlight = null",
        "let lastTableHtml = \"\"",
        "if (loadInFlight) return loadInFlight",
        "if (html === lastTableHtml) return",
        "__CHAT2API_LINUX_WORKER_ROWS__",
        "__CHAT2API_LINUX_WORKER_REFRESH__",
        "chat2api:linux-worker-rows",
        "linuxWorkerLiveSummary",
        "在线：${online}",
        "}, 2500);",
    ):
        assert token in patched
    assert '}, 1000);' not in patched

    target = tmp_path / "admin_linux_workers_patched.js"
    target.write_text(patched, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_stable_renderer_reuses_shared_snapshot_instead_of_second_poll_loop(tmp_path):
    source = (ROOT / "app" / "admin_linux_worker_stable_table.js").read_text(encoding="utf-8")
    patched = _patch_stable_asset(source)

    assert patched != source
    for token in (
        "const sharedRefresh = globalThis.__CHAT2API_LINUX_WORKER_REFRESH__",
        "const sharedRows = globalThis.__CHAT2API_LINUX_WORKER_ROWS__",
        'globalThis.addEventListener("chat2api:linux-worker-rows"',
        'setInterval(() => { if (section.classList.contains("active") && !pairingDialog.open) paint(); }, 5000);',
        'if (section.classList.contains("active")) refreshRows();',
    ):
        assert token in patched
    assert 'setInterval(() => { if (section.classList.contains("active") && !pairingDialog.open) refreshRows(); }, 1500);' not in patched

    target = tmp_path / "admin_linux_worker_stable_table_patched.js"
    target.write_text(patched, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_entry_installs_console_polling_patch_after_worker_feature_stack():
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .linux_worker_console_polling_patch import install_linux_worker_console_polling_patch" in source
    assert "install_linux_worker_console_polling_patch(app)" in source
    assert source.index("install_linux_worker_enable_patch(app)") < source.rindex("install_linux_worker_console_polling_patch(app)")
