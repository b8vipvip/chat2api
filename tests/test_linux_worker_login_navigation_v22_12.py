from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_remote_login_navigation_prefers_loopback_cdp_with_singleton_fallback():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")
    assert 'LOGIN_URL = os.environ.get("CHAT2API_LOGIN_URL", "https://chatgpt.com/auth/login")' in helper
    assert 'CHROME_DEBUG_URL = os.environ.get("CHAT2API_LOGIN_CHROME_DEBUG_URL", "http://127.0.0.1:9222")' in helper
    assert '[CHROME_BINARY, f"--user-data-dir={CHROME_PROFILE_DIR}", "--new-tab", url]' in helper
    navigation = helper.split("def _navigate_login_page", 1)[1].split("def inject_worker_binding", 1)[0]
    assert "_open_url_via_cdp(LOGIN_URL" in navigation
    assert "_open_url_via_existing_chrome(LOGIN_URL" in navigation
    assert "xdotool" not in navigation
    assert "ctrl+l" not in navigation
    assert "--remote-debugging-address=127.0.0.1" in launcher
    assert "--remote-debugging-port=9222" in launcher


def test_navigation_failure_blocks_misleading_blank_remote_login_session():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    open_session = helper.split("def open_session()", 1)[1].split("def close_session()", 1)[0]
    assert '"error": "login_navigation_failed"' in open_session
    assert 'SESSION.close()' in open_session
    assert '"navigation_warning"' not in open_session
    assert '"ok": False' in open_session
    assert '"ok": True' in open_session


def test_binding_navigation_keeps_secret_url_off_process_argv_and_devtools_http_query():
    helper = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    binding = helper.split("def inject_worker_binding", 1)[1].split("def send_input", 1)[0]
    cdp = helper.split("def _navigate_secret_url_via_cdp", 1)[1].split("def _open_url_via_existing_chrome", 1)[0]
    assert '_navigate_secret_url_via_cdp(binding_url, error_name="binding_injection_failed")' in binding
    assert "_focus_window(" not in binding
    assert "_type_url_into_focused_chrome(" not in binding
    assert 'endpoint = f"{CHROME_DEBUG_URL}/json/new?about:blank"' in cdp
    assert '"method": "Page.navigate"' in cdp
    assert '"params": {"url": url}' in cdp
