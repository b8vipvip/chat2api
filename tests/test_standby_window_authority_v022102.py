from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_list_has_one_final_renderer_without_old_flash_passes() -> None:
    canonical = text("app/admin_extension_columns.js")
    behavior = text("app/admin_worker_limits_clipboard_v121.js")
    legacy = text("app/admin_worker_presentation_v66.js")

    assert '{key: "worker_settings", label: "并发 / 备用设置"}' in canonical
    assert '{key: "occupancy", label: "请求 / 备用窗口"}' in canonical
    assert 'table.style.visibility = "hidden"' in canonical
    assert 'Promise.all([' in canonical
    assert 'api("/api/admin/window-manager")' in canonical
    assert 'document.documentElement.dataset.chat2apiWorkerListSingleRenderer = "1"' in canonical

    assert 'jsonRequest("/api/admin/extensions")' not in behavior
    assert "installWorkerHooks" not in behavior
    assert "setTimeout(() => { installWorkerHooks" not in behavior
    assert 'renderer: "canonical-worker-list-v152"' in behavior

    assert 'retired_renderer: true' in legacy
    assert "setTimeout(() => refresh(true)" not in legacy
    assert 'callApi("/api/admin/extensions")' not in legacy


def test_standby_target_excludes_active_and_five_minute_route_leases() -> None:
    pool = text("chrome_extension/conversation_persistent_pool_v132.js")
    router = text("chrome_extension/conversation_routing.js")

    assert 'const STANDBY_SEMANTICS_REVISION = 152' in pool
    assert 'function standbyRows(rows, value)' in pool
    assert '!assigned.has(row.window_id)' in pool
    assert '!busy.has(row.window_id)' in pool
    assert 'while (standbyRows(rows, value).length < target)' in pool
    assert 'post-admission-standby-refill-v152' in pool
    assert 'route.window_owned = true' in pool
    assert 'markPooledRoute' not in pool
    assert 'reassign-idle-persistent-route' not in pool

    assert 'const IDLE_CLOSE_MS = 5 * 60 * 1000' in router
    assert 'route.close_after = Date.now() + IDLE_CLOSE_MS' in router
    assert 'chrome.alarms.create(closeAlarmName(route.window_id)' in router


def test_live_window_truth_distinguishes_standby_in_use_and_leased() -> None:
    observer = text("chrome_extension/background_window_observer_v90.js")
    server = text("app/window_manager_v88_patch.py")
    admin = text("app/admin_window_manager_v88.js")

    assert 'source: routed ? "route-observer-v152" : "standby-observer-v152"' in observer
    assert 'status: routed ? (inflight ? "in_use" : "leased") : "ready"' in observer
    assert 'standby_count:' in observer
    assert 'leased_count:' in observer
    assert 'standby_window_count' in server
    assert 'api_key_name' in server
    assert 'return "CHAT2API_API_KEY"' in server
    assert 'return `正在接待${name || "API请求"}`' in admin
    assert 'if (value === "ready") return "可接待"' in admin
    assert 'return "已关闭"' in admin


def test_changed_javascript_parses() -> None:
    for path in (
        "app/admin_extension_columns.js",
        "app/admin_worker_limits_clipboard_v121.js",
        "app/admin_worker_presentation_v66.js",
        "app/admin_window_manager_v88.js",
        "chrome_extension/conversation_persistent_pool_v132.js",
        "chrome_extension/conversation_persistent_route_restore_v132.js",
        "chrome_extension/background_window_observer_v90.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
