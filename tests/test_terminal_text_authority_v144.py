import asyncio
from pathlib import Path

from app.broker import RequestBroker, _monotonic_text, _terminal_text


ROOT = Path(__file__).resolve().parents[1]


def test_controller_terminal_overrides_intermediate_snapshot_even_when_shorter():
    full = '<<<CHAT2API_RESPONSES_TOOL_V109>>>\n{"kind":"final","text":"FDEX_CODEX_SMOKE_cdac2df519f84e20_WIRE"}\n<<<END_CHAT2API_RESPONSES_TOOL_V109>>>'
    partial = '<<<CHAT2API_RESPONSES_TOOL_V109>>>'
    assert _terminal_text(full, partial) == (partial, "terminal")


def test_terminal_reconciliation_never_replaces_with_unrelated_stream_text():
    assert _terminal_text("different network observation", "controller final") == ("controller final", "terminal")


def test_snapshot_accumulation_only_accepts_prefix_compatible_growth():
    assert _monotonic_text("", "abc") == ("abc", "initial")
    assert _monotonic_text("abc", "abcdef") == ("abcdef", "prefix-extension")
    assert _monotonic_text("abcdef", "abc") == ("abcdef", "stale-prefix")
    assert _monotonic_text("abcdef", "unrelated") == ("abcdef", "divergent-ignored")


def test_late_partial_or_divergent_snapshot_cannot_destroy_full_evidence():
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req-1", "worker-1")
        full = '<<<CHAT2API_RESPONSES_TOOL_V109>>>\n{"kind":"final","text":"FDEX_CODEX_SMOKE_cdac2df519f84e20_WIRE"}\n<<<END_CHAT2API_RESPONSES_TOOL_V109>>>'
        partial = '<<<CHAT2API_RESPONSES_TOOL_V109>>>'
        await broker.publish("req-1", {"type": "chat.snapshot", "text": full})
        await broker.publish("req-1", {"type": "chat.snapshot", "text": partial})
        await broker.publish("req-1", {"type": "chat.snapshot", "text": "unrelated evidence"})
        assert state.text == full
        await broker.publish("req-1", {"type": "chat.completed", "text": partial})
        assert await state.final_future == partial
        assert state.diagnostics["terminal_text_source"] == "terminal"
        await broker.release("req-1")

    asyncio.run(scenario())


def test_worker_manifest_has_one_terminal_completion_owner():
    manifest = (ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8")
    assert '"content_request_v6.js"' in manifest
    assert '"content_network_stream_recovery_v55.js"' in manifest
    assert '"content_response_stream_recovery_v49.js"' not in manifest
    assert '"content_response_stream_recovery_v69.js"' not in manifest
    assert '"content_terminal_integrity_v89.js"' not in manifest


def test_network_observer_is_evidence_only():
    source = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert 'network-stream-evidence-v57' in source
    assert 'network_terminal_authority: "request-v6"' in source
    assert 'type: "chat.completed"' not in source
    assert 'active.cancelled = true' not in source


def test_prompt_overlay_does_not_arbitrate_terminal_events():
    source = (ROOT / "chrome_extension" / "content_request_terminal_prompt_v88.js").read_text(encoding="utf-8")
    assert 'role: "prompt-insertion-only"' in source
    assert 'chrome.runtime.sendMessage = function' not in source
    assert 'network-success-is-terminal' not in source


def test_broker_first_terminal_wins_for_success_error_and_late_snapshots():
    async def scenario() -> None:
        broker = RequestBroker()
        completed = await broker.create("req-done", "worker-done")
        assert await broker.publish("req-done", {"type": "chat.snapshot", "text": "draft"})
        assert await broker.publish("req-done", {"type": "chat.completed", "text": "final"})
        assert not await broker.publish("req-done", {"type": "chat.error", "error": "late failure"})
        assert not await broker.publish("req-done", {"type": "chat.completed", "text": "different"})
        assert not await broker.publish("req-done", {"type": "chat.snapshot", "text": "after terminal"})
        assert await completed.final_future == "final"
        assert completed.terminal_event_type == "chat.completed"
        assert completed.queue.qsize() == 2
        await broker.release("req-done")

        failed = await broker.create("req-error", "worker-error")
        assert await broker.publish("req-error", {"type": "chat.error", "error": "real error"})
        assert not await broker.publish("req-error", {"type": "chat.completed", "text": "late answer"})
        try:
            await failed.final_future
            assert False, "first error must be preserved"
        except RuntimeError as error:
            assert str(error) == "real error"
        assert failed.terminal_event_type == "chat.error"
        assert failed.queue.qsize() == 1
        await broker.release("req-error")

    asyncio.run(scenario())


def test_extension_has_only_one_request_terminal_owner():
    import json
    manifest = json.loads((ROOT / "chrome_extension/manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_request_v6.js" in scripts
    assert "content_request_stall_guard_v34.js" not in scripts
    assert "content_request_interruption_bridge_v72.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    status = (ROOT / "chrome_extension/content_request_lifecycle_v50.js").read_text(encoding="utf-8")
    assert 'type: "chat.error"' not in status
    assert 'type: "chat.completed"' not in status
