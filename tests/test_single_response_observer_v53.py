from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_v51_is_non_owning_helper_for_request_v6():
    v51 = (EXT / "content_response_semantic_recovery_v51.js").read_text(encoding="utf-8")
    assert 'mode: "semantic-helper-only"' in v51
    assert 'owner: "request-v6"' in v51
    assert 'timer: null' in v51
    assert 'clearInterval' not in v51
    assert 'setInterval' not in v51


def test_worker_manifest_has_one_response_terminal_owner():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_request_v6.js" in scripts
    assert "content_network_stream_recovery_v55.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_terminal_integrity_v89.js" not in scripts
    assert scripts.index("content_request_v6.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js")


def test_runtime_contract_exposes_request_v6_plus_network_evidence():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    preflight = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (EXT / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (EXT / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.94"' in runtime
    assert '"network_response_recovery": True' in runtime
    assert 'const REQUIRED_BUNDLE = "0.22.94"' in preflight
    assert '"content_network_stream_recovery_v55.js"' in preflight
    assert '"content_request_v6.js"' in preflight
    assert 'const REQUIRED_BUNDLE = "0.22.94"' in contract
    assert 'request_v6' in contract
    assert 'network_stream_recovery_v55' in contract
    assert 'semanticHelper?.timer == null' in contract
    assert 'bundle: "0.22.94"' in marker
