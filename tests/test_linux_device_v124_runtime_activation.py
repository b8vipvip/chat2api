import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_v124_is_installed_in_the_real_production_entrypoint_without_polluting_pytest_process():
    probe = r'''
import json
from app.entry import app
paths = {getattr(route, "path", "") for route in app.routes}
print(json.dumps({
    "installed": bool(getattr(app.state, "linux_worker_device_authority_v124_installed", False)),
    "paths": sorted(paths),
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["installed"] is True
    paths = set(payload["paths"])
    assert "/api/admin/linux-devices" in paths
    assert "/api/admin/linux-devices/setup-options" in paths
    assert "/api/admin/linux-legacy-records" in paths
    assert "/assets/chat2api-linux-device-authority-v124.js" in paths


def test_v124_readiness_uses_real_production_state_not_nonexistent_worker_enrollment():
    source = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    assert '"worker_enrollment",' not in source
    assert '"linux_worker_installs"' in source
    assert '"linux_worker_proxy_catalog"' in source
    assert "install_linux_worker_device_authority_v124_patch(app)" in source


def test_v124_backend_matches_current_linux_worker_store_shapes():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "linux_worker_installs.list_admin()" in source
    assert 'store.data.get("workers", {})' in source
    assert 'store.data.get("installs", {})' in source
    assert "worker_enrollment" not in source
    assert "._workers" not in source
    assert "._find(" not in source
    assert ".mark_command(" not in source
    assert ".update_metadata(" not in source
    assert "'/bootstrap/linux-worker.sh'" in source


def test_v124_first_install_reuses_one_code_for_install_progress_and_worker_enrollment():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "install = installs.create(worker_name)" in source
    assert 'code = str(install.get("code") or "")' in source
    assert 'workers.data.setdefault("enrollments", {})[digest]' in source
    assert '"install_id": str(install.get("install_id") or "")' in source
    assert "installs.link_worker(code, worker_id)" in source


def test_v124_selected_pairing_is_persisted_for_existing_pairing_reconciler():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert '"worker_pairing": {' in source
    assert '"pairing_id": pairing_id' in source
    assert 'live["worker_pairing_state"] = "pending"' in source
    assert "--pairing-code" in source


def test_v124_admin_html_owner_removes_legacy_linux_assets_before_injecting_itself():
    source = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    assert "LEGACY_LINUX_ASSET_RE.sub" in source
    assert 'ASSET_PATH = "/assets/chat2api-linux-device-authority-v124.js"' in source
    assert "no-store, no-cache, must-revalidate" in source
