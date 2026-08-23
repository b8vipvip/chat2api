import json
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import app as package
from app.live_voice_patch import LIVE_PROTOCOL_VERSION
from app.runtime_contract import (
    ADMIN_EXTENSION_COLUMNS_ASSET,
    ADMIN_VERSION_ASSET,
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
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == ADMIN_VERSION_ASSET) == 1
    assert sum(1 for route in runtime.routes if getattr(route, "path", "") == ADMIN_EXTENSION_COLUMNS_ASSET) == 1

    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_runtime_contract(app)" in entry
    assert entry.index("install_v21_4_model_contract_patch(app)") < entry.index("install_v21_5_patch(app)")
    assert entry.index("install_v21_5_patch(app)") < entry.index("install_runtime_contract(app)")


def test_runtime_contract_is_final_admin_version_owner():
    runtime = FastAPI(version="0.21.4")

    @runtime.get("/admin")
    async def admin_page():
        return HTMLResponse('<html><body><div class="brand"><small>Server Console · v0.21.4</small></div><div id="status">v0.20.0</div><script src="/assets/historical.js"></script></body></html>')

    @runtime.get("/developers")
    async def developers_page():
        return HTMLResponse('<html><body><div class="brand"><small>Server Console · v0.21.4</small></div></body></html>')

    @runtime.get("/api/admin/overview")
    async def overview():
        return {"version": "0.21.4", "online_extensions": 2}

    @runtime.get("/api/admin/extensions")
    async def extensions():
        return {"clients": [{"client_id": "ext_a", "version": CHROME_BRIDGE_VERSION}]}

    install_runtime_contract(runtime)
    assert runtime.version == SERVER_RUNTIME_VERSION

    client = TestClient(runtime)
    html = client.get("/admin").text
    assert f'<script src="{ADMIN_VERSION_ASSET}"></script>' in html
    assert f'<script src="{ADMIN_EXTENSION_COLUMNS_ASSET}"></script>' in html
    assert html.index('/assets/historical.js') < html.index(ADMIN_VERSION_ASSET) < html.index(ADMIN_EXTENSION_COLUMNS_ASSET)

    developers_html = client.get("/developers").text
    assert ADMIN_VERSION_ASSET in developers_html
    assert ADMIN_EXTENSION_COLUMNS_ASSET not in developers_html

    script = client.get(ADMIN_VERSION_ASSET).text
    assert f'const VERSION = "{SERVER_RUNTIME_VERSION}"' in script
    assert "__chat2apiRuntimeVersionOwner" in script
    assert 'document.querySelector(".brand small")' in script
    assert 'document.getElementById("status")' in script
    assert "MutationObserver" in script

    columns_script = client.get(ADMIN_EXTENSION_COLUMNS_ASSET).text
    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v2"' in columns_script
    assert 'const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v1"' in columns_script
    assert 'label: "API 调用 / 并发上限"' in columns_script
    assert 'id = "extensionColumnSettingsButton"' in columns_script

    overview_payload = client.get("/api/admin/overview").json()
    assert overview_payload["version"] == SERVER_RUNTIME_VERSION

    extension_payload = client.get("/api/admin/extensions").json()
    assert extension_payload["clients"][0]["version"] == CHROME_BRIDGE_VERSION
    assert "version" not in extension_payload


def test_readme_describes_version_contract_and_voice_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Python package：`{PACKAGE_VERSION}`" in readme
    assert "Server runtime / console：`" in readme
    assert "Chrome Bridge：`" in readme
    assert "GET /version" in readme
    assert "完整版本规则见 `docs/VERSIONING.md`" in readme
    assert "语音生成、语音对话尚未实现" not in readme
