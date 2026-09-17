from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_bundle_uses_request_v6_with_network_evidence_and_liveness() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.41"
    scripts = [script for item in manifest.get("content_scripts", []) for script in item.get("js", [])]
    assert "content_request_v6.js" in scripts
    assert "content_network_stream_recovery_v55.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_generation_liveness_v49.js" in scripts
    assert "content_request_lifecycle_v50.js" in scripts
    assert "content_transient_retry_v50.js" in scripts
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_terminal_integrity_v89.js" not in scripts

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert '"content_request_v6.js"' in bootstrap
    assert '"content_network_stream_recovery_v55.js"' in bootstrap
    assert '"content_generation_liveness_v49.js"' in bootstrap
    assert '"content_response_stream_recovery_v49.js"' not in bootstrap
    assert '"content_response_stream_recovery_v69.js"' not in bootstrap

    request = (ROOT / "chrome_extension" / "content_request_v6.js").read_text(encoding="utf-8")
    semantic = (ROOT / "chrome_extension" / "content_response_semantic_recovery_v51.js").read_text(encoding="utf-8")
    heartbeat = (ROOT / "chrome_extension" / "content_generation_liveness_v49.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    network_main = (ROOT / "chrome_extension" / "network_stream_main_v55.js").read_text(encoding="utf-8")

    assert 'type: "chat.completed"' in request
    assert 'network_response_recovery: "evidence-only-v56"' in network
    assert 'network_terminal_authority: "request-v6"' in network
    assert 'type: "chat.snapshot"' in network
    assert 'type: "chat.completed"' not in network
    assert 'const PARSER_REVISION = 63;' in network_main
    assert 'mode: "semantic-helper-only"' in semantic
    assert 'owner: "request-v6"' in semantic
    assert 'timer: null' in semantic
    assert "generation_heartbeat_sequence" in heartbeat
    assert "generation_control_visible" in heartbeat

    for path in (
        ROOT / "chrome_extension" / "content_request_v6.js",
        ROOT / "chrome_extension" / "network_stream_main_v55.js",
        ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js",
        ROOT / "chrome_extension" / "content_response_semantic_recovery_v51.js",
        ROOT / "chrome_extension" / "content_generation_liveness_v49.js",
        ROOT / "chrome_extension" / "content_request_lifecycle_v50.js",
        ROOT / "chrome_extension" / "content_transient_retry_v50.js",
    ):
        result = subprocess.run(["node", "--check", str(path)], cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def test_background_loads_single_route_authority_instead_of_legacy_request_reserving_router() -> None:
    source = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"conversation_routing.js"' in source
    assert '"conversation_workers_v25.js"' not in source
