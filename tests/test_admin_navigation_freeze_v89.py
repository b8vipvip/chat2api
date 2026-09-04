from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_device_identity_no_longer_watches_the_entire_admin_dom() -> None:
    source = text("app/admin_request_device_identity_v47.js")
    # This exact whole-document observer pattern previously caused the console to
    # become unresponsive without a JavaScript exception. Identity decoration now
    # runs once for the shell and after bounded API/navigation events instead.
    assert 'canonicalizeElement(document.body);' in source
    assert 'queueCanonicalizeActiveView' in source
    assert '.nav button[data-view]' in source
    assert 'window.addEventListener("hashchange", queueCanonicalizeActiveView)' in source
    assert '.observe(document.body' not in source
    assert 'subtree:true,characterData:true' not in source


def test_window_manager_polling_exists_only_while_its_view_is_active() -> None:
    source = text("app/admin_window_manager_v88.js")
    assert 'navigation_revision: 89' in source
    assert 'const FETCH_TIMEOUT_MS = 8000' in source
    assert 'new AbortController()' in source
    assert 'function stopPolling()' in source
    assert 'function schedulePoll(' in source
    assert 'if (!isActive() || document.hidden) return;' in source
    assert 'signal: controller.signal' in source
    assert 'if (location.hash !== "#window-manager") location.hash = "window-manager";' in source
    assert 'view !== "window-manager") stopPolling()' in source
    assert 'document.addEventListener("visibilitychange"' in source
    assert 'setInterval(' not in source
    # Request-ID decoration is mutation-driven and navigation-driven; it must not
    # be repainted by a permanent timer while unrelated console views are open.
    assert 'new MutationObserver(() => queueMicrotask(paintRequestIds)).observe(body, { childList: true, subtree: false });' in source
    assert 'paintRequestIds();\n    }, 2500)' not in source


def test_admin_freeze_hotfix_javascript_parses() -> None:
    node = shutil.which("node")
    if not node:
        return
    for filename in (
        "app/admin_request_device_identity_v47.js",
        "app/admin_window_manager_v88.js",
    ):
        completed = subprocess.run(
            [node, "--check", str(ROOT / filename)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, f"{filename}: {completed.stderr}"
