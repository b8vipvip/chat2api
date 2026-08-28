from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_update_status_poll_has_browser_side_timeout_guard():
    guard = (ROOT / "app" / "admin_server_update_fetch_guard.js").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "server_update_patch.py").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")

    for token in (
        "POLL_TIMEOUT_MS=3000",
        'url.pathname==="/api/admin/server-update/status"',
        "new AbortController()",
        "controller.abort()",
        "options.signal=controller.signal",
        ".finally(()=>window.clearTimeout(timer))",
    ):
        assert token in guard

    assert 'FETCH_GUARD_ASSET_PATH = "/assets/chat2api-server-update-fetch-guard.js"' in patch
    assert 'Path(__file__).with_name("admin_server_update_fetch_guard.js")' in patch
    assert "guard + marker" in patch
    assert 'SERVER_RUNTIME_VERSION = "0.22.32"' in runtime
    assert '"server_update_poll_timeout_guard": True' in runtime
    assert '"github_transport_failover": True' in runtime


def test_server_update_fetch_guard_parses_as_javascript():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_server_update_fetch_guard.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
