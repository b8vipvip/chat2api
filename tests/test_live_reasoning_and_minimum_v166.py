from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_dynamic_model_and_reasoning_dom_authority():
    if not shutil.which("node"):
        return
    result = subprocess.run(
        ["node", str(ROOT/"tests"/"model_reasoning_evidence_v166.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr

def test_legacy_minimum_selection_has_one_explicit_configuration_owner():
    model = (ROOT/"app"/"legacy_mini_default_alias_v163_patch.py").read_text(encoding="utf-8")
    management = (ROOT/"app"/"worker_text_model_management_v146_patch.py").read_text(encoding="utf-8")
    assert "def current_default_model(" in model
    assert 'getattr(registry, "legacy_minimum_model", None)' in model
    assert "Prefer an instant/non-reasoning" not in model
    assert 'registry.legacy_minimum_model = lambda: minimum_model' in management
    assert '"/api/admin/text-model-minimum"' in management
    assert '"legacy_minimum_model": minimum_model' in management
