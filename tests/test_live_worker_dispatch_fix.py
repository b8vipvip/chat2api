from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_gpt_live_outer_handler_uses_serialized_worker_allocator() -> None:
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    dispatch = (EXTENSION / "conversation_dispatch.js").read_text(encoding="utf-8")
    live = (EXTENSION / "audio_routing_live.js").read_text(encoding="utf-8")

    # Live is intentionally the outer handler, so it must explicitly enter the
    # dispatcher's worker-allocation queue for session start.
    assert entry.index('"conversation_dispatch.js"') < entry.index('"audio_routing_live.js"')
    assert "chat2apiResolveRoutedWorkerTabV24" in dispatch
    assert "enqueueDispatch(() => resolveRoutedTab(message))" in dispatch
    assert "chat2apiResolveRoutedWorkerTabV24" in live
    assert "return routed(message)" in live
    assert "const tab = await resolveLiveWorkerTab(message)" in live


def test_live_worker_lock_is_released_before_webrtc_startup() -> None:
    live = (EXTENSION / "audio_routing_live.js").read_text(encoding="utf-8")
    allocation = live.index("const tab = await resolveLiveWorkerTab(message)")
    startup = live.index("chrome.tabs.sendMessage(tab.id", allocation)
    assert allocation < startup
    assert ".then(response =>" in live[startup:]
