from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v133_guard_load_order_wraps_final_request_resolver() -> None:
    entry = text("chrome_extension/background_entry.js")
    router = entry.index('"conversation_routing.js"')
    pool = entry.index('"conversation_persistent_pool_v132.js"')
    restore = entry.index('"conversation_persistent_route_restore_v132.js"')
    guard = entry.index('"conversation_persistent_pool_guard_v133.js"')
    dispatch = entry.index('"conversation_dispatch.js"')
    assert router < pool < restore < guard < dispatch


def test_v133_guard_serialises_repair_and_delegates_login_lifecycle_to_pool() -> None:
    source = text("chrome_extension/conversation_persistent_pool_guard_v133.js")
    assert 'await pool.reconcile("request-admission-barrier-v133")' in source
    assert 'reason: "delegated-to-persistent-window-pool-v137"' in source
    assert 'login-state:' in source
    assert 'await chrome.windows.remove' not in source
    assert 'chrome.windows.create' not in source
    assert 'route.window_id = null' not in source


def test_v133_window_authority_overlay_runs_after_observer_before_refresh() -> None:
    entry = text("chrome_extension/background_entry.js")
    observer = entry.index('"background_window_observer_v90.js"')
    authority = entry.index('"background_window_authority_v133.js"')
    refresh = entry.index('"background_window_refresh_v129.js"')
    assert observer < authority < refresh

    source = text("chrome_extension/background_window_authority_v133.js")
    assert 'window_decision_authority: state.authority' in source
    assert 'route_window_authority: state.route_authority' in source
    assert 'persistent_window_pool_revision: 132' in source
    assert 'prewarmed_windows: true' in source
    assert 'speculative_windows: false' in source


def test_v133_javascript_contract_and_syntax() -> None:
    vm_contract = subprocess.run(
        ["node", str(ROOT / "tests" / "persistent_pool_guard_v133.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert vm_contract.returncode == 0, vm_contract.stdout + vm_contract.stderr
    assert "persistent pool guard v133 VM contract passed" in vm_contract.stdout

    for path in (
        "chrome_extension/conversation_persistent_pool_guard_v133.js",
        "chrome_extension/background_window_authority_v133.js",
        "chrome_extension/background_entry.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
