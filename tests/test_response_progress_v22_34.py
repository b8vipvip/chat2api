from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_bundle_loads_v49_progress_probe_and_diagnostic_heartbeat() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.21"
    scripts = [script for item in manifest.get("content_scripts", []) for script in item.get("js", [])]
    assert "content_response_stream_recovery_v49.js" in scripts
    assert "content_network_stream_recovery_v55.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_generation_liveness_v49.js" in scripts
    assert "content_request_lifecycle_v50.js" in scripts
    assert "content_transient_retry_v50.js" in scripts
    assert "content_response_stream_recovery_v48.js" not in scripts
    assert "content_generation_liveness_v42.js" not in scripts

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert '"content_response_stream_recovery_v49.js"' in bootstrap
    assert '"content_network_stream_recovery_v55.js"' in bootstrap
    assert '"content_generation_liveness_v49.js"' in bootstrap
    assert '"content_response_stream_recovery_v48.js"' not in bootstrap
    assert '"content_generation_liveness_v42.js"' not in bootstrap

    recovery = (ROOT / "chrome_extension" / "content_response_stream_recovery_v49.js").read_text(encoding="utf-8")
    semantic = (ROOT / "chrome_extension" / "content_response_semantic_recovery_v51.js").read_text(encoding="utf-8")
    heartbeat = (ROOT / "chrome_extension" / "content_generation_liveness_v49.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    network_main = (ROOT / "chrome_extension" / "network_stream_main_v55.js").read_text(encoding="utf-8")

    assert 'owner_revision: 53' in recovery
    assert 'page_progress_probe: "page-progress-v49"' in recovery
    assert 'page_probe_failure: "chatgpt-ui-stuck"' in recovery
    assert "const IDLE_STUCK_MS = 25000" in recovery
    assert "const NON_IDLE_STUCK_MS = 45000" in recovery
    assert "const VISIBLE_GENERATION_STUCK_MS = 120000" in recovery
    assert 'type: "chat.snapshot"' in recovery
    assert 'type: "chat.completed"' in recovery
    assert 'type: "chat.error"' in recovery
    assert 'network_response_recovery: "sse-assistant-v55"' in network
    assert 'type: "chat.snapshot"' in network
    assert 'type: "chat.completed"' in network
    assert 'const PARSER_REVISION = 62;' in network_main
    assert 'data-chat2api-network-stream-parser' in network_main
    assert 'mode: "semantic-helper-only"' in semantic
    assert 'timer: null' in semantic

    assert "generation_heartbeat_sequence" in heartbeat
    assert "generation_control_visible" in heartbeat
    assert "generation_sequence:" not in heartbeat

    for path in (
        ROOT / "chrome_extension" / "content_response_stream_recovery_v49.js",
        ROOT / "chrome_extension" / "network_stream_main_v55.js",
        ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js",
        ROOT / "chrome_extension" / "content_response_semantic_recovery_v51.js",
        ROOT / "chrome_extension" / "content_generation_liveness_v49.js",
        ROOT / "chrome_extension" / "content_request_lifecycle_v50.js",
        ROOT / "chrome_extension" / "content_transient_retry_v50.js",
        ROOT / "chrome_extension" / "conversation_workers_v25.js",
    ):
        result = subprocess.run(["node", "--check", str(path)], cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def test_page_progress_thresholds_distinguish_idle_intermediate_and_true_generation() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_response_stream_recovery_v49.js", "utf8");
class FakeElement {}
const context = {
  console,
  Date,
  Map,
  Set,
  Promise,
  Element: FakeElement,
  getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  document: { querySelectorAll: () => [] },
  chrome: { runtime: { sendMessage: async () => ({ ok: true }) } },
  setInterval: () => 1,
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "content_response_stream_recovery_v49.js" });

const recovery = context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__;
assert.ok(recovery);
assert.equal(recovery.constants.diagnostic_after_ms, 15000);
assert.equal(recovery.stuckThreshold({ stop_visible: false, send_ready: true, status_active: false }), 25000);
assert.equal(recovery.stuckThreshold({ stop_visible: false, send_ready: false, status_active: false }), 45000);
assert.equal(recovery.stuckThreshold({ stop_visible: false, send_ready: true, status_active: true }), 45000);
assert.equal(recovery.stuckThreshold({ stop_visible: true, send_ready: false, status_active: true }), 120000);
'''
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_background_loads_request_reserving_worker_router() -> None:
    source = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"conversation_workers_v25.js"' in source
    assert '"conversation_workers_v24.js"' not in source
    assert '"background_route_quarantine_v50.js"' in source

    router = (ROOT / "chrome_extension" / "conversation_workers_v25.js").read_text(encoding="utf-8")
    assert "routeReservations: new Map()" in router
    assert "Reserve synchronously before awaiting tab allocation" in router
    assert 'extension_worker_router: "per-api-key-v25-request-reservation"' in router
    assert "extension_worker_request_reservation: true" in router
