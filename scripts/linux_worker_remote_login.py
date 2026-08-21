#!/usr/bin/env python3
"""Xvfb-only remote login helper for the Linux Worker Agent.

No listener is opened. Frames and input are only exposed through the already
authenticated outbound Worker websocket control plane.
"""
from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from websockets.sync.client import connect as websocket_connect


DISPLAY = os.environ.get("CHAT2API_LOGIN_DISPLAY", ":99")
SOURCE_WIDTH = int(os.environ.get("CHAT2API_LOGIN_SOURCE_WIDTH", "1920"))
SOURCE_HEIGHT = int(os.environ.get("CHAT2API_LOGIN_SOURCE_HEIGHT", "1080"))
FRAME_WIDTH = int(os.environ.get("CHAT2API_LOGIN_FRAME_WIDTH", "1280"))
FRAME_HEIGHT = int(os.environ.get("CHAT2API_LOGIN_FRAME_HEIGHT", "720"))
SESSION_IDLE_SECONDS = int(os.environ.get("CHAT2API_LOGIN_SESSION_IDLE_SECONDS", "1200"))
LOGIN_TMPDIR = os.environ.get("CHAT2API_LOGIN_TMPDIR", "/dev/shm")
LOGIN_URL = os.environ.get("CHAT2API_LOGIN_URL", "https://chatgpt.com/auth/login")
CHROME_BINARY = os.environ.get("CHAT2API_LOGIN_CHROME_BINARY", "/home/chat2api/.cache/chat2api-chrome-for-testing/chrome")
CHROME_PROFILE_DIR = os.environ.get("CHAT2API_LOGIN_CHROME_PROFILE", "/home/chat2api/.config/chat2api-chrome-worker-01")
CHROME_HOME = os.environ.get("CHAT2API_LOGIN_CHROME_HOME", "/home/chat2api")
CHROME_DEBUG_URL = os.environ.get("CHAT2API_LOGIN_CHROME_DEBUG_URL", "http://127.0.0.1:9222").rstrip("/")
MAX_FRAME_BYTES = 1_500_000


@dataclass
class RemoteLoginSession:
    active: bool = False
    deadline: float = 0.0

    def expired(self) -> bool:
        return self.active and time.monotonic() >= self.deadline

    def touch(self) -> None:
        self.active = True
        self.deadline = time.monotonic() + SESSION_IDLE_SECONDS

    def close(self) -> None:
        self.active = False
        self.deadline = 0.0


SESSION = RemoteLoginSession()
ACTION_LOCK = threading.RLock()


def _env() -> dict[str, str]:
    env = {
        **os.environ,
        "DISPLAY": DISPLAY,
        "HOME": CHROME_HOME,
        "XDG_CONFIG_HOME": f"{CHROME_HOME}/.config",
        "XDG_CACHE_HOME": f"{CHROME_HOME}/.cache",
    }
    if LOGIN_TMPDIR:
        # The Agent runs with ProtectSystem=strict, which can expose /tmp as
        # read-only inside its mount namespace. ImageMagick and a secondary
        # Chrome launcher both need writable scratch space. /dev/shm stays
        # writable, is memory-backed, and avoids persisting login frames.
        env["TMPDIR"] = LOGIN_TMPDIR
        env["MAGICK_TMPDIR"] = LOGIN_TMPDIR
    return env


def session_active() -> bool:
    with ACTION_LOCK:
        if SESSION.expired():
            SESSION.close()
        return SESSION.active


def _check_session() -> dict[str, Any] | None:
    with ACTION_LOCK:
        if SESSION.expired():
            SESSION.close()
        if not SESSION.active:
            return {"ok": False, "error": "login_session_not_active"}
        SESSION.touch()
    return None


def open_session() -> dict[str, Any]:
    with ACTION_LOCK:
        SESSION.touch()
        navigation = _navigate_login_page()
        if not navigation.get("ok"):
            SESSION.close()
            return {
                "ok": False,
                "error": "login_navigation_failed",
                "navigation_detail": str(navigation.get("detail") or navigation.get("error") or "")[:240],
                "login_url": LOGIN_URL,
            }
    return {
        "ok": True,
        "idle_timeout_seconds": SESSION_IDLE_SECONDS,
        "source_width": SOURCE_WIDTH,
        "source_height": SOURCE_HEIGHT,
        "login_url": LOGIN_URL,
        "navigation_method": str(navigation.get("method") or "direct"),
    }


