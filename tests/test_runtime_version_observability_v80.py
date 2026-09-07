import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, CHROME_BRIDGE_VERSION, SERVER_RUNTIME_VERSION


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_historical_v213_admin_patch_no_longer_overwrites_runtime_identity() -> None:
    source = read("app/v21_3_patch.py")
    assert 'payload["version"] = PATCH_VERSION' not in source
    assert 'payload["server_version"] = PATCH_VERSION' not in source
    assert "Runtime identity is" in source
    assert "owned by runtime_contract" in source


def test_runtime_contract_owns_current_identity() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = json.loads(read("chrome_extension/manifest.json"))
    assert f'SERVER_RUNTIME_VERSION = "{SERVER_RUNTIME_VERSION}"' in runtime
    assert f'CHROME_BRIDGE_VERSION = "{CHROME_BRIDGE_VERSION}"' in runtime
    assert f'CHROME_BRIDGE_BUNDLE_VERSION = "{CHROME_BRIDGE_BUNDLE_VERSION}"' in runtime
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION
    assert '"runtime_version_observability_v80": True' in runtime
