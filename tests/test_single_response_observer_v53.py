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
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.284"' in runtime
    assert '"network_response_recovery": True' in runtime
    assert 'const REQUIRED_BUNDLE = "0.22.284"' in preflight
    assert '"content_network_stream_recovery_v55.js"' in preflight
    assert '"content_request_v6.js"' in preflight
    assert 'const REQUIRED_BUNDLE = "0.22.284"' in contract
    assert 'request_v6' in contract
    assert 'network_stream_recovery_v55' in contract
    assert 'semanticHelper?.timer == null' in contract
    assert 'bundle: "0.22.284"' in marker

def test_native_rich_sidecar_does_not_replace_request_v6_terminal():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_rich_response_v69.js") < scripts.index("content_native_content_v1.js")
    assert scripts.index("content_native_content_v1.js") < scripts.index("content_request_v6.js")
    worker = (EXT / "content_request_v6.js").read_text(encoding="utf-8")
    sidecar = (EXT / "content_native_content_v1.js").read_text(encoding="utf-8")
    assert 'native?.capture?.(active, false)' in worker
    assert 'await native.capture(active, true)' in worker
    assert '"native-observer-not-loaded"' in worker
    assert 'type:"chat.native.snapshot"' in sidecar
    assert 'type:"chat.completed"' not in sidecar
    assert 'type:"chat.error"' not in sidecar


def test_native_svg_is_a_non_owning_visual_sidecar_and_roleless_scope_is_distinct():
    """Extending request-scoped vectors must not create another API terminal."""
    native = (EXT / "content_native_content_v1.js").read_text(encoding="utf-8")
    evidence = (EXT / "content_native_dom_evidence_v1.js").read_text(encoding="utf-8")
    request = (EXT / "content_request_v6.js").read_text(encoding="utf-8")
    assert 'const svgNS="http://www.w3.org/2000/svg"' in native
    assert 'nativeSvgChartsCaptured' in native
    assert 'nativeGraphicsUnsupported' in native
    assert 'canvas' in native
    # SVG/Canvas siblings may be rendered inside OPEN Shadow DOM. The
    # old querySelectorAll-only graphicNodes scan missed these nodes.
    assert 'composedFind(cursor,node=>' in evidence
    assert 'tag!=="svg" && tag!=="canvas"' in evidence
    assert 'native_roleless_scope_observed:rolelessBound === true && !nativeSelectionBound' in request
    assert 'native_message_id_scope_verified:nativeSelectionBound === true' in request
    assert 'verified_native_assistant_selection' in evidence and 'verified_native_assistant_selection' in native
    assert 'active.baselineRolelessHeadings.has(heading)' in evidence
    assert 'native_svg_charts_captured:Number(active.nativeSvgChartsCaptured || 0)' in request
    assert 'native_canvas_omitted:Number(active.nativeCanvasOmitted || 0)' in request
    assert 'type:"chat.native.snapshot"' in native
    assert 'type:"chat.completed"' not in native
    assert 'type:"chat.error"' not in native
