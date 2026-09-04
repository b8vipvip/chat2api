from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_request_history_has_exactly_one_frontend_owner() -> None:
    owner = text("app/admin_request_history_v93.js")
    prompt_base = text("app/admin_prompt_config_v72.js")
    prompt_editor = text("app/admin_prompt_config_v75.js")
    identity = text("app/admin_request_device_identity_v47.js")
    windows = text("app/admin_window_manager_v88.js")

    assert '__CHAT2API_REQUEST_HISTORY_OWNER_V93__' in owner
    assert 'globalThis.loadRequests = loadRequestsV93;' in owner
    assert 'body.innerHTML = state.lastRows.length' in owner
    assert 'data-request-action="prompt"' in owner
    assert 'data-request-action="detail"' in owner
    assert 'new MutationObserver' not in owner

    # Prompt configuration is presentation/configuration-only. It must never own
    # request navigation, request loading, or request-row repair.
    assert 'window.loadRequests =' not in prompt_base
    assert 'globalThis.loadRequests =' not in prompt_base
    assert 'window.show =' not in prompt_base
    assert 'globalThis.show =' not in prompt_base
    assert 'augmentRequestRows' not in prompt_base
    assert 'repairPromptCells' not in prompt_editor
    assert 'observeRequestRows' not in prompt_editor
    assert 'new MutationObserver' not in prompt_editor
    assert 'rqBody' not in prompt_editor

    # Device terminology and Window Management are separate bounded domains.
    # Neither is allowed to decorate request rows anymore.
    assert 'hookApi' not in identity
    assert 'paintRequestRows' not in identity
    assert 'rqBody' not in identity
    assert 'paintRequestIds' not in windows
    assert 'installRequestIdObserver' not in windows
    assert 'rqBody' not in windows
    assert 'new MutationObserver' not in windows


def test_device_identity_normalization_is_bounded_to_navigation() -> None:
    source = text("app/admin_request_device_identity_v47.js")
    assert 'canonicalizeStaticChrome();' in source
    assert 'queueCanonicalizeActiveView' in source
    assert '.nav button[data-view]' in source
    assert 'window.addEventListener("hashchange", queueCanonicalizeActiveView)' in source
    assert 'canonicalizeElement(document.body);' not in source
    assert '.observe(document.body' not in source


def test_window_manager_polling_exists_only_while_its_view_is_active() -> None:
    source = text("app/admin_window_manager_v88.js")
    assert 'navigation_revision: 93' in source
    assert 'const FETCH_TIMEOUT_MS = 8000' in source
    assert 'new AbortController()' in source
    assert 'function stopPolling()' in source
    assert 'function schedulePoll(' in source
    assert 'if (!isActive() || document.hidden) return;' in source
    assert 'signal: controller.signal' in source
    assert 'if (location.hash !== "#window-manager") location.hash = "window-manager";' in source
    assert 'document.addEventListener("visibilitychange"' in source
    assert 'setInterval(' not in source


def test_request_history_asset_is_served_by_request_route_owner() -> None:
    source = text("app/request_device_identity_patch.py")
    assert 'REQUEST_HISTORY_ASSET = "/assets/chat2api-request-history-v93.js"' in source
    assert 'Path(__file__).with_name("admin_request_history_v93.js")' in source
    assert 'history_marker = f\'<script src="{REQUEST_HISTORY_ASSET}"></script>\'' in source
    assert '__chat2api_request_stability_v93__' in source


def test_admin_single_owner_javascript_parses() -> None:
    node = shutil.which("node")
    if not node:
        return
    for filename in (
        "app/admin_request_history_v93.js",
        "app/admin_request_device_identity_v47.js",
        "app/admin_window_manager_v88.js",
        "app/admin_prompt_config_v72.js",
        "app/admin_prompt_config_v75.js",
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
