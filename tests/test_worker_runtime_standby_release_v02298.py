from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v02298_unifies_server_worker_bundle_and_publishes_v145() -> None:
    runtime = text("app/runtime_contract.py")
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert 'RELEASE_VERSION = "0.22.105"' in runtime
    assert 'SERVER_RUNTIME_VERSION = "0.22.105"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.22.105"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.105"' in runtime
    assert manifest["version"] == "0.22.105"
    assert '"worker_runtime_preflight_repair_v145": True' in runtime
    assert '"routable_standby_semantics_v145": True' in runtime
    assert "worker-runtime-preflight-repair-v145-routable-standby-v145-release-v02298" in runtime


def test_v02298_preflight_repairs_persistent_tabs_with_v144_evidence() -> None:
    preflight = text("chrome_extension/background_runtime_preflight_v48.js")
    bootstrap = text("chrome_extension/content_bootstrap.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")
    assert 'const REQUIRED_BUNDLE = "0.22.105"' in preflight
    assert "model_evidence_main_v144.js" in preflight
    assert "content_model_evidence_v144.js" in preflight
    assert "model_evidence_main_v144.js" in bootstrap
    assert "content_model_evidence_v144.js" in bootstrap
    assert "model_evidence_v144" in contract
    assert "model_evidence_main_v144" in contract
    assert 'mode: "repair-budget-exhausted-v145"' in preflight
    assert "missing_modules: missing" in preflight


def test_v02298_standby_is_routable_idle_capacity() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    capacity = text("chrome_extension/background_capacity_control_v35.js")
    assert "const routableRows = rows.filter(row => row.routable);" in pool
    assert "function standbyRows(rows, value)" in pool
    assert "!assigned.has(row.window_id)" in pool
    assert "!busy.has(row.window_id)" in pool
    assert "const STANDBY_SEMANTICS_REVISION = 152" in pool
    assert "standby_semantics_revision: STANDBY_SEMANTICS_REVISION" in pool
    assert "runtime_ready_standby: true" in pool
    assert "validateIdleRuntime" in pool
    assert "const standby = Number(snapshot.idle || snapshot.standby || 0);" in capacity
    assert "standby === target" in capacity
