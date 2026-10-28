"""The API transport cannot become a second Worker terminal owner."""
from __future__ import annotations
import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.stream_detached_terminal_v219 import settle_detached_stream

ROOT = Path(__file__).resolve().parents[1]


def test_client_disconnect_preserves_broker_lease_until_real_worker_completion():
    async def exercise():
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(request_id="req_owner", final_future=loop.create_future(),
                                terminal_event_type=None, diagnostics={})
        events = []
        class Broker:
            async def publish(self, *_args):
                raise AssertionError("No synthetic broker terminal before Worker completion")
        async def finalize(error):
            events.append(("finalize", error, state.terminal_event_type))
        async def release():
            events.append(("release",))
        task = asyncio.create_task(settle_detached_stream(
            state=state, broker=Broker(), finalize=finalize,
            release=release, wait_seconds=2))
        await asyncio.sleep(0.01)
        assert not task.done() and events == []
        state.terminal_event_type = "chat.completed"
        state.final_future.set_result("real answer")
        await task
        assert events == [("finalize", None, "chat.completed"), ("release",)]
        assert state.diagnostics["api_stream_detached_worker_terminal"] == "chat.completed"
    asyncio.run(exercise())


def test_detached_worker_error_is_not_promoted_to_success():
    async def exercise():
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(request_id="req_error", final_future=loop.create_future(),
                                terminal_event_type="chat.error", diagnostics={})
        state.final_future.set_exception(RuntimeError("Worker failed"))
        events = []
        class Broker:
            async def publish(self, *_args):
                raise AssertionError("No second terminal")
        async def finalize(error):
            events.append(("finalize", error))
        async def release():
            events.append(("release",))
        await settle_detached_stream(state=state, broker=Broker(), finalize=finalize,
                                     release=release, wait_seconds=1)
        assert events == [("finalize", "Worker failed"), ("release",)]
    asyncio.run(exercise())


def test_detached_timeout_fails_through_broker_not_http_finally():
    async def exercise():
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(request_id="req_timeout", final_future=loop.create_future(),
                                terminal_event_type=None, diagnostics={})
        events = []
        class Broker:
            async def publish(self, request_id, event):
                assert request_id == state.request_id
                events.append(("broker", event["type"]))
                state.terminal_event_type = event["type"]
                state.final_future.set_exception(RuntimeError(event["error"]))
                return True
        async def finalize(error):
            events.append(("finalize", error))
        async def release():
            state.final_future.exception()
            events.append(("release",))
        await settle_detached_stream(state=state, broker=Broker(), finalize=finalize,
                                     release=release, wait_seconds=0.01)
        assert events[0] == ("broker", "chat.error")
        assert events[1][0] == "finalize"
        assert events[2] == ("release",)
    asyncio.run(exercise())


def test_sse_disconnect_only_changes_delivery_state_and_keepalive_is_outermost():
    src = (ROOT / "app/main.py").read_text()
    segment = src[src.index("        except asyncio.CancelledError:", src.index("    async def stream_request(")):
                  src.index('    @app.post("/v1/chat/completions")')]
    assert 'state.diagnostics["api_delivery_state"] = "disconnected"' in segment
    assert "settle_detached_stream(" in segment
    assert "async def release_broker_after_terminal()" in segment
    assert "if recorded:" in segment
    assert '"cancelled" if not worker_terminal' not in segment
    assert 'await registry.send(state.client_id, {' not in segment
    entry = (ROOT / "app/entry.py").read_text()
    assert entry.rindex("install_stream_keepalive_patch(app)") > entry.rindex(
        "install_worker_probe_ui_v1(app)"
    )
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")

def test_nonstream_http_cancel_keeps_same_broker_terminal_owner():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    handler = source.split('    @app.post("/v1/chat/completions")', 1)[1].split(
        '    @app.post("/v1/images/generations")', 1
    )[0]
    assert 'detached_nonstream = False' in handler
    assert 'except asyncio.CancelledError:' in handler
    assert 'api_nonstream_close_cause' in handler
    assert 'awaiting-broker-terminal-after-http-disconnect' in handler
    assert 'settle_detached_stream(' in handler
    assert 'if not detached_nonstream:' in handler
    assert 'async def persist_detached_nonstream(' in handler
    assert 'async def release_detached_nonstream(' in handler
    # No synthetic successful terminal on ASGI cancellation and no extra
    # request controller. The sole Broker remains the final event owner.
    detached = handler.split('        except asyncio.CancelledError:', 2)[-1].split(
        '        except asyncio.TimeoutError as error:', 1
    )[0]
    assert '"type": "chat.completed"' not in detached
    assert 'await broker.release(request_id)' in detached  # only in release callback
    assert 'state.terminal_event_type' in detached


def test_nonstream_cancelled_http_wait_preserves_late_worker_completion():
    from app.broker import RequestBroker

    async def exercise():
        broker = RequestBroker()
        state = await broker.create("req_nonstream_disconnect", "ext_one")
        events = []

        async def finalize(error):
            assert error is None
            assert state.terminal_event_type == "chat.completed"
            events.append(("persist", state.text))
        async def release():
            events.append(("release", state.terminal_event_type))
            await broker.release(state.request_id)

        detached = asyncio.create_task(settle_detached_stream(
            state=state, broker=broker, finalize=finalize, release=release,
            wait_seconds=2,
        ))
        await asyncio.sleep(0)
        assert state.request_id in broker.requests
        assert not detached.done() and not events
        await broker.publish(state.request_id, {
            "type": "chat.completed", "request_id":state.request_id,
            "text": "The actual full Worker final answer",
        })
        await detached
        assert events == [
            ("persist", "The actual full Worker final answer"),
            ("release", "chat.completed"),
        ]
        assert state.request_id not in broker.requests

    asyncio.run(exercise())