def close_session() -> dict[str, Any]:
    with ACTION_LOCK:
        SESSION.close()
    return {"ok": True}


def capture_frame() -> dict[str, Any]:
    error = _check_session()
    if error:
        return error
    try:
        xwd = subprocess.Popen(["xwd", "-display", DISPLAY, "-root", "-silent"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_env())
        convert = subprocess.run(["convert", "xwd:-", "-resize", f"{FRAME_WIDTH}x{FRAME_HEIGHT}", "-strip", "-quality", "62", "jpg:-"], stdin=xwd.stdout, capture_output=True, timeout=8, check=False, env=_env())
        if xwd.stdout:
            xwd.stdout.close()
        xwd.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "frame_capture_failed"}
    payload = convert.stdout or b""
    if convert.returncode != 0 or not payload or len(payload) > MAX_FRAME_BYTES:
        return {"ok": False, "error": "frame_capture_failed"}
    return {"ok": True, "mime": "image/jpeg", "frame": base64.b64encode(payload).decode("ascii"), "source_width": SOURCE_WIDTH, "source_height": SOURCE_HEIGHT, "frame_width": FRAME_WIDTH, "frame_height": FRAME_HEIGHT}


def _run_xdotool(args: list[str]) -> dict[str, Any]:
    error = _check_session()
    if error:
        return error
    try:
        result = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=5, check=False, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "input_injection_failed"}
    return {"ok": result.returncode == 0, "error": None if result.returncode == 0 else "input_injection_failed"}


