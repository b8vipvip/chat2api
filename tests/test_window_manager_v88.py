from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_window_creation_is_registered_as_loading_before_pool_readiness() -> None:
    source = text("chrome_extension/background_window_lifecycle_observer_v88.js")
    assert "const baseCreate = chrome.windows.create.bind(chrome.windows)" in source
    assert "openedAt = Date.now()" in source
    assert 'status: "loading"' in source
    assert 'source: "creation-observer-v88"' in source
    assert "wm.nextWindowNo" in source
    assert "wm.active.set(win.id, record)" in source
    assert "wm.reconcile?.(true)" in source


def test_terminal_prompt_overlay_does_not_arbitrate_terminal_events() -> None:
    guard = text("chrome_extension/content_request_terminal_prompt_v88.js")
    manager = text("chrome_extension/background_window_manager_v88.js")
    assert 'role: "prompt-insertion-only"' in guard
    assert 'terminal_authority: "request-v6"' in guard
    assert "chrome.runtime.sendMessage =" not in guard
    assert 'event?.type === "chat.cancelled" || event?.type === "chat.error"' not in guard
    assert "state.protectedUntil" in manager
    assert "repairSuccessfulRoute" in manager
    assert "SUCCESS_LEASE_MS = 5 * 60 * 1000" in manager


def test_long_prompt_avoids_unbounded_execcommand_inserttext() -> None:
    manifest = json.loads(text("chrome_extension/manifest.json"))
    scripts = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")["js"]
    assert scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_request_terminal_prompt_v88.js")
    guard = text("chrome_extension/content_request_terminal_prompt_v88.js")
    assert "LONG_PROMPT_THRESHOLD = 2048" in guard
    assert "editable.replaceChildren(document.createTextNode(text))" in guard
    assert 'prompt_fast_insert_method: "direct-text-node+input-event"' in guard
