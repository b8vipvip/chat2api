from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_settings_has_one_structural_owner() -> None:
    legacy = read("app/admin_v21_5.js")
    health = read("app/admin_v21_6.js")
    columns = read("app/admin_extension_columns.js")

    assert '{key: "worker_settings", label: "并发 / 备用设置"}' in columns
    assert 'data-chat2api-structural-owner="worker-settings-v152"' in columns
    assert 'data-v121-limit-summary' in columns
    assert 'data-v121-edit-limits' in columns
    assert '{key: "bound_api_keys", label: "绑定 API Key 数"}' not in columns
    assert '{key: "concurrency"' not in columns
    assert '{key: "reserve_windows"' not in columns
    assert '{key: "platform"' not in columns

    assert 'retired: true' in legacy
    assert 'delegated_to: "admin_extension_columns-v152"' in legacy
    assert 'renderer: "canonical-worker-list-v152"' in legacy
    assert 'data-worker-window-editor' not in legacy
    assert 'ensureWorkerSettingsStructure' not in legacy
    assert 'refreshWorkerSettings' not in legacy
    assert 'data-worker-save' not in legacy
    assert 'data-worker-refresh' not in legacy

    assert 'renderState(ensureCell(tr, "worker_settings")' not in health
    assert 'health_columns: ["network", "chatgpt"]' in health
    assert 'chained_capacity_poll: false' in health


def test_worker_settings_refresh_is_owned_by_canonical_renderer_only() -> None:
    legacy = read("app/admin_v21_5.js")
    columns = read("app/admin_extension_columns.js")

    assert 'data-worker-window-editor' not in legacy
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV59' not in legacy
    assert 'ensureWorkerSettingsStructure' not in legacy
    assert 'refreshWorkerSettings' not in legacy
    assert 'data-v121-worker-limits' in columns
    assert 'data-v121-save-limits' in columns
    assert 'loadCanonicalExtensions' in columns
    assert 'table.style.visibility = "hidden"' in columns


def test_canonical_worker_list_retires_legacy_multi_stage_rendering() -> None:
    columns = read("app/admin_extension_columns.js")
    behavior = read("app/admin_worker_limits_clipboard_v121.js")
    presentation = read("app/admin_worker_presentation_v66.js")

    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v3"' in columns
    assert 'const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2"' in columns
    assert 'data-chat2api-canonical-worker-row="1"' in columns
    assert 'DEFAULT_ORDER.every(key => Boolean(keyedChild(tr, key)))' in columns
    assert 'table.style.visibility = "hidden"' in columns
    assert 'table.style.visibility = ""' in columns
    assert 'document.documentElement.dataset.chat2apiWorkerListSingleRenderer = "1"' in columns

    assert 'jsonRequest("/api/admin/extensions")' not in behavior
    assert 'installWorkerHooks' not in behavior
    assert 'renderer: "canonical-worker-list-v152"' in behavior
    assert 'retired_renderer: true' in presentation
    assert 'callApi("/api/admin/extensions")' not in presentation
    assert 'setTimeout(() => refresh(true)' not in presentation


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
    assert 'const REMOVED_KEYS = new Set(["concurrency", "reserve_windows", "bound_api_keys", "occupied_windows"])' in columns
    assert 'const LEGACY_KEY_MAP = new Map([["platform", "worker_settings"]])' in columns
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
