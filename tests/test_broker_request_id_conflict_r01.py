"""Request IDs are unique while a Broker request is active."""
from __future__ import annotations

import asyncio

import pytest

from app.broker import RequestBroker


def test_same_request_id_cannot_be_bound_to_two_workers_concurrently() -> None:
    async def exercise() -> None:
        broker = RequestBroker()
        first = await broker.create("req_shared_identity", "worker_a")

        with pytest.raises(RuntimeError, match="request ID is already active"):
            await broker.create("req_shared_identity", "worker_b")

        assert broker.requests["req_shared_identity"] is first
        assert broker.client_requests == {"worker_a": "req_shared_identity"}

        # A different request may proceed on a different worker while the
        # original identity remains active.
        second = await broker.create("req_other_identity", "worker_b")
        assert broker.requests["req_other_identity"] is second

    asyncio.run(exercise())
