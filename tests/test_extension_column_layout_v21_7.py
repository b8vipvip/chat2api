from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def source() -> str:
    return (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")


def test_column_layout_supports_visibility_order_and_v2_to_v3_persistence():
    text = source()
    assert 'const VERSION = "0.22.41-worker-list-v59"' in text
    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v3"' in text
    assert 'const LEGACY_STORAGE_KEY = "chat2api.extensionColumns.v2"' in text
    assert "localStorage.getItem(STORAGE_KEY)" in text
    assert "localStorage.getItem(LEGACY_STORAGE_KEY)" in text
    assert "localStorage.setItem(STORAGE_KEY" in text
    assert 'data-column-visible' in text
    assert 'data-column-move' in text
    assert "← 前移" in text
    assert "后移 →" in text
    assert 'button.textContent = "⚙"' in text
    assert 'button.title = "Worker列表列设置"' in text
    assert "fragment.appendChild(node)" in text
    assert "parent.appendChild(fragment)" in text
    assert 'activePrefs.visible[key] === false ? "none" : ""' in text


def test_column_layout_uses_one_canonical_semantic_schema():
    text = source()
    expected = (
        ("client_id", "Worker ID"),
        ("device_id", "设备标识"),
        ("version", "版本"),
        ("account_type", "账户类型"),
        ("status", "状态"),
        ("bound_api_keys", "绑定 API Key 数"),
        ("worker_settings", "并发设置"),
        ("last_seen", "最后在线"),
        ("network", "网络"),
        ("chatgpt", "ChatGPT"),
        ("actions", "操作"),
    )
    for key, label in expected:
        assert f'{{key: "{key}", label: "{label}"}}' in text
        assert f'data-chat2api-column-key="{key}"' in text

    # The legacy presentation-only columns must not survive as active COLUMNS.
    assert '{key: "concurrency",' not in text
    assert '{key: "reserve_windows",' not in text
    assert '{key: "platform",' not in text
    assert 'removed_columns: ["concurrency", "reserve_windows", "platform"]' in text
    assert '旧并发列（已合并）' not in text
    assert '旧备用窗口列（已合并）' not in text


def test_column_layout_migrates_the_historical_miskeyed_cells():
    text = source()
    assert '["platform", "worker_settings"]' in text
    assert '["concurrency", "bound_api_keys"]' in text
    assert 'const REMOVED_KEYS = new Set(["reserve_windows"])' in text
    assert 'const key = LEGACY_KEY_MAP.get(original) || original' in text
    assert 'if (REMOVED_KEYS.has(original)) continue' in text
    assert 'if (!KNOWN_KEYS.has(key) || seen.has(key)) continue' in text


def test_canonical_header_and_rows_have_the_same_column_keys():
    text = source()
    assert "function canonicalHeaderHtml()" in text
    assert "function rowHtml(row)" in text
    assert 'data-chat2api-canonical-worker-row="1"' in text
    assert 'DEFAULT_ORDER.every(key => Boolean(keyedChild(tr, key)))' in text
    assert 'headerRow.innerHTML !== header' in text
    assert 'body.innerHTML = rows.length' in text
    assert 'applyLayout();' in text


def test_worker_view_bypasses_historical_multi_stage_extension_renderers():
    text = source()
    assert "function installCanonicalShowOwner()" in text
    assert 'if (viewName !== "extensions") return baseShow(viewName)' in text
    assert "activateExtensionView();" in text
    assert "return loadCanonicalExtensions(true);" in text
    assert "legacy_renderers_bypassed: true" in text

    # Late promises from historical wrappers can still finish after the final
    # owner is installed, so v59 repairs any non-canonical replacement before
    # the next paint instead of allowing a visible second table style.
    assert "function queueCanonicalRepair()" in text
    assert "queueMicrotask(() =>" in text
    assert "!isCanonical()" in text
    assert 'new MutationObserver(queueCanonicalRepair).observe(body, {childList: true})' in text
    assert 'new MutationObserver(queueCanonicalRepair).observe(headerRow, {childList: true})' in text


def test_initial_worker_table_is_hidden_until_canonical_snapshot_is_ready():
    text = source()
    assert 'table.style.visibility = "hidden"' in text
    assert 'table.style.visibility = ""' in text
    assert 'document.documentElement.dataset.chat2apiWorkerListReady = "1"' in text
    assert 'loadCanonicalExtensions(true)' in text


def test_column_settings_modal_only_exposes_current_columns():
    text = source()
    for token in (
        'backdrop.id = "extensionColumnSettingsBackdrop"',
        'menu.id = "extensionColumnSettingsMenu"',
        'position:fixed',
        'inset:0',
        'align-items:center',
        'justify-content:center',
        'grid-template-columns:repeat(auto-fit,minmax(250px,1fr))',
        'overflow:auto',
        'data-close-columns',
        'if (event.target === backdrop) closeMenu()',
        'event.key === "Escape"',
        'document.body.style.overflow = "hidden"',
        'document.body.style.overflow = bodyOverflowBeforeModal',
        '旧并发列与旧备用窗口列已永久移除',
    ):
        assert token in text

    assert "positionMenu" not in text
    assert 'window.addEventListener("scroll"' not in text
    assert 'window.addEventListener("resize"' not in text
    assert 'menu.style.left' not in text
    assert 'menu.style.top' not in text


def test_column_settings_modal_uses_numbered_cards_and_compact_order_controls():
    text = source()
    assert 'String(index + 1).padStart(2, "0")' in text
    assert "← 前移" in text
    assert "后移 →" in text
    assert "已显示 ${visibleCount} / ${active.order.length} 列" in text


def test_canonical_worker_list_uses_only_admin_apis_not_browser_privileges():
    text = source()
    assert 'api("/api/admin/extensions")' in text
    assert 'api("/api/admin/pairing-codes"' in text
    assert "chrome." not in text
    assert "CAPTCHA" not in text
    assert "password" not in text.lower()


def test_column_layout_javascript_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_extension_columns.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
