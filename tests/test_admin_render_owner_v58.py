from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_settings_has_one_structural_owner() -> None:
    worker = read("app/admin_v21_5.js")
    health = read("app/admin_v21_6.js")
    columns = read("app/admin_extension_columns.js")

    assert 'data-chat2api-structural-owner="worker-settings-v59"' in worker
    assert 'globalThis.__CHAT2API_WORKER_SETTINGS_RENDER_OWNER_V59__' in worker
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV59' in worker
    assert 'column: "worker_settings"' in worker
    assert 'structural_updates: "create-once-update-values"' in worker
    assert 'polling: false' in worker

    assert '{key: "worker_settings", label: "并发设置"}' in columns
    assert '{key: "bound_api_keys", label: "绑定 API Key 数"}' not in columns
    assert '{key: "concurrency"' not in columns
    assert '{key: "reserve_windows"' not in columns
    assert '{key: "platform"' not in columns

    assert 'renderState(ensureCell(tr, "worker_settings")' not in health
    assert 'health_columns: ["network", "chatgpt"]' in health
    assert 'chained_capacity_poll: false' in health


def test_worker_settings_refresh_is_event_driven_and_idempotent() -> None:
    worker = read("app/admin_v21_5.js")

    assert "setInterval(" not in worker
    assert "requestAnimationFrame" in worker
    assert 'new MutationObserver(mutations =>' in worker
    assert '.observe(extensionBody, {childList: true})' in worker
    assert 'tr.querySelector(\'td[data-chat2api-column-key="worker_settings"]\')' in worker
    assert 'if (!editor || String(editor.dataset.clientId || "") !== clientId)' in worker
    assert 'if (maxInput && Number(maxInput.value) !== maximum) maxInput.value = String(maximum)' in worker
    assert 'if (reserveInput && Number(reserveInput.value) !== reserve) reserveInput.value = String(reserve)' in worker
    assert "data-worker-live" not in worker
    assert "data-worker-platform" not in worker
    assert "platformText(" not in worker
    assert 'columnCell(tr, "platform", 8)' not in worker
    assert 'patchColumnSettingsLabels' not in worker


def test_canonical_worker_list_retires_legacy_multi_stage_rendering() -> None:
    columns = read("app/admin_extension_columns.js")

    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v3"' in columns
    assert 'const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2"' in columns
    assert 'legacy_renderers_bypassed: true' in columns
    assert 'if (viewName !== "extensions") return baseShow(viewName)' in columns
    assert 'activateExtensionView();' in columns
    assert 'return loadCanonicalExtensions(true);' in columns
    assert 'data-chat2api-canonical-worker-row="1"' in columns
    assert 'DEFAULT_ORDER.every(key => Boolean(keyedChild(tr, key)))' in columns
    assert 'queueMicrotask(() =>' in columns
    assert 'table.style.visibility = "hidden"' in columns
    assert 'table.style.visibility = ""' in columns


def test_canonical_worker_header_and_rows_use_same_keys() -> None:
    columns = read("app/admin_extension_columns.js")
    expected = [
        "client_id",
        "device_id",
        "version",
        "account_type",
        "status",
        "worker_settings",
        "last_seen",
        "network",
        "chatgpt",
        "actions",
        "device_name",
        "occupancy",
    ]
    for key in expected:
        assert f'{{key: "{key}",' in columns
        assert f'data-chat2api-column-key="{key}"' in columns
    assert '{key: "bound_api_keys",' not in columns
    assert '{key: "occupied_windows",' not in columns
    assert 'data-chat2api-column-key="bound_api_keys"' not in columns
    assert 'removed_columns: ["concurrency", "reserve_windows", "platform", "bound_api_keys", "occupied_windows"]' in columns
    assert '旧并发列（已合并）' not in columns
    assert '旧备用窗口列（已合并）' not in columns


def test_health_refresh_does_not_self_invalidate_table() -> None:
    health = read("app/admin_v21_6.js")

    assert 'const POLL_MS = 5000' in health
    assert 'function setText(node, value)' in health
    assert 'if (node && node.textContent !== next) node.textContent = next' in health
    assert 'setText(th, label)' in health
    assert 'setText(versionCell, effectiveVersion(row))' in health
    assert 'setText(cell, state.label)' in health
    assert 'setInterval(refreshHealthCenter, POLL_MS)' not in health
    assert 'schedulePoll(POLL_MS)' in health
    assert '!document.hidden && extensionViewActive()' in health


def test_common_bug_document_records_render_owner_rule() -> None:
    doc = read("docs/COMMON_DEVELOPMENT_BUGS.md")
    assert "Multiple Render Owners" in doc
    assert "一个结构区域只能有一个 Structural Owner" in doc
    assert "Self-invalidating Presentation Poll" in doc
    assert "请求一直 Running，但 Worker 槽位不释放" in doc


def test_admin_render_owner_scripts_parse_as_javascript() -> None:
    for path in ("app/admin_extension_columns.js", "app/admin_v21_5.js", "app/admin_v21_6.js"):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
