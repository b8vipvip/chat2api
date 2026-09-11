from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v91_terminal_reporter_registers_before_router_cleanup_listeners() -> None:
    entry = text("chrome_extension/background_entry.js")
    router = entry.index('"conversation_routing.js"')
    dispatch = entry.index('"conversation_dispatch.js"')
    terminal = entry.index('"background_route_close_terminal_v91.js"')
    observer = entry.index('"background_window_observer_v90.js"')
    assert terminal < router < dispatch < observer
    assert '"background_request_recovery_v40.js"' not in entry


def test_v91_reports_unexpected_active_route_close_without_lifecycle_authority() -> None:
    source = text("chrome_extension/background_route_close_terminal_v91.js")
    assert 'policy: "terminal-report-only-v91"' in source
    assert "chrome.tabs.onRemoved.addListener" in source
    assert "chrome.windows.onRemoved.addListener" in source
    assert "trySendSocket" in source
    assert 'return "chat.error"' in source
    assert 'return "image.error"' in source
    assert 'return "voice.error"' in source
    assert "unexpected_route_close_terminal_v91: true" in source
    assert 'route_window_authority: "single-route-window-authority-v30"' in source

    for forbidden in (
        "chrome.windows.create",
        "chrome.windows.remove",
        "chrome.tabs.create",
        "chrome.tabs.remove",
        "resetClosedRoute(",
        ".retireRoute(",
        "route.inflight_request_id =",
        "route.window_id =",
        "route.tab_id =",
    ):
        assert forbidden not in source


def test_v91_source_parses_as_javascript() -> None:
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run(
        [node, "--check", str(ROOT / "chrome_extension/background_route_close_terminal_v91.js")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
