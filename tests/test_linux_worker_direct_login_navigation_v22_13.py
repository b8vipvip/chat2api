from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "linux_worker_remote_login.py"


def load_helper():
    name = "linux_worker_remote_login_v22_13_test"
    spec = importlib.util.spec_from_file_location(name, HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_login_navigation_prefers_loopback_cdp_without_keyboard_simulation(monkeypatch):
    helper = load_helper()
    seen = []

    def fake_cdp(url, *, error_name):
        seen.append((url, error_name))
        return {"ok": True, "method": "cdp", "target_id": "target-1", "target_url": url}

    monkeypatch.setattr(helper, "_open_url_via_cdp", fake_cdp)
    monkeypatch.setattr(
        helper,
        "_open_url_via_existing_chrome",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    result = helper._navigate_login_page()

    assert result["ok"] is True
    assert result["method"] == "cdp"
    assert seen == [("https://chatgpt.com/auth/login", "login_navigation_failed")]


def test_login_navigation_falls_back_to_existing_chrome_singleton(monkeypatch):
    helper = load_helper()
    seen = []

    monkeypatch.setattr(
        helper,
        "_open_url_via_cdp",
        lambda *_args, **_kwargs: {"ok": False, "error": "login_navigation_failed", "detail": "cdp unavailable"},
    )
    monkeypatch.setattr(helper, "time", SimpleNamespace(sleep=lambda *_args: None))
    monkeypatch.setattr(helper, "_chrome_window_id", lambda: "123")

    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    result = helper._navigate_login_page()

    assert result["ok"] is True
    assert result["method"] == "process-singleton"
    assert len(seen) == 1
    args = seen[0][0]
    assert args == [
        helper.CHROME_BINARY,
        f"--user-data-dir={helper.CHROME_PROFILE_DIR}",
        "--new-tab",
        "https://chatgpt.com/auth/login",
    ]
    assert "xdotool" not in args


def test_binding_xdotool_script_uses_separate_command_lines(monkeypatch):
    helper = load_helper()
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    binding_url = "about:blank#chat2api-worker-bind=wbind_example&chat2api-server=https%3A%2F%2Fexample.test"
    result = helper._run_xdotool_stdin_commands(
        [
            ["key", "--clearmodifiers", "ctrl+l"],
            ["type", "--clearmodifiers", "--delay", "0", binding_url],
            ["key", "--clearmodifiers", "Return"],
        ],
        require_session=False,
    )

    assert result == {"ok": True, "error": None}
    assert captured["args"] == ["xdotool", "-"]
    lines = captured["input"].splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("key ")
    assert lines[1].startswith("type ")
    assert binding_url in lines[1]
    assert lines[2].startswith("key ")
    assert "Return" not in lines[1]


def test_binding_is_deferred_while_remote_login_session_is_active(monkeypatch):
    helper = load_helper()
    helper.SESSION.touch()
    monkeypatch.setattr(helper, "_chrome_window_id", lambda: (_ for _ in ()).throw(AssertionError("must not touch Chrome")))

    result = helper.inject_worker_binding("wbind_example", "https://chat2api.example")

    assert result == {"ok": False, "error": "binding_deferred_login_session"}
    helper.SESSION.close()
