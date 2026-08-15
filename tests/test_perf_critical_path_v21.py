from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def test_warm_pool_has_bounded_claim_and_paid_model_readiness() -> None:
    source = read("conversation_warm_pool_v2.js")
    assert "CLAIM_WAIT_MS = 1800" in source
    assert "REQUEST_READY_WAIT_MS = 1400" in source
    assert "warm-opening-exceeded-claim-budget" in source
    assert "warm-model-controller-not-ready" in source
    assert "requestRequiresModelPicker" in source
    assert 'model === "gpt-5.5-mini" && accountType === "free"' in source
    assert '"composer+model-controller-ready"' in source
    assert '"composer-controller-ready"' in source
    assert "conversation_prewarm_bypassed" in source
    assert "conversation_prewarm_claim_wait_ms" in source
    # Concurrent-request behavior remains intentional: claim A, refill its slot immediately.
    assert "scheduleWarm(350, warm.slot_key)" in source


def test_fast_family_prefetch_is_best_effort_and_canonical_router_remains_owner() -> None:
    content = read("content_model_fast_v21.js")
    background = read("model_prefetch_fast_v21.js")
    routing = read("model_routing_v2.js")
    entry = read("background_entry.js")

    assert '"gpt-5.6-sol"' in content
    assert '"gpt-5.5"' in content
    assert "fast-family-click-passive-verify-later" in content
    assert "chat2api.model.prepare.fast.v21" in content
    assert "baseHandleServerMessage(message)" in background
    assert "Optimization failure must never replace the canonical model-selection path" in background
    assert "model_prefetch_fast_v21" in background
    assert "prepareRequestedState" in routing
    assert "probeState" in routing
    assert entry.index('"model_routing_v2.js"') < entry.index('"model_prefetch_fast_v21.js"')
    assert entry.index('"model_prefetch_fast_v21.js"') < entry.index('"background_logging.js"')
    assert entry.index('"background_logging.js"') < entry.index('"conversation_dispatch.js"')


def test_send_click_fast_enter_requires_strong_ignored_click_signal() -> None:
    source = read("content_request_perf_v21.js")
    assert "FAST_FALLBACK_MS = 1200" in source
    assert "prompt-present+not-generating+send-ready" in source
    assert "submit_fast_enter_fallback" in source
    assert "promptStillPresent" not in source
    assert "stopButton()" in source
    assert "buttonReady(button)" in source


def test_fast_completion_only_uses_final_actions_and_preserves_v6_fallback() -> None:
    fast = read("content_completion_fast_v21.js")
    conservative = read("content_completion_v6.js")
    manifest = json.loads(read("manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]

    assert "strong-final-actions-fast" in fast
    assert "stableMs >= 900" in fast
    assert "transientStatusVisible()" in fast
    assert "finalActionControls" in fast
    assert "stableMs >= 2500" in conservative
    assert "stableMs >= 9000" in conservative
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_perf_v21.js")
    assert scripts.index("content_completion_v6.js") < scripts.index("content_completion_fast_v21.js")


def test_socket_connect_is_singleflight_without_protocol_changes() -> None:
    source = read("background_socket_singleflight_v21.js")
    entry = read("background_entry.js")
    background = read("background.js")

    assert "state.inFlight" in source
    assert "WebSocket.OPEN" in source
    assert "WebSocket.CONNECTING" in source
    assert "baseConnectSocket" in source
    assert entry.index('"background_hardening.js"') < entry.index('"background_socket_singleflight_v21.js"')
    assert "new WebSocket(wsUrl" in background
    assert "/ws/extensions/" in background


def test_bootstrap_injects_perf_scripts_for_existing_tabs() -> None:
    bootstrap = read("content_bootstrap.js")
    for name in [
        "content_model_fast_v21.js",
        "content_request_perf_v21.js",
        "content_completion_fast_v21.js",
        "content_reasoning_transport_v20.js",
        "content_format_v20.js",
        "content_account_v20.js",
    ]:
        assert f'"{name}"' in bootstrap


def test_openai_http_api_surface_is_untouched_by_performance_patch() -> None:
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.post("/v1/chat/completions")' in main
    assert 'media_type="text/event-stream"' in main
    assert '"chat.completion.chunk"' in main
    assert 'data: [DONE]' in main
