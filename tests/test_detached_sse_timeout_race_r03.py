"""R-03: detached SSE timeout must not beat a real Worker terminal."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.stream_detached_terminal_v219 as detached


def test_worker_terminal_winning_lease_timeout_race_is_not_fabricated_error(monkeypatch) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        state = SimpleNamespace(
            request_id="req_r03_race", final_future=future,
            terminal_event_type=None, diagnostics={},
        )
        actions = []

        async def fake_wait_for(_future, timeout):
            assert timeout >= 1
            # Mimic an already accepted Worker terminal at the exact lease
            # boundary, after wait_for chooses its timeout outcome.
            state.terminal_event_type = "chat.completed"
            future.set_result("real-worker-reply")
            raise asyncio.TimeoutError()

        async def finalize(error):
            actions.append(("finalize", error, state.terminal_event_type))

        async def release():
            actions.append(("release",))

        class Broker:
            async def publish(self, request_id, event):
                assert request_id == state.request_id
                assert event["type"] == "chat.error"
                actions.append(("timeout-candidate-rejected",))
                return False

        monkeypatch.setattr(detached.asyncio, "wait_for", fake_wait_for)
        await detached.settle_detached_stream(
            state=state, broker=Broker(), finalize=finalize,
            release=release, wait_seconds=1,
        )
        assert actions == [
            ("timeout-candidate-rejected",),
            ("finalize", None, "chat.completed"),
            ("release",),
        ]
        assert "detached_stream_terminal_wait_timeout" not in state.diagnostics
        assert state.diagnostics["detached_stream_worker_won_timeout_race"] is True
        assert state.diagnostics["api_stream_detached_worker_terminal"] == "chat.completed"

    asyncio.run(exercise())


def test_rejected_timeout_without_known_worker_terminal_stays_unknown(monkeypatch) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(
            request_id="req_r03_unknown", final_future=loop.create_future(),
            terminal_event_type=None, diagnostics={},
        )
        errors = []
        released = []

        async def timeout(_future, timeout):
            raise asyncio.TimeoutError()

        class Broker:
            async def publish(self, *_args):
                return False

        async def finalize(error):
            errors.append(error)

        async def release():
            released.append(True)

        monkeypatch.setattr(detached.asyncio, "wait_for", timeout)
        await detached.settle_detached_stream(
            state=state, broker=Broker(), finalize=finalize,
            release=release, wait_seconds=1,
        )
        assert errors and "did not reach a terminal" in errors[0]
        assert released == [True]
        assert "detached_stream_terminal_wait_timeout" not in state.diagnostics
        assert "detached_stream_worker_won_timeout_race" not in state.diagnostics

    asyncio.run(exercise())



def test_missing_future_cannot_override_existing_broker_terminal() -> None:
    async def exercise() -> None:
        state = SimpleNamespace(
            request_id="req_r03_missing_future",
            final_future=None,
            terminal_event_type="chat.completed",
            diagnostics={},
        )
        finalized = []
        class Broker:
            async def publish(self, _request_id, event):
                assert event["type"] == "chat.error"
                return False
        async def finalize(error):
            finalized.append(error)
        async def release():
            return None

        await detached.settle_detached_stream(
            state=state, broker=Broker(), finalize=finalize,
            release=release, wait_seconds=1,
        )
        assert finalized == [None]
        assert state.diagnostics["api_stream_detached_worker_terminal"] == "chat.completed"
        assert state.diagnostics["detached_stream_worker_won_timeout_race"] is True

    asyncio.run(exercise())



def test_timeout_publish_exception_still_finalizes_and_releases_detached_request(monkeypatch) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(
            request_id="req_r03_publish_exception",
            final_future=loop.create_future(),
            terminal_event_type=None,
            diagnostics={},
        )
        finalized = []
        released = []

        async def timeout(_future, timeout):
            assert timeout >= 1
            raise asyncio.TimeoutError()

        class BrokenBroker:
            async def publish(self, *_args):
                raise RuntimeError("simulated Broker publish outage")

        async def finalize(error):
            finalized.append(error)

        async def release():
            released.append(True)

        monkeypatch.setattr(detached.asyncio, "wait_for", timeout)
        await detached.settle_detached_stream(
            state=state, broker=BrokenBroker(), finalize=finalize,
            release=release, wait_seconds=1,
        )
        assert len(finalized) == 1
        assert "did not reach a terminal" in finalized[0]
        assert released == [True]
        assert state.diagnostics["detached_stream_timeout_publish_error"] == "RuntimeError"
        assert state.diagnostics["api_stream_detached_settled"] is True

    asyncio.run(exercise())


def test_timeout_publish_exception_after_broker_terminal_keeps_real_terminal(monkeypatch) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(
            request_id="req_r03_publish_after_accept",
            final_future=loop.create_future(),
            terminal_event_type=None,
            diagnostics={},
        )
        finalized = []
        released = []

        async def timeout(_future, timeout):
            assert timeout >= 1
            raise asyncio.TimeoutError()

        class AcceptedThenRaisedBroker:
            async def publish(self, *_args):
                # Broker committed the terminal before a downstream notification
                # raised. Its own state is the authority, not the call exception.
                state.terminal_event_type = "chat.completed"
                raise RuntimeError("simulated post-accept notification failure")

        async def finalize(error):
            finalized.append(error)

        async def release():
            released.append(True)

        monkeypatch.setattr(detached.asyncio, "wait_for", timeout)
        await detached.settle_detached_stream(
            state=state, broker=AcceptedThenRaisedBroker(), finalize=finalize,
            release=release, wait_seconds=1,
        )
        assert finalized == [None]
        assert released == [True]
        assert state.diagnostics["detached_stream_timeout_publish_error"] == "RuntimeError"
        assert state.diagnostics["detached_stream_worker_won_timeout_race"] is True
        assert state.diagnostics["api_stream_detached_worker_terminal"] == "chat.completed"

    asyncio.run(exercise())
