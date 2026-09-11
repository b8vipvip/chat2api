from __future__ import annotations

import json
from pathlib import Path

from app.worker_limits_clipboard_v121_patch import (
    _load_window_limits,
    _normalize_limit,
    _save_window_limits,
)


ROOT = Path(__file__).resolve().parents[1]


def test_window_limit_config_round_trip_and_bounds(tmp_path: Path) -> None:
    path = tmp_path / "worker_window_limits.json"
    _save_window_limits(path, {"ext_b": 7, "ext_a": 2})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["mode"] == "per-extension-routed-window-limit"
    assert payload["clients"] == {"ext_a": 2, "ext_b": 7}
    assert _load_window_limits(path) == {"ext_a": 2, "ext_b": 7}
    assert _normalize_limit(0) == 1
    assert _normalize_limit(99) == 32


def test_window_limit_guard_preserves_single_creation_and_lifecycle_authority() -> None:
    entry = (ROOT / "chrome_extension/background_entry.js").read_text(encoding="utf-8")
    router = (ROOT / "chrome_extension/conversation_routing.js").read_text(encoding="utf-8")
    guard = (ROOT / "chrome_extension/background_window_limit_v121.js").read_text(encoding="utf-8")
    control = (ROOT / "chrome_extension/background_capacity_control_v35.js").read_text(encoding="utf-8")
    server = (ROOT / "app/worker_limits_clipboard_v121_patch.py").read_text(encoding="utf-8")

    assert entry.index('"conversation_routing.js"') < entry.index('"background_window_limit_v121.js"') < entry.index('"conversation_dispatch.js"')
    assert "chrome.windows.create" not in guard
    assert "chrome.windows.remove" not in guard
    assert "route.generation =" not in guard
    assert 'typeof value.retireRoute !== "function"' in guard
    assert 'value.retireRoute(entry.key, route, "", reason)' in guard
    assert "state.retireRoute = retireRoute" in router
    assert "route.inflight_request_id" in guard
    assert "worker_window_limit_exhausted" in guard
    assert "worker-window-limit-admission" in guard
    assert 'action === "windows.limit"' in control
    assert 'window_decision_authority: "conversation-routing-v30"' in control
    assert 'routing["worker_window_limit"] = window_limit_for(client_id)' in server
    assert 'return max(concurrency, _normalize_limit(configured, concurrency))' in server
    assert "最大窗口数不能小于并发上限" in server
    assert "on-demand-hard-cap-no-warm-pool" in server


def test_remote_login_unicode_clipboard_is_ticket_scoped_and_bidirectional() -> None:
    helper = (ROOT / "scripts/linux_worker_remote_clipboard_v45.py").read_text(encoding="utf-8")
    agent = (ROOT / "scripts/linux_worker_agent_v44.py").read_text(encoding="utf-8")
    server = (ROOT / "app/worker_limits_clipboard_v121_patch.py").read_text(encoding="utf-8")
    console = (ROOT / "app/admin_worker_limits_clipboard_v121.js").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'Input.insertText' in helper
    assert 'Runtime.evaluate' in helper
    assert '"down", "move", "up"' in helper
    assert "xclip" not in helper
    assert 'import linux_worker_remote_clipboard_v45 as remote_clipboard' in agent
    assert 'AGENT_VERSION = "0.3.7"' in agent
    assert "scripts/linux_worker_remote_clipboard_v45.py" in dockerfile

    assert 'LOGIN_TICKET_HEADER = "x-chat2api-login-ticket"' in server
    assert 'login_sessions.require(worker_id, ticket, touch=True)' in server
    assert '"kind": "clipboard", "action": "paste"' in server
    assert '"kind": "clipboard", "action": "copy_selection"' in server
    assert "MAX_CLIPBOARD_CHARS = 16_384" in server

    assert "粘贴到远程（中文）" in console
    assert "复制远程选中文本" in console
    assert 'clipboardCommand("paste"' in console
    assert 'clipboardCommand("copy_selection")' in console
    assert 'sendMouse("down"' in console
    assert 'sendMouse("move"' in console
    assert 'sendMouse("up"' in console
    assert 'event.stopImmediatePropagation()' in console


def test_worker_settings_ui_keeps_concurrency_and_windows_distinct() -> None:
    console = (ROOT / "app/admin_worker_limits_clipboard_v121.js").read_text(encoding="utf-8")
    presentation = (ROOT / "app/worker_presentation_v64_patch.py").read_text(encoding="utf-8")

    assert 'headerCell.textContent = "并发 / 窗口"' in console
    assert "窗口数不能小于并发数" in console
    assert '/concurrency`' in console
    assert '/windows/limit`' in console
    assert 'install_worker_limits_clipboard_v121_patch(app)' in presentation
