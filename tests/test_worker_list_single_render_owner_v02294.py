from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_list_has_one_visible_render_owner_and_no_old_refresh_style() -> None:
    canonical = read("app/admin_extension_columns.js")
    legacy_capacity = read("app/admin_v21_5.js")
    legacy_health = read("app/admin_v21_6.js")
    limits = read("app/admin_worker_limits_clipboard_v121.js")
    presentation = read("app/admin_worker_presentation_v66.js")

    assert '{key: "worker_settings", label: "并发 / 窗口"}' in canonical
    assert '{key: "occupancy", label: "请求 / 实际窗口"}' in canonical
    assert "data-v121-limit-summary" in canonical
    assert "data-v121-limit-popover hidden" in canonical
    assert 'api("/api/admin/window-manager")' in canonical
    assert 'presentation_owner: "admin_extension_columns"' in canonical
    assert "legacy_renderers_removed: true" in canonical
    assert "MutationObserver" not in canonical

    for token in ("data-worker-window-editor", "data-worker-max", "data-worker-reserve", "data-worker-save", "data-worker-refresh"):
        assert token not in legacy_capacity
    assert "extensionDeviceBody" not in legacy_capacity
    assert "legacy_renderer_removed: true" in legacy_capacity

    assert "extensionDeviceBody" not in legacy_health
    assert "setTimeout(" not in legacy_health
    assert "setInterval(" not in legacy_health
    assert "MutationObserver" not in legacy_health
    assert "legacy_health_renderer_removed: true" in legacy_health

    assert "headerCell.textContent" not in limits
    assert "cell.innerHTML = html" not in limits
    assert "Worker-list presentation is owned exclusively by admin_extension_columns.js" in limits

    assert "extensionDeviceBody" not in presentation
    assert "applyRows" not in presentation
    assert "ensureHeader" not in presentation
    assert "worker_table_render_authority:false" in presentation


def test_worker_list_assets_remain_valid_javascript() -> None:
    for path in (
        "app/admin_extension_columns.js",
        "app/admin_v21_5.js",
        "app/admin_v21_6.js",
        "app/admin_worker_limits_clipboard_v121.js",
        "app/admin_worker_presentation_v66.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"
