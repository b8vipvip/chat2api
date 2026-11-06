"""Model authority must describe the *actual* overriding fetch BodyInit."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_model_authority_request_body_override_real_fetch_semantics():
    if not shutil.which("node"):
        return
    run = subprocess.run(
        ["node", "tests/model_request_body_override_v284.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + "\n" + run.stderr


def test_main_world_adapter_never_falls_back_to_stale_request_body_on_override():
    script = (ROOT / "chrome_extension/model_evidence_main_v144.js").read_text()
    assert 'Object.prototype.hasOwnProperty.call(init,"body")' in script
    assert 'if (hasOverride)' in script
    assert "new TextDecoder().decode" in script
    assert "nextInit = { ...init, headers, body: nextBody }" in script
    assert 'headers.set("content-type", originalType || "application/json")' in script
    assert "const actualWire = rewritten ? rawModel(payload) : originalWire;" in script
