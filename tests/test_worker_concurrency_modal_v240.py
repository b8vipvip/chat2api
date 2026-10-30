from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v240_worker_concurrency_editor_is_one_top_layer_form_with_save_feedback():
    assert shutil.which("node"), "The repo CI requires Node for browser contracts"
    completed = subprocess.run(
        ["node", "tests/worker_concurrency_modal_v240.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_v240_admin_script_urls_refresh_after_old_browser_cache():
    runtime = (ROOT / "app/runtime_contract.py").read_text(encoding="utf-8")
    limits = (ROOT / "app/worker_limits_clipboard_v121_patch.py").read_text(encoding="utf-8")
    unified = (ROOT / "app/worker_auto_login_v153_patch.py").read_text(encoding="utf-8")
    assert '?v=0.22.256' in runtime
    assert 'ASSET_RELEASE = "0.22.256"' in limits
    assert '?v=0.22.256' in unified
    assert "data-v121-windows" not in (ROOT / "app/admin_extension_columns.js").read_text(encoding="utf-8")
