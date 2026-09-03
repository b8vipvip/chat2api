from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_historical_v213_admin_patch_no_longer_overwrites_runtime_identity() -> None:
    source = read("app/v21_3_patch.py")
    assert 'payload["version"] = PATCH_VERSION' not in source
    assert 'payload["server_version"] = PATCH_VERSION' not in source
    assert "Runtime identity is" in source
    assert "owned by runtime_contract" in source


def test_v02248_runtime_contract_owns_current_identity_without_bundle_bump() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = read("chrome_extension/manifest.json")
    assert 'SERVER_RUNTIME_VERSION = "0.22.49"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.20"' in runtime
    assert '"version": "0.8.20"' in manifest
    assert '"runtime_version_observability_v80": True' in runtime
