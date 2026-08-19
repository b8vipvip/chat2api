from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_remote_login_navigation_uses_bare_xvfb_safe_focus():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    assert 'LOGIN_URL = os.environ.get("CHAT2API_LOGIN_URL", "https://chatgpt.com/auth/login")' in helper
    assert '["xdotool", "windowfocus", "--sync", window_id]' in helper
    assert '"windowactivate", "--sync", window_id' not in helper
    assert 'navigation = _navigate_login_page()' in helper


def test_navigation_failure_is_warning_not_remote_login_blocker():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    open_session = helper.split("def open_session()", 1)[1].split("def close_session()", 1)[0]
    assert '"navigation_warning"' in open_session
    assert 'SESSION.close()' not in open_session
    assert '"ok": True' in open_session


def test_binding_navigation_keeps_secret_url_off_process_argv():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    binding = helper.split("def inject_worker_binding", 1)[1].split("def send_input", 1)[0]
    assert '_focus_window(window_id, error_name="binding_focus_failed")' in binding
    assert '_type_url_into_focused_chrome(binding_url, error_name="binding_injection_failed")' in binding
    assert 'subprocess.run(["xdotool", "-"]' in helper
