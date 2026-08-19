#!/usr/bin/env python3
"""Xvfb-only remote login helper for the Linux Worker Agent.

No listener is opened. Frames and input are only exposed through the already
authenticated outbound Worker websocket control plane.
"""
from __future__ import annotations

import base64
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


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
    # Opening the remote frame is the recovery path if automatic navigation
    # ever fails. Do not make a best-effort fixed-URL action a hard gate for
    # the whole login session.
    return {
        "ok": True,
        "idle_timeout_seconds": SESSION_IDLE_SECONDS,
        "source_width": SOURCE_WIDTH,
        "source_height": SOURCE_HEIGHT,
        "login_url": LOGIN_URL,
        "navigation_warning": None if navigation.get("ok") else str(navigation.get("error") or "login_navigation_failed"),
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


def _open_url_via_existing_chrome(url: str, *, error_name: str) -> dict[str, Any]:
    """Ask the already-running profile instance to open a URL directly.

    The login URL is fixed and non-secret, so there is no reason to simulate
    Ctrl+L/typing/Enter. A second Chrome invocation with the same profile is
    handed to the existing ProcessSingleton and opens the URL as a new tab.
    This avoids keyboard focus, clipboard, and concurrent UI automation races.
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
    return {"ok": result.returncode == 0, "error": None if result.returncode == 0 else error_name}


def _navigate_login_page() -> dict[str, Any]:
    return _open_url_via_existing_chrome(LOGIN_URL, error_name="login_navigation_failed")


def inject_worker_binding(ticket: str, server_url: str) -> dict[str, Any]:
    """Deliver a short-lived Worker binding proof through an about:blank fragment.

    The proof never enters subprocess argv, an HTTP request, or ChatGPT page
    JavaScript. The Chrome Bridge captures it from the tab URL, scrubs the tab,
    claims the Worker identity, and then restores ChatGPT. Binding injection is
    deferred while a human remote-login session is active so it cannot steal
    address-bar focus from the operator.
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
        window_id = _chrome_window_id()
        if not window_id:
            return {"ok": False, "error": "chrome_window_not_found"}
        focused = _focus_window(window_id, error_name="binding_focus_failed")
        if not focused.get("ok"):
            return focused
        binding_url = f"about:blank#chat2api-worker-bind={raw_ticket}&chat2api-server={quote(clean_server, safe='')}"
        return _type_url_into_focused_chrome(binding_url, error_name="binding_injection_failed")


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
