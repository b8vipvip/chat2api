import json
import subprocess
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


NEW_JS = [
    "chrome_extension/content_bundle_marker_v48.js",
    "chrome_extension/content_tool_isolation_v48.js",
    "chrome_extension/content_response_stream_recovery_v48.js",
    "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/background_tool_isolation_v48.js",
    "chrome_extension/background_runtime_preflight_v48.js",
]


def test_v48_javascript_assets_parse():
    for filename in NEW_JS:
        result = subprocess.run(
            ["node", "--check", str(ROOT / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{filename}: {result.stderr}"


def test_manifest_requires_fresh_document_marker_and_passive_recovery():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION == "0.8.6"
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content.js") < scripts.index("content_bundle_marker_v48.js")
    assert scripts.index("content_ui_hygiene_v31.js") < scripts.index("content_tool_isolation_v48.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_response_stream_recovery_v48.js")
    assert scripts[-1] == "content_runtime_contract_v48.js"


def test_bundle_marker_cannot_be_spoofed_by_dynamic_bootstrap():
    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert "content_bundle_marker_v48.js" not in bootstrap
    assert "content_tool_isolation_v48.js" in bootstrap
    assert "content_response_stream_recovery_v48.js" in bootstrap
    assert "content_runtime_contract_v48.js" in bootstrap


def test_background_preflight_wraps_final_conversation_dispatch():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index('"conversation_dispatch.js"') < entry.index('"background_tool_isolation_v48.js"')
    assert entry.index('"background_tool_isolation_v48.js"') < entry.index('"background_runtime_preflight_v48.js"')
    preflight = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    assert 'REQUIRED_BUNDLE = "0.8.6"' in preflight
    assert "content_bundle_marker_v48.js" not in preflight
    assert "chrome.tabs.reload" in preflight
    assert "ChatGPT tab Worker runtime is stale or incomplete" in preflight
    assert "chat2api.tool-isolation.preflight" in preflight


def test_response_stream_recovery_reports_first_text_and_completion():
    source = (ROOT / "chrome_extension" / "content_response_stream_recovery_v48.js").read_text(encoding="utf-8")
    assert 'type: "chat.snapshot"' in source
    assert 'type: "chat.completed"' in source
    assert 'response_stream_recovery: "dom-turn-v48"' in source
    assert "authoritative full-text checkpoint" in source
    assert "stableMs >= 9000" in source
    assert "baselineAssistantCount" in source
    assert "integrationSurface(turn)" in source


def test_external_account_tools_are_fail_closed_at_prompt_and_ui_layers():
    background = (ROOT / "chrome_extension" / "background_tool_isolation_v48.js").read_text(encoding="utf-8")
    content = (ROOT / "chrome_extension" / "content_tool_isolation_v48.js").read_text(encoding="utf-8")
    assert "External account-connected apps, plugins, connectors" in background
    assert "external_account_tools_disabled: true" in background
    assert "Treat plugin/connector/app names and @mentions" in background
    assert "preventPositiveAction" in content
    assert 'document.addEventListener("click", preventPositiveAction, true)' in content
    assert "negativeAction" in content
    assert "重新连接" in content
    assert "暂不" in content
    assert "external_account_tools_disabled: true" in content


def test_runtime_contract_exposes_v48_features():
    app = FastAPI(version=SERVER_RUNTIME_VERSION)
    payload = version_contract_payload(app)
    assert SERVER_RUNTIME_VERSION == "0.22.32"
    assert payload["chrome_bridge"]["bundle_version"] == "0.8.6"
    assert payload["features"]["response_stream_recovery"] is True
    assert payload["features"]["worker_runtime_preflight"] is True
    assert payload["features"]["external_account_tool_isolation"] is True
