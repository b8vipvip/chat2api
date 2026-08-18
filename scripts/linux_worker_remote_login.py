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
import time
from dataclasses import dataclass
from typing import Any


DISPLAY = os.environ.get("CHAT2API_LOGIN_DISPLAY", ":99")
SOURCE_WIDTH = int(os.environ.get("CHAT2API_LOGIN_SOURCE_WIDTH", "1920"))
SOURCE_HEIGHT = int(os.environ.get("CHAT2API_LOGIN_SOURCE_HEIGHT", "1080"))
FRAME_WIDTH = int(os.environ.get("CHAT2API_LOGIN_FRAME_WIDTH", "1280"))
FRAME_HEIGHT = int(os.environ.get("CHAT2API_LOGIN_FRAME_HEIGHT", "720"))
SESSION_IDLE_SECONDS = int(os.environ.get("CHAT2API_LOGIN_SESSION_IDLE_SECONDS", "1200"))
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


def _env() -> dict[str, str]:
    return {**os.environ, "DISPLAY": DISPLAY}


def _check_session() -> dict[str, Any] | None:
    if SESSION.expired():
        SESSION.close()
    if not SESSION.active:
        return {"ok": False, "error": "login_session_not_active"}
    SESSION.touch()
    return None


def open_session() -> dict[str, Any]:
    SESSION.touch()
    return {"ok": True, "idle_timeout_seconds": SESSION_IDLE_SECONDS, "source_width": SOURCE_WIDTH, "source_height": SOURCE_HEIGHT}


def close_session() -> dict[str, Any]:
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


def _run_xdotool_script(parts: list[str]) -> dict[str, Any]:
    """Send keyboard commands over stdin so typed secrets never enter argv."""
    error = _check_session()
    if error:
        return error
    script = " ".join(shlex.quote(str(part)) for part in parts) + "\n"
    try:
        result = subprocess.run(["xdotool", "-"], input=script, capture_output=True, text=True, timeout=5, check=False, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "input_injection_failed"}
    return {"ok": result.returncode == 0, "error": None if result.returncode == 0 else "input_injection_failed"}


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
            return _run_xdotool_script(["key", "--clearmodifiers", combo])
        if len(key) == 1 and key.isprintable():
            chord_modifiers = [item for item in allowed_modifiers if item in {"ctrl", "alt", "super"}]
            if chord_modifiers:
                combo = "+".join([*chord_modifiers, key])
                return _run_xdotool_script(["key", "--clearmodifiers", combo])
            return _run_xdotool_script(["type", "--clearmodifiers", "--delay", "0", key])
        return {"ok": False, "error": "unsupported_key"}

    return {"ok": False, "error": "unsupported_input_kind"}
