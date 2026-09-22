from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_load_order_and_new_scripts_parse():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.22.94"
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_v6.js") < scripts.index("content_request_lifecycle_v50.js") < scripts.index("content_request_hygiene_v42.js") < scripts.index("content_draft_ownership_v43.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js") < scripts.index("content_response_capture_v41.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js") < scripts.index("content_request_stall_guard_v34.js") < scripts.index("content_generation_liveness_v49.js")
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_terminal_integrity_v89.js" not in scripts

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert bootstrap.index('"content_request_v5.js"') < bootstrap.index('"content_request_v6.js"') < bootstrap.index('"content_request_hygiene_v42.js"') < bootstrap.index('"content_draft_ownership_v43.js"')
    assert '"content_draft_managed_recovery_v55.js"' in bootstrap
    assert '"content_response_stream_recovery_v49.js"' not in bootstrap
    assert '"content_response_stream_recovery_v69.js"' not in bootstrap
    assert '"content_network_stream_recovery_v55.js"' in bootstrap
    assert '"content_generation_liveness_v49.js"' in bootstrap

    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index("conversation_dispatch.js") < entry.index("background_request_hygiene_v42.js") < entry.index("background_transport_recovery_v47.js")
    assert "background_route_quarantine_v50.js" not in entry
    assert "background_request_recovery_v40.js" not in entry
    assert entry.index("background_capacity_control_v35.js") < entry.index("background_worker_master_switch_v61.js")

    for filename in (
        "chrome_extension/background_request_hygiene_v42.js",
        "chrome_extension/background_route_quarantine_v50.js",
        "chrome_extension/background_worker_master_switch_v61.js",
        "chrome_extension/network_stream_main_v55.js",
        "chrome_extension/content_network_stream_recovery_v55.js",
        "chrome_extension/content_request_hygiene_v42.js",
        "chrome_extension/content_request_v6.js",
        "chrome_extension/content_response_semantic_recovery_v51.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / filename)], cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