def _run_xdotool_stdin_commands(commands: list[list[str]], *, require_session: bool = True, error_name: str = "input_injection_failed") -> dict[str, Any]:
    """Send one xdotool command per stdin line so secrets never enter argv.

    `xdotool -` treats a line as one command. Joining several commands onto one
    line makes `type` consume later command tokens as literal text, which was
    the source of the address-bar corruption seen in the remote login frame.
    """
    if require_session:
        error = _check_session()
        if error:
            return error
    script = "".join(" ".join(shlex.quote(str(part)) for part in command) + "\n" for command in commands)
    try:
        result = subprocess.run(["xdotool", "-"], input=script, capture_output=True, text=True, timeout=8, check=False, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": error_name}
    return {"ok": result.returncode == 0, "error": None if result.returncode == 0 else error_name}


def _run_xdotool_stdin(parts: list[str], *, require_session: bool = True, error_name: str = "input_injection_failed") -> dict[str, Any]:
    return _run_xdotool_stdin_commands([parts], require_session=require_session, error_name=error_name)


def _chrome_window_id() -> str | None:
    """Return one visible Chrome/CfT X11 window. Window IDs are not sensitive."""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", ".*[Cc]hrome.*"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    rows = [line.strip() for line in (result.stdout or "").splitlines() if line.strip().isdigit()]
    return rows[-1] if result.returncode == 0 and rows else None


def _focus_window(window_id: str, *, error_name: str) -> dict[str, Any]:
    """Focus an X11 window without requiring an EWMH window manager."""
    try:
        result = subprocess.run(
            ["xdotool", "windowfocus", "--sync", window_id],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": error_name}
    return {"ok": result.returncode == 0, "error": None if result.returncode == 0 else error_name}


def _type_url_into_focused_chrome(url: str, *, error_name: str) -> dict[str, Any]:
    """Type a secret/internal URL without putting it in process argv."""
    return _run_xdotool_stdin_commands(
        [
            ["key", "--clearmodifiers", "ctrl+l"],
            ["type", "--clearmodifiers", "--delay", "0", url],
            ["key", "--clearmodifiers", "Return"],
        ],
        require_session=False,
        error_name=error_name,
    )


def _open_url_via_cdp(url: str, *, error_name: str) -> dict[str, Any]:
    """Open a fixed public URL through Chrome's loopback-only DevTools endpoint."""
    endpoint = f"{CHROME_DEBUG_URL}/json/new?{quote(url, safe=':/?&=%')}"
    try:
        request = Request(endpoint, data=b"", method="PUT")
        with urlopen(request, timeout=4) as response:
            payload = json.loads(response.read(65536).decode("utf-8"))
    except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": error_name, "detail": f"cdp_unavailable:{type(exc).__name__}"}
    target_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
    target_url = str(payload.get("url") or "") if isinstance(payload, dict) else ""
    if not target_id:
        return {"ok": False, "error": error_name, "detail": "cdp_target_missing"}
    return {"ok": True, "method": "cdp", "target_id": target_id, "target_url": target_url}


def _navigate_secret_url_via_cdp(url: str, *, error_name: str) -> dict[str, Any]:
    """Navigate a fresh blank target over loopback CDP without putting `url` in HTTP or argv."""
    endpoint = f"{CHROME_DEBUG_URL}/json/new?about:blank"
    try:
        request = Request(endpoint, data=b"", method="PUT")
        with urlopen(request, timeout=4) as response:
            payload = json.loads(response.read(65536).decode("utf-8"))
    except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": error_name, "detail": f"cdp_unavailable:{type(exc).__name__}"}

    target_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
    debugger_url = str(payload.get("webSocketDebuggerUrl") or "") if isinstance(payload, dict) else ""
    if not target_id or not debugger_url:
        return {"ok": False, "error": error_name, "detail": "cdp_target_missing"}
    if not debugger_url.startswith(("ws://127.0.0.1:", "ws://localhost:")):
        return {"ok": False, "error": error_name, "detail": "cdp_debugger_not_loopback"}

    command_id = 1
    try:
        with websocket_connect(debugger_url, open_timeout=4, close_timeout=1) as socket:
            socket.send(json.dumps({
                "id": command_id,
                "method": "Page.navigate",
                "params": {"url": url},
            }, separators=(",", ":")))
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                raw = socket.recv(timeout=max(0.05, deadline - time.monotonic()))
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("id") != command_id:
                    continue
                if message.get("error"):
                    return {"ok": False, "error": error_name, "detail": "cdp_page_error"}
                result = message.get("result") if isinstance(message.get("result"), dict) else {}
                if result.get("errorText"):
                    return {"ok": False, "error": error_name, "detail": "cdp_navigation_error"}
                return {"ok": True, "method": "cdp-page-navigate", "target_id": target_id}
    except Exception as exc:
        # Do not include the navigation URL or WebSocket payload in diagnostics;
        # the Worker binding ticket is intentionally short-lived and secret.
        return {"ok": False, "error": error_name, "detail": f"cdp_socket:{type(exc).__name__}"}
    return {"ok": False, "error": error_name, "detail": "cdp_navigation_timeout"}


def _open_url_via_existing_chrome(url: str, *, error_name: str) -> dict[str, Any]:
    """Ask the already-running profile instance to open a URL directly.

    This is the compatibility fallback for older upgraded launchers. It still
    uses Chrome's ProcessSingleton command path and never simulates typing or
    reads the operator clipboard.
    """
    if not _chrome_window_id():
        return {"ok": False, "error": "chrome_window_not_found"}
    try:
        result = subprocess.run(
            [CHROME_BINARY, f"--user-data-dir={CHROME_PROFILE_DIR}", "--new-tab", url],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": error_name}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "process_singleton_failed").strip().replace("\n", " ")[-220:]
        return {"ok": False, "error": error_name, "detail": detail}
    return {"ok": True, "method": "process-singleton"}


def _navigate_login_page() -> dict[str, Any]:
    cdp_error = ""
    for _ in range(6):
        result = _open_url_via_cdp(LOGIN_URL, error_name="login_navigation_failed")
        if result.get("ok"):
            return result
        cdp_error = str(result.get("detail") or result.get("error") or "")
        time.sleep(0.35)
    fallback = _open_url_via_existing_chrome(LOGIN_URL, error_name="login_navigation_failed")
    if fallback.get("ok"):
        return fallback
    fallback_error = str(fallback.get("detail") or fallback.get("error") or "")
    return {
        "ok": False,
        "error": "login_navigation_failed",
        "detail": f"cdp={cdp_error or 'failed'};process={fallback_error or 'failed'}"[:240],
    }


def inject_worker_binding(ticket: str, server_url: str) -> dict[str, Any]:
    """Deliver a short-lived Worker binding proof through an about:blank fragment.

    The proof never enters subprocess argv, a DevTools HTTP request, or ChatGPT
    page JavaScript. A blank CDP target is created first, then Page.navigate is
    sent only over Chrome's loopback-only DevTools WebSocket. The Chrome Bridge
    captures the fragment, scrubs the tab, claims the Worker identity, and then
    restores ChatGPT. Binding remains deferred during a human login session.
    """
    raw_ticket = str(ticket or "").strip()
    clean_server = str(server_url or "").strip().rstrip("/")
    if not raw_ticket.startswith("wbind_") or len(raw_ticket) > 160:
        return {"ok": False, "error": "invalid_binding_ticket"}
    if not clean_server.startswith(("https://", "http://")) or len(clean_server) > 500:
        return {"ok": False, "error": "invalid_binding_server"}
    with ACTION_LOCK:
        if SESSION.expired():
            SESSION.close()
        if SESSION.active:
            return {"ok": False, "error": "binding_deferred_login_session"}
        binding_url = f"about:blank#chat2api-worker-bind={raw_ticket}&chat2api-server={quote(clean_server, safe='')}"
        return _navigate_secret_url_via_cdp(binding_url, error_name="binding_injection_failed")


def send_input(arguments: dict[str, Any]) -> dict[str, Any]:
    kind = str(arguments.get("kind") or "")
    if kind == "mouse":
        try:
            x = max(0, min(SOURCE_WIDTH - 1, int(arguments.get("x"))))
            y = max(0, min(SOURCE_HEIGHT - 1, int(arguments.get("y"))))
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_mouse_coordinates"}
        action = str(arguments.get("action") or "click")
        if action == "move":
            return _run_xdotool(["mousemove", str(x), str(y)])
        if action == "click":
            try:
                button = int(arguments.get("button") or 1)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_mouse_button"}
            if button not in {1, 2, 3}:
                return {"ok": False, "error": "invalid_mouse_button"}
            return _run_xdotool(["mousemove", str(x), str(y), "click", str(button)])
        if action == "double_click":
            return _run_xdotool(["mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "120", "1"])
        if action == "scroll":
            try:
                delta = int(arguments.get("delta") or 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_scroll_delta"}
            if delta == 0:
                return {"ok": True}
            button = "4" if delta < 0 else "5"
            count = max(1, min(8, abs(delta)))
            return _run_xdotool(["mousemove", str(x), str(y), "click", "--repeat", str(count), button])
        return {"ok": False, "error": "unsupported_mouse_action"}

    if kind == "key":
        key = str(arguments.get("key") or "")
        modifiers = arguments.get("modifiers") or []
        allowed_modifiers = [str(item).lower() for item in modifiers if str(item).lower() in {"ctrl", "alt", "shift", "super"}]
        special = {"Enter":"Return","Tab":"Tab","Escape":"Escape","Backspace":"BackSpace","Delete":"Delete","ArrowLeft":"Left","ArrowRight":"Right","ArrowUp":"Up","ArrowDown":"Down","Home":"Home","End":"End","PageUp":"Page_Up","PageDown":"Page_Down"," ":"space"}
        if key in special:
            combo = "+".join([*allowed_modifiers, special[key]])
            return _run_xdotool_stdin(["key", "--clearmodifiers", combo])
        if len(key) == 1 and key.isprintable():
            chord_modifiers = [item for item in allowed_modifiers if item in {"ctrl", "alt", "super"}]
            if chord_modifiers:
                combo = "+".join([*chord_modifiers, key])
                return _run_xdotool_stdin(["key", "--clearmodifiers", combo])
            return _run_xdotool_stdin(["type", "--clearmodifiers", "--delay", "0", key])
        return {"ok": False, "error": "unsupported_key"}

    return {"ok": False, "error": "unsupported_input_kind"}
