from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_multimodal_ready_gate_waits_for_real_upload_completion() -> None:
    source = text("chrome_extension/content_multimodal_settle_v84.js")
    assert "const REVISION = 84" in source
    assert "[aria-busy='true']" in source
    assert "[class*='animate-spin']" in source
    assert "style.animationName" in source
    assert "waitForReady" in source
    assert "ready_stable_ms" in source
    assert "attachment_ready_stage" in source
    assert "chrome.runtime.onMessage.removeListener(prior.listener)" in source
    assert "const data = await prior.attach(specs)" in source
    assert "const ready = await waitForReady(count" in source


def test_multimodal_ready_gate_javascript_syntax() -> None:
    completed = subprocess.run(
        ["node", "--check", str(ROOT / "chrome_extension" / "content_multimodal_settle_v84.js")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_worker_manifest_loads_ready_gate_immediately_after_v78_uploader() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    assert manifest["version"] == "0.8.27"
    isolated = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")
    scripts = isolated["js"]
    assert scripts.index("content_multimodal_v78.js") < scripts.index("content_multimodal_settle_v84.js")
    assert scripts.index("content_multimodal_settle_v84.js") < scripts.index("content_multimodal_v68.js")


def test_runtime_preflight_requires_v84_ready_gate() -> None:
    source = text("chrome_extension/background_runtime_preflight_v48.js")
    contract = text("chrome_extension/content_runtime_contract_v71.js")
    assert '"content_multimodal_settle_v84.js"' in source
    assert "result?.modules?.multimodal_v78" in source
    assert "result?.modules?.multimodal_v84" in source
    assert "multimodal revision 85" in source
    assert "multimodal_v84" in contract
    assert 'typeof multimodal?.waitForReady === "function"' in contract


def test_v02255_runtime_and_bundle_contract() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.58"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.27"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["chrome_bridge"]["multimodal_revision"] == 85
    assert payload["features"]["multimodal_upload_ready_v84"] is True
