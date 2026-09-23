import json
from pathlib import Path

from fastapi import FastAPI

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02290_v0839_route_recovery_release_contract() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    recovery = read("chrome_extension/conversation_route_recovery_v136.js")
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))

    assert SERVER_RUNTIME_VERSION == "0.22.96"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.22.96"
    assert manifest["version"] == "0.22.96"
    assert "qnbot-orphan-owner-recovery-v140-release-v02290" in payload["server"]["feature_revision"]
    assert "qnbot-orphan-owner-recovery-v140-release-v0839" in payload["chrome_bridge"]["build_revision"]
    assert payload["chrome_bridge"]["route_recovery_revision"] == 140
    assert payload["features"]["qnbot_stale_route_recovery_v136"] is True
    assert payload["features"]["qnbot_orphan_owner_recovery_v140"] is True
    assert "revision: 140" in recovery
    assert "orphaned-persisted-inflight-v140" in recovery
    assert "__chat2apiRouteRecoveryV140" in recovery
    assert "server-authority-stale-inflight-v136" in recovery
    assert "server-cancel-control-v136" in recovery
