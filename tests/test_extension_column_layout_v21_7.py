from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_column_layout_supports_visibility_order_and_persistence():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'const VERSION = "0.21.12"' in source
    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v1"' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert 'data-column-visible' in source
    assert 'data-column-move' in source
    assert 'title="前移一位"' in source
    assert 'title="后移一位"' in source
    assert "resetLayout" in source
    assert 'button.textContent = "⚙"' in source
    assert 'button.title = "设置扩展列表显示列和排序"' in source
    assert "fragment.appendChild(node)" in source
    assert "parent.appendChild(fragment)" in source
    assert 'prefs.visible[key] === false ? "none" : ""' in source


def test_column_layout_covers_real_v20_base_columns_and_nonduplicate_status_columns():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert (
        'const BASE_KEYS = ["client_id", "device_id", "version", "account_type", '
        '"status", "concurrency", "last_seen", "actions"]'
    ) in source
    for key in (
        "client_id",
        "device_id",
        "version",
        "account_type",
        "status",
        "concurrency",
        "last_seen",
        "actions",
        "platform",
        "network",
        "chatgpt",
    ):
        assert f'key: "{key}"' in source
    for label in (
        "扩展 ID",
        "设备标识",
        "版本",
        "账户类型",
        "状态",
        "API 调用数（实时并发）",
        "最后在线",
        "操作",
        "平台",
        "网络",
        "ChatGPT",
    ):
        assert label in source

    assert 'key: "health"' not in source
    assert 'label: "运行健康"' not in source
    assert '["运行健康", "health"]' not in source

    historical = (ROOT / "app" / "admin_v20.js").read_text(encoding="utf-8")
    assert "<th>账户类型</th>" in historical
    assert "${accountPill(row)}" in historical
    assert 'colspan="8"' in historical


def test_existing_extension_pollers_are_column_key_aware_with_correct_fallbacks():
    live = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")
    health = (ROOT / "app" / "admin_v21_6.js").read_text(encoding="utf-8")

    assert 'columnCell(tr, "client_id", 0)' in live
    assert 'columnCell(tr, "concurrency", 5)' in live
    assert 'columnCell(tr, "concurrency", 4)' not in live
    assert 'data-chat2api-column-key' in live

    assert 'columnCell(tr, "client_id", 0)' in health
    assert 'columnCell(tr, "version", 2)' in health
    assert 'th.dataset.chat2apiColumnKey = key' in health
    assert 'cell.dataset.chat2apiColumnKey = key' in health


def test_column_layout_is_event_driven_and_idempotent_instead_of_periodic_dom_churn():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert "MutationObserver" in source
    assert "reorderKnownChildren" in source
    assert "current.every((node, index) => node === desired[index])" in source
    assert "setInterval(" not in source
    assert "APPLY_MS" not in source
    assert "scheduleRefresh" in source


def test_column_settings_uses_centered_modal_grid_instead_of_dropdown_positioning():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")

    for token in (
        'backdrop.id = "extensionColumnSettingsBackdrop"',
        'menu.id = "extensionColumnSettingsMenu"',
        'menu.setAttribute("role", "dialog")',
        'menu.setAttribute("aria-modal", "true")',
        '"align-items:center"',
        '"justify-content:center"',
        '"position:fixed"',
        '"inset:0"',
        'grid-template-columns:repeat(auto-fit,minmax(240px,1fr))',
        'id="extensionColumnSettingsBody"',
        'overflow:auto',
        'data-close-extension-columns',
        'if (event.target === backdrop) closeMenu()',
        'event.key === "Escape"',
        'document.body.style.overflow = "hidden"',
        'document.body.style.overflow = bodyOverflowBeforeModal',
    ):
        assert token in source

    assert "positionMenu" not in source
    assert 'window.addEventListener("scroll"' not in source
    assert 'window.addEventListener("resize"' not in source
    assert 'menu.style.left' not in source
    assert 'menu.style.top' not in source


def test_column_settings_modal_uses_numbered_cards_and_compact_order_controls():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'data-column-card="${key}"' in source
    assert 'String(index + 1).padStart(2, "0")' in source
    assert "← 前移" in source
    assert "后移 →" in source
    assert "宽屏自动多列排列" in source
    assert "已显示 ${visibleCount} / ${prefs.order.length} 列" in source


def test_column_layout_migrates_legacy_preferences_without_losing_account_type():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'candidate.includes("account_type")' in source
    assert 'candidate.indexOf("version")' in source
    assert 'candidate.splice(versionIndex + 1, 0, "account_type")' in source
    assert "KNOWN_KEYS.has(key)" in source


def test_column_layout_does_not_add_server_calls_or_privileged_browser_automation():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'api("' not in source
    assert "fetch(" not in source
    assert "chrome." not in source
    assert "CAPTCHA" not in source
    assert "password" not in source.lower()


def test_column_layout_javascript_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_extension_columns.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
