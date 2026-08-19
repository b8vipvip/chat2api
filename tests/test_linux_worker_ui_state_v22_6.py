from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_linux_worker_detail_state_asset_is_valid_javascript():
    source = ROOT / "app" / "admin_linux_workers_state.js"
    subprocess.run(["node", "--check", str(source)], check=True)


def test_linux_worker_detail_state_survives_table_refresh_contract():
    source = (ROOT / "app" / "admin_linux_workers_state.js").read_text(encoding="utf-8")
    assert "const openDetails = new Set()" in source
    assert "document.addEventListener(\"toggle\"" in source
    assert "new MutationObserver(() => restoreOpenDetails())" in source
    assert "`command:${installId}`" in source
    assert "`progress:${installId}`" in source
    assert "details && !details.open" in source


def test_linux_worker_detail_state_asset_is_injected_into_admin():
    patch = (ROOT / "app" / "linux_worker_ui_state_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'ASSET_PATH = "/assets/chat2api-linux-workers-state.js"' in patch
    assert 'request.url.path != "/admin"' in patch
    assert 'text.replace("</body>", marker + "</body>")' in patch
    assert "install_linux_worker_ui_state_patch" in entry
