from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "app" / "admin_server_update_fetch_guard.js"


def test_server_update_guard_wraps_global_api_used_by_update_console():
    source = GUARD.read_text(encoding="utf-8")

    # admin_server_update.js prefers the global api() helper. admin_v17 captures
    # native fetch before the late update guard is injected, so wrapping only
    # window.fetch does not protect status polling. Keep both layers guarded.
    for token in (
        'const baseApi=typeof window.api==="function"?window.api:null;',
        "if(baseApi&&!baseApi.__chat2apiServerUpdatePollGuard)",
        "window.api=guardedApi",
        'credentials:"same-origin"',
        "error.status=response.status",
    ):
        assert token in source


def test_server_update_guard_recovers_expired_admin_session_without_manual_refresh():
    source = GUARD.read_text(encoding="utf-8")

    assert "response.status===401||response.status===403" in source
    assert "scheduleAuthRecovery()" in source
    assert "window.location.reload()" in source
    assert "更新任务仍会在服务器后台继续" in source


def test_server_update_guard_still_has_bounded_status_poll_timeout():
    source = GUARD.read_text(encoding="utf-8")

    assert "POLL_TIMEOUT_MS=3000" in source
    assert 'url.pathname==="/api/admin/server-update/status"' in source
    assert "new AbortController()" in source
    assert "controller.abort()" in source
    assert "options.signal=controller.signal" in source


def test_server_update_auth_guard_parses_as_javascript():
    result = subprocess.run(
        ["node", "--check", str(GUARD)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
