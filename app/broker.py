from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestState:
    request_id: str
    client_id: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    final_future: asyncio.Future[str] | None = None
    text: str = ""


class RequestBroker:
    def __init__(self) -> None:
        self.requests: dict[str, RequestState] = {}
        self.client_requests: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def create(self, request_id: str, client_id: str) -> RequestState:
        async with self.lock:
            if client_id in self.client_requests:
                raise RuntimeError("The selected extension is busy with another request")
            loop = asyncio.get_running_loop()
            state = RequestState(request_id=request_id, client_id=client_id, final_future=loop.create_future())
            self.requests[request_id] = state
            self.client_requests[client_id] = request_id
            return state

    async def release(self, request_id: str) -> None:
        async with self.lock:
            state = self.requests.pop(request_id, None)
            if state and state.final_future and state.final_future.done() and not state.final_future.cancelled():
                # Streaming requests use the event queue and never await final_future.
                # Reading the exception here prevents an unhandled-future warning.
                state.final_future.exception()
            if state and self.client_requests.get(state.client_id) == request_id:
                self.client_requests.pop(state.client_id, None)

    async def publish(self, request_id: str, event: dict[str, Any]) -> bool:
        state = self.requests.get(request_id)
        if not state:
            return False
        event_type = event.get("type")
        if event_type == "chat.delta":
            delta = str(event.get("delta") or "")
            state.text += delta
        elif event_type == "chat.snapshot":
            state.text = str(event.get("text") or state.text)
        elif event_type == "chat.completed":
            final = str(event.get("text") or state.text)
            state.text = final
            if state.final_future and not state.final_future.done():
                state.final_future.set_result(final)
        elif event_type in {"chat.error", "chat.cancelled"}:
            message = str(event.get("error") or event.get("reason") or "Request failed")
            if state.final_future and not state.final_future.done():
                state.final_future.set_exception(RuntimeError(message))
        await state.queue.put(event)
        return True
