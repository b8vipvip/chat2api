"""Cancellation and duplicate-media protection across SSE/detached finalizers."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.terminal_archive_singleflight_v277 import archive_once


def test_http_cancel_during_archive_reuses_same_task_for_detached_terminal():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        state = SimpleNamespace(diagnostics={})
        archive_calls = []
        async def fake_archive():
            archive_calls.append("media.write")
            started.set()
            await release.wait()
            return ("![img](/api/media/med_shared/opaque)", [{"id": "med_shared"}], [])

        args = dict(state=state, request_id="req_archive_singleflight", owner="owner_A",
                    final_text="![img](data:image/png;base64,AA==)",
                    base_url="https://same-origin.example", run=fake_archive)
        http = asyncio.create_task(archive_once(**args))
        await asyncio.wait_for(started.wait(), timeout=1)
        http.cancel()
        with pytest.raises(asyncio.CancelledError):
            await http
        assert archive_calls == ["media.write"], "HTTP cancellation cannot cancel the archive itself"
        detached = asyncio.create_task(archive_once(**args))
        await asyncio.sleep(0)
        assert archive_calls == ["media.write"], "detached finalization must not start another write"
        release.set()
        result = await asyncio.wait_for(detached, timeout=1)
        assert result[1] == [{"id": "med_shared"}]
        assert await archive_once(**args) is result
        assert state.diagnostics["server_finalization_archive_reused"] is True
        assert len(archive_calls) == 1

    asyncio.run(scenario())


def test_parallel_consumers_share_one_archive_without_waiting_before_registration():
    async def scenario():
        state = SimpleNamespace(diagnostics={})
        calls = 0
        async def store():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return ["unique"]
        kwargs = dict(state=state, request_id="req_parallel", owner="k",
                      final_text="final", base_url="https://same.example", run=store)
        outputs = await asyncio.gather(*(archive_once(**kwargs) for _ in range(12)))
        assert calls == 1
        assert all(value is outputs[0] for value in outputs)
    asyncio.run(scenario())


def test_changed_terminal_or_owner_cannot_reuse_stored_media():
    async def scenario():
        state = SimpleNamespace(diagnostics={})
        async def store():
            return ["safe"]
        common = dict(state=state, request_id="req_bound", owner="key_1",
                      final_text="image-original", base_url="https://site.example", run=store)
        assert await archive_once(**common) == ["safe"]
        for altered in (
            {"request_id": "req_other"},
            {"owner": "key_2"},
            {"final_text": "altered bytes"},
            {"base_url": "https://other.example"},
        ):
            with pytest.raises(ValueError, match="identity changed"):
                await archive_once(**{**common, **altered})
    asyncio.run(scenario())


def test_archival_failure_is_shared_without_duplicate_side_effects():
    async def scenario():
        state = SimpleNamespace(diagnostics={})
        calls = 0
        async def fail():
            nonlocal calls
            calls += 1
            raise ValueError("remote image failed")
        kwargs = dict(state=state, request_id="req_fail", owner="k",
                      final_text="original", base_url="https://origin.example", run=fail)
        for _ in range(2):
            with pytest.raises(ValueError, match="remote image failed"):
                await archive_once(**kwargs)
        assert calls == 1
    asyncio.run(scenario())


def test_formal_stream_and_nonstream_archive_share_broker_state():
    from app.main import create_app
    src = inspect.getsource(create_app)
    assert "return await archive_once(" in src
    assert "async def persist_broker_terminal(" in src
    assert "async def persist_detached_nonstream(" in src
    assert src.count("await archive_verified_terminal(") >= 4
    assert "asyncio.shield(task)" in inspect.getsource(archive_once)
