from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_image_packages_public_linux_worker_bootstrap():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    control_plane = (ROOT / "app" / "linux_worker_patch.py").read_text(encoding="utf-8")
    bootstrap = ROOT / "scripts" / "bootstrap_linux_worker.sh"

    assert bootstrap.is_file()
    assert "set -euo pipefail" in bootstrap.read_text(encoding="utf-8")
    assert "COPY scripts/bootstrap_linux_worker.sh ./scripts/bootstrap_linux_worker.sh" in dockerfile
    assert "chmod 755 /app/scripts/bootstrap_linux_worker.sh" in dockerfile
    assert '@app.get("/bootstrap/linux-worker.sh", include_in_schema=False)' in control_plane
    assert 'joinpath("scripts/bootstrap_linux_worker.sh").read_text()' in control_plane


def test_bootstrap_packaging_fix_bumps_server_only():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    manifest = (ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8")

    assert 'SERVER_RUNTIME_VERSION = "0.22.4"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert '"version": "0.8.1"' in manifest
