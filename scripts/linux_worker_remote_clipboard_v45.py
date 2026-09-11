#!/usr/bin/env python3
"""Unicode text bridge for the Linux Worker remote-login session.

The historical remote controller sends physical key events through xdotool. That
is correct for navigation/control keys, but it cannot reliably synthesize CJK,
emoji or other IME text on a minimal Xvfb keyboard map. This overlay keeps mouse
and special-key control on X11 while sending committed text through Chrome
DevTools ``Input.insertText``. The reverse path reads only the user's current
page selection (or selected text in the focused input/textarea) and never stores
clipboard text on disk.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from websockets.sync.client import connect as websocket_connect

import linux_worker_remote_login as remote


MAX_CLIPBOARD_CHARS = 16_384
_ACTIVE_TARGET_ID = ""


def _safe_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) > MAX_CLIPBOARD_CHARS:
        raise ValueError("clipboard_text_too_long")
    return text


def _targets() -> list[dict[str, Any]]:
    try:
        with urlopen(f"{remote.CHROME_DEBUG_URL}/json/list", timeout=4) as response:
            payload = json.loads(response.read(512_000).decode("utf-8"))
    except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("type") == "page"]


def _target() -> dict[str, Any] | None:
    global _ACTIVE_TARGET_ID
    rows = _targets()
    if _ACTIVE_TARGET_ID:
        current = next((item for item in rows if str(item.get("id") or "") == _ACTIVE_TARGET_ID), None)
        if current and str(current.get("webSocketDebuggerUrl") or "").startswith(("ws://127.0.0.1:", "ws://localhost:")):
            return current

    def score(item: dict[str, Any]) -> tuple[int, int]:
        url = str(item.get("url") or "").lower()
        title = str(item.get("title") or "").lower()
        points = 0
        if "chatgpt.com" in url:
            points += 100
        if "openai.com" in url:
            points += 80
        if "/auth" in url or "login" in url or "sign in" in title:
            points += 35
        if url and url != "about:blank":
            points += 10
        return points, len(url)

    usable = [
        item for item in rows
        if str(item.get("webSocketDebuggerUrl") or "").startswith(("ws://127.0.0.1:", "ws://localhost:"))
        and not str(item.get("url") or "").startswith(("chrome-extension://", "devtools://"))
    ]
    if not usable:
        return None
    chosen = max(usable, key=score)
    _ACTIVE_TARGET_ID = str(chosen.get("id") or "")
    return chosen


def _command(method: str, params: dict[str, Any]) -> dict[str, Any]:
    target = _target()
    debugger_url = str(target.get("webSocketDebuggerUrl") or "") if target else ""
    if not debugger_url:
        return {"ok": False, "error": "login_cdp_target_unavailable"}
    command_id = int(time.time() * 1000) % 1_000_000_000
    try:
        with websocket_connect(debugger_url, open_timeout=4, close_timeout=1) as socket:
            socket.send(json.dumps({"id": command_id, "method": method, "params": params}, separators=(",", ":"), ensure_ascii=False))
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                raw = socket.recv(timeout=max(0.05, deadline - time.monotonic()))
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("id") != command_id:
                    continue
                if message.get("error"):
                    return {"ok": False, "error": "login_cdp_command_failed"}
                result = message.get("result") if isinstance(message.get("result"), dict) else {}
                return {"ok": True, "result": result}
    except Exception:
        return {"ok": False, "error": "login_cdp_command_failed"}
    return {"ok": False, "error": "login_cdp_command_timeout"}


def _paste_text(value: Any) -> dict[str, Any]:
    error = remote._check_session()
    if error:
        return error
    try:
        text = _safe_text(value)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not text:
        return {"ok": True, "characters": 0}
    result = _command("Input.insertText", {"text": text})
    if not result.get("ok"):
        return result
    return {"ok": True, "characters": len(text), "transport": "cdp-input-insert-text"}


def _copy_selection() -> dict[str, Any]:
    error = remote._check_session()
    if error:
        return error
    expression = r"""(() => {
      const el = document.activeElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') &&
          Number.isInteger(el.selectionStart) && Number.isInteger(el.selectionEnd)) {
        return String(el.value || '').slice(el.selectionStart, el.selectionEnd);
      }
      const selection = window.getSelection ? window.getSelection() : null;
      return selection ? String(selection.toString() || '') : '';
    })()"""
    response = _command("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": False,
        "userGesture": True,
    })
    if not response.get("ok"):
        return response
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    remote_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    text = str(remote_result.get("value") or "")
    if len(text) > MAX_CLIPBOARD_CHARS:
        text = text[:MAX_CLIPBOARD_CHARS]
    return {"ok": True, "text": text, "characters": len(text), "transport": "cdp-runtime-selection"}


def open_session() -> dict[str, Any]:
    global _ACTIVE_TARGET_ID
    result = remote.open_session()
    if result.get("ok"):
        target = _target()
        _ACTIVE_TARGET_ID = str(target.get("id") or "") if target else ""
        result = {**result, "unicode_text_bridge": True}
    return result


def close_session() -> dict[str, Any]:
    global _ACTIVE_TARGET_ID
    _ACTIVE_TARGET_ID = ""
    return remote.close_session()


def send_input(arguments: dict[str, Any]) -> dict[str, Any]:
    kind = str(arguments.get("kind") or "")
    if kind == "clipboard":
        action = str(arguments.get("action") or "")
        if action == "paste":
            return _paste_text(arguments.get("text"))
        if action == "copy_selection":
            return _copy_selection()
        return {"ok": False, "error": "unsupported_clipboard_action"}

    if kind == "mouse" and str(arguments.get("action") or "") in {"down", "move", "up"}:
        error = remote._check_session()
        if error:
            return error
        try:
            x = max(0, min(remote.SOURCE_WIDTH - 1, int(arguments.get("x"))))
            y = max(0, min(remote.SOURCE_HEIGHT - 1, int(arguments.get("y"))))
            button = int(arguments.get("button") or 1)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_mouse_coordinates"}
        if button not in {1, 2, 3}:
            return {"ok": False, "error": "invalid_mouse_button"}
        action = str(arguments.get("action") or "")
        if action == "move":
            return remote._run_xdotool(["mousemove", str(x), str(y)])
        verb = "mousedown" if action == "down" else "mouseup"
        return remote._run_xdotool(["mousemove", str(x), str(y), verb, str(button)])

    return remote.send_input(arguments)
