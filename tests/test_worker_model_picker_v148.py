from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_live_chatgpt_picker_selection_v148():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "model_picker_selection_v148.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=35, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_model_source_ids_use_ext_transport_identity_without_rewriting_linux_management_key():
    unified = (ROOT / "app" / "admin_unified_workers_v153.js").read_text("utf-8")
    models = (ROOT / "app" / "admin_text_model_tasks_v147.js").read_text("utf-8")
    assert 'esc(text(worker?.extension_client_id)||"—（扩展未绑定）")' in unified
    assert 'data-unified-worker-id=' in unified
    assert 'row.dataset.linuxWorkerId' in unified
    assert '<code>${esc(id)}</code>' in models
    assert 'const workerById=new Map(workers.map(w=>[w.client_id,w]));' in models


def test_unclickable_observed_model_cannot_be_offered_for_routing():
    source = (ROOT / "app" / "worker_model_tasks_v147_patch.py").read_text("utf-8")
    browser = (ROOT / "chrome_extension" / "model_routing_v2.js").read_text("utf-8")
    assert 'if item["selectable"]' in source
    assert 'if model in disabled or not row["selectable"]' in source
    assert 'raw.selectable === false' in browser
    assert 'model_selection_evidence:' in browser
    assert 'model_selection_after:' in browser
