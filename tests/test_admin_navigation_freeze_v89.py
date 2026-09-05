from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_device_identity_no_longer_watches_the_entire_admin_dom() -> None:
    source = text("app/admin_request_device_identity_v47.js")
    assert 'canonicalizeStaticChrome();' in source
    assert 'queueCanonicalizeActiveView' in source
    assert '.nav button[data-view]' in source
    assert 'window.addEventListener("hashchange", queueCanonicalizeActiveView)' in source
    assert 'canonicalizeElement(document.body);' not in source
    assert '.observe(document.body' not in source
    assert 'subtree:true,characterData:true' not in source


def test_device_identity_has_no_request_table_or_api_render_authority() -> None:
    source = text("app/admin_request_device_identity_v47.js")
    assert 'structural_owner: false' in source
    assert 'function scheduleRequestPaint()' not in source
    assert 'paintRequestRows' not in source
    assert 'new MutationObserver' not in source
    assert 'queueMicrotask(paintRequestRows)' not in source
    assert 'const wrapped = async function(path' not in source
    assert 'closest("script,style,tbody,pre,code")' in source
    assert 'element.closest?.("tbody,pre,code")' in source


def test_window_manager_polling_exists_only_while_its_view_is_active() -> None:
    source = text("app/admin_window_manager_v88.js")
    assert 'navigation_revision: 94' in source
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
    assert 'paintRequestIds' not in source
    assert 'new MutationObserver' not in source


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
