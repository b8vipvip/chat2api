import json
import tomllib
from pathlib import Path

from fastapi import FastAPI

import app as package
from app.live_voice_patch import LIVE_PROTOCOL_VERSION
from app.runtime_contract import (
    CHROME_BRIDGE_VERSION,
    PACKAGE_VERSION,
    PRODUCTION_ENTRYPOINT,
    SERVER_RUNTIME_VERSION,
    install_runtime_contract,
    version_contract_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def test_python_package_versions_are_aligned():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert package.__version__ == PACKAGE_VERSION == pyproject["project"]["version"]


def test_chrome_bridge_contract_matches_manifest():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == CHROME_BRIDGE_VERSION


def test_realtime_protocol_contract_matches_live_bridge():
    assert LIVE_PROTOCOL_VERSION == "chat2api-live-v1"


def test_runtime_contract_payload_names_each_version_surface():
    runtime = FastAPI(version=SERVER_RUNTIME_VERSION)
    payload = version_contract_payload(runtime)
    assert payload["object"] == "chat2api.version"
    assert payload["server"]["package_version"] == PACKAGE_VERSION
    assert payload["server"]["runtime_version"] == SERVER_RUNTIME_VERSION
    assert payload["server"]["runtime_aligned"] is True
    assert payload["server"]["entrypoint"] == PRODUCTION_ENTRYPOINT
    assert payload["chrome_bridge"]["version"] == CHROME_BRIDGE_VERSION
    assert payload["protocols"]["realtime_voice"] == LIVE_PROTOCOL_VERSION


def test_runtime_contract_installs_once_and_after_latest_patch():
    runtime = FastAPI(version=SERVER_RUNTIME_VERSION)
    install_runtime_contract(runtime)
    install_runtime_contract(runtime)
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == "/version") == 1

    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_runtime_contract(app)" in entry
    assert entry.index("install_v21_4_model_contract_patch(app)") < entry.index("install_v21_5_patch(app)")
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_runtime_contract(app)")


def test_readme_describes_version_contract_and_voice_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Python package：`{PACKAGE_VERSION}`" in readme
    assert "Server runtime / console：`" in readme
    assert f"Chrome Bridge：`{CHROME_BRIDGE_VERSION}`" in readme
    assert "GET /version" in readme
    assert "完整版本规则见 `docs/VERSIONING.md`" in readme
    assert "语音生成、语音对话尚未实现" not in readme
