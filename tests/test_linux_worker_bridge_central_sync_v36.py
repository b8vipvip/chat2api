from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_VERSION, CHROME_BRIDGE_BUNDLE_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_bridge_protocol_stays_stable_while_bundle_busts_mv3_cache() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    dispatcher = (ROOT / "chrome_extension" / "background_capacity_control_v36.js").read_text(encoding="utf-8")

    assert manifest["version"] == "0.8.23"
    assert CHROME_BRIDGE_VERSION == "0.8.1"
    assert CHROME_BRIDGE_BUNDLE_VERSION == manifest["version"]
    assert '"background_capacity_control_v36.js"' in entry
    assert entry.index('"background_capacity_control_v35.js"') < entry.index('"background_capacity_control_v36.js"')
    assert "const CONTROL_VERSION = 36" in dispatcher
    assert "extension_control_version: CONTROL_VERSION" in dispatcher
    assert "extension_control_ready" in dispatcher
    assert "authoritative-global-dispatch-v36" in dispatcher


def test_linux_worker_autoreload_pulls_verified_central_bundle() -> None:
    source = (ROOT / "scripts" / "linux_extension_autoreload.sh").read_text(encoding="utf-8")

    assert 'SERVER_URL="${CHAT2API_SERVER_URL:-}"' in source
    assert 'REPO_DIR}" == "/opt/chat2api-worker"' in source
    assert "/bootstrap/linux-worker-bundle.json" in source
    assert "/bootstrap/linux-worker-bundle.tar.gz" in source
    assert "sha256sum -c -" in source
    assert "extension-central-bundle.sha256" in source
    assert "CENTRAL_EXTENSION_CHANGED=1" in source
    assert 'systemctl restart "${CHROME_UNIT}"' in source
    assert "central Worker Bundle" in source


def test_worker_diagnostics_exposes_loaded_capacity_protocol_and_bundle_fingerprint() -> None:
    source = (ROOT / "scripts" / "linux_worker_diagnostics.sh").read_text(encoding="utf-8")
    assert "capacity_control_v35_source" in source
    assert "capacity_control_v36_source" in source
    assert "capacity_control_v36_loaded_by_entry" in source
    assert "extension-state.env" in source
    assert "extension-central-bundle.sha256" in source


def test_updated_worker_shell_scripts_parse() -> None:
    for filename in ("linux_extension_autoreload.sh", "linux_worker_diagnostics.sh"):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / filename)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
