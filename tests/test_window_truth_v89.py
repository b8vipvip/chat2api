from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v89_browser_truth_refresh_is_retired_and_v90_observer_is_loaded() -> None:
    entry = text("chrome_extension/background_entry.js")
    for retired in (
        "background_window_manager_v88.js",
        "background_window_lifecycle_observer_v88.js",
        "background_window_truth_refresh_v89.js",
    ):
        assert f'"{retired}"' not in entry
    assert '"conversation_routing.js"' in entry
    assert '"background_window_observer_v90.js"' in entry
    assert '__CHAT2API_WINDOW_OBSERVER_V90__?.report?.(true)' in entry


def test_legacy_v89_refresh_source_remains_inspectable_but_v90_observer_owns_reporting() -> None:
    source = text("chrome_extension/background_window_truth_refresh_v89.js")
    observer = text("chrome_extension/background_window_observer_v90.js")
    assert 'message?.type === "window.manager.refresh"' in source
    assert "physical.reconcile()" in source
    assert "manager.reconcile(true)" in source
    assert "manager.report(true)" in source
    assert 'refreshPhysicalTruth("service-worker-start")' in source
    assert "updated_at_ms" in source

    assert 'policy: "observe-only-single-route-authority-v90"' in observer
    assert "state.reconcile = liveRoutes" in observer
    assert "state.report = report" in observer
    assert 'window_decision_authority: "conversation-routing-v30"' in observer
    assert "decision_authority: false" in observer


def test_server_never_promotes_cached_active_rows_without_fresh_proof() -> None:
    source = text("app/window_manager_v88_patch.py")
    assert "LIVE_TRUTH_REVISION = 89" in source
    assert "LIVE_TRUTH_MIN_BUNDLE = (0, 8, 27)" in source
    assert '"type": "window.manager.refresh"' in source
    assert "_snapshot_updated_at(row) > before.get(client_id, 0)" in source
    assert "if live_verified:" in source
    assert '"cached_active_rows_suppressed"' in source
    assert 'truth_status = "upgrade-required"' in source
    # A persisted v88 snapshot is historical telemetry, not live truth. Active
    # rows may only be appended inside the verified branch.
    verified_index = source.index("if live_verified:", source.index("def window_rows"))
    append_index = source.index("active.append(item)", verified_index)
    next_closed_loop = source.index('for raw in snapshot.get("closed")', verified_index)
    assert verified_index < append_index < next_closed_loop


def test_admin_surfaces_unverified_and_suppressed_window_state() -> None:
    ui = text("app/admin_window_manager_v88.js")
    worker_ui = text("app/admin_worker_presentation_v66.js")
    assert "wmTruthStatus" in ui
    assert "实时物理核验" in ui
    assert "历史缓存窗口" in ui
    assert "cached_active_rows_suppressed" in ui
    assert "请求 / 实际窗口" in worker_ui
    assert 'callApi("/api/admin/window-manager")' in worker_ui
    assert "liveWindowTruth" in worker_ui
    assert "旧版遥测（未实时核验）" in worker_ui


def test_v89_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        return
    for filename in (
        "chrome_extension/background_entry.js",
        "chrome_extension/background_window_truth_refresh_v89.js",
        "app/admin_window_manager_v88.js",
        "app/admin_worker_presentation_v66.js",
    ):
        completed = subprocess.run(
            [node, "--check", str(ROOT / filename)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
