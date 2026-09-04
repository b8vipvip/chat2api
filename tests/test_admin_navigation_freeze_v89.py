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
    # runs once for the shell and after bounded navigation events instead.
    assert 'canonicalizeElement(document.body);' in source
    assert 'queueCanonicalizeActiveView' in source
    assert '.nav button[data-view]' in source
    assert 'window.addEventListener("hashchange", queueCanonicalizeActiveView)' in source
    assert '.observe(document.body' not in source
    assert 'subtree:true,characterData:true' not in source


def test_device_identity_never_treewalks_request_data_after_background_api_calls() -> None:
    source = text("app/admin_request_device_identity_v47.js")
    # v0.22.57 removed the broad document observer, but its api wrapper still
    # scheduled active-view canonicalization after *every* successful console API
    # request. v0.22.58 added more live-window truth traffic, which exposed that
    # latent starvation path while the 100-row request table was visible.
    assert 'captureRequestRows(path, payload);\n      queueCanonicalizeActiveView();' not in source
    assert 'canonicalizeElement(document.getElementById("view-requests"));' not in source
    assert 'closest("script,style,tbody,pre,code")' in source
    assert 'element.closest?.("tbody,pre,code")' in source
    # Request identity remains mutation-driven on exactly rqBody, not a parent or
    # the whole document, so table replacement still gets its additive cells.
    assert 'new MutationObserver(() => queueMicrotask(paintRequestRows)).observe(tbody, {childList:true,subtree:false});' in source


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
