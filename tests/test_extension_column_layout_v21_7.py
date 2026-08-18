from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_column_layout_supports_visibility_order_and_persistence():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    assert 'const VERSION = "0.21.7"' in source
    assert 'const STORAGE_KEY = "chat2api.extensionColumns.v1"' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert 'data-column-visible' in source
    assert 'data-column-move' in source
    assert 'title="前移"' in source
    assert 'title="后移"' in source
    assert "resetLayout" in source
    assert 'button.textContent = "⚙"' in source
    assert 'button.title = "设置扩展列表显示列和排序"' in source
    assert "headerRow.appendChild(header)" in source
    assert "tr.appendChild(cell)" in source
    assert 'prefs.visible[key] === false ? "none" : ""' in source


def test_column_layout_covers_all_current_extension_columns():
    source = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    for key in (
        "client_id",
        "device_id",
        "version",
        "status",
        "concurrency",
        "last_seen",
        "actions",
        "platform",
        "network",
        "chatgpt",
        "health",
    ):
        assert f'key: "{key}"' in source
    for label in (
        "扩展 ID",
        "设备标识",
        "版本",
        "状态",
        "API 调用数（实时并发）",
        "最后在线",
        "操作",
        "平台",
        "网络",
        "ChatGPT",
        "运行健康",
    ):
        assert label in source


def test_existing_extension_pollers_are_column_key_aware():
    live = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")
    health = (ROOT / "app" / "admin_v21_6.js").read_text(encoding="utf-8")

    assert 'columnCell(tr, "client_id", 0)' in live
    assert 'columnCell(tr, "concurrency", 4)' in live
    assert 'data-chat2api-column-key' in live

    assert 'columnCell(tr, "client_id", 0)' in health
    assert 'columnCell(tr, "version", 2)' in health
    assert 'th.dataset.chat2apiColumnKey = key' in health
    assert 'cell.dataset.chat2apiColumnKey = key' in health


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
