from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v88_is_the_final_background_window_authority() -> None:
    entry = text("chrome_extension/background_entry.js")
    assert '"background_window_manager_v88.js"' in entry
    assert '"background_window_lifecycle_observer_v88.js"' in entry
    assert entry.index('"background_window_affinity_v87.js"') < entry.index('"background_window_manager_v88.js"')
    assert entry.index('"background_window_manager_v88.js"') < entry.index('"background_window_lifecycle_observer_v88.js"')
    manager = text("chrome_extension/background_window_manager_v88.js")
    assert 'policy: "oldest-ready-fifo-v88"' in manager
    assert 'source: "reserve"' in manager
    assert 'source: "warm"' in manager
    assert "rows.sort((a, b) =>" in manager
    assert "a.opened_at_ms" in manager and "b.opened_at_ms" in manager
    assert "claimOldestReady" in manager
    assert "window_opened_at_ms" in manager
    assert "window_no" in manager


def test_window_creation_is_registered_as_loading_before_pool_readiness() -> None:
    source = text("chrome_extension/background_window_lifecycle_observer_v88.js")
    assert "const baseCreate = chrome.windows.create.bind(chrome.windows)" in source
    assert "openedAt = Date.now()" in source
    assert 'status: "loading"' in source
    assert 'source: "creation-observer-v88"' in source
    assert "wm.nextWindowNo" in source
    assert "wm.active.set(win.id, record)" in source
    assert "wm.reconcile?.(true)" in source


def test_success_terminal_cannot_be_downgraded_to_cancel() -> None:
    guard = text("chrome_extension/content_request_terminal_prompt_v88.js")
    manager = text("chrome_extension/background_window_manager_v88.js")
    assert 'event?.type === "chat.cancelled" || event?.type === "chat.error"' in guard
    assert "active?.networkCompleted === true" in guard
    assert 'reason: "network-success-is-terminal-v88"' in guard
    assert "state.protectedUntil" in manager
    assert "repairSuccessfulRoute" in manager
    assert "SUCCESS_LEASE_MS = 5 * 60 * 1000" in manager


def test_long_prompt_avoids_unbounded_execcommand_inserttext() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    scripts = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")["js"]
    assert scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_request_terminal_prompt_v88.js")
    guard = text("chrome_extension/content_request_terminal_prompt_v88.js")
    assert "LONG_PROMPT_THRESHOLD = 2048" in guard
    assert "editable.replaceChildren(document.createTextNode(text))" in guard
    assert 'prompt_fast_insert_method: "direct-text-node+input-event"' in guard


def test_admin_window_management_is_installed_and_isolated_from_request_history() -> None:
    entry = text("app/entry.py")
    patch = text("app/window_manager_v88_patch.py")
    ui = text("app/admin_window_manager_v88.js")
    assert "install_window_manager_v88_patch(app)" in entry
    assert '"/api/admin/window-manager"' in patch
    assert '"/api/admin/window-manager/{client_id}/{window_id}/capture"' in patch
    for token in ("窗口管理", "接待中窗口", "已关闭窗口", "窗口编号", "设备码名称", "请求ID", "截图当前界面"):
        assert token in ui
    assert "request_id" in ui
    assert 'document.getElementById("rqBody")' not in ui
    assert '$("rqBody")' not in ui
    assert "MutationObserver" not in ui
    assert 'structural_owner: "window-manager-only"' in ui


def test_v88_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        return
    for filename in (
        "chrome_extension/background_window_manager_v88.js",
        "chrome_extension/background_window_lifecycle_observer_v88.js",
        "chrome_extension/content_request_terminal_prompt_v88.js",
        "app/admin_window_manager_v88.js",
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


def test_production_entry_exposes_window_manager_routes() -> None:
    from app.entry import app

    paths = {getattr(route, "path", "") for route in app.router.routes}
    assert "/api/admin/window-manager" in paths
    assert "/api/admin/window-manager/{client_id}/{window_id}/capture" in paths
