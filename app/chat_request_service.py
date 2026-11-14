"""Shared Mode 2 request dispatch boundary.

Only owns admission to the existing RequestBroker and the construction/sending
of Worker chat.request commands. It deliberately does not own authentication,
routing policy, terminal transitions, SSE, cancellation, archives or billing.
The API adapter and future Web BFF remain responsible for those concerns.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .broker import RequestBroker, RequestState
from .registry import ClientRegistry


@dataclass(frozen=True)
class ChatDispatch:
    """Detached Worker command snapshot, never a broker terminal."""

    request_id: str
    prompt: str
    attachments: tuple[dict[str, object], ...]
    options: dict[str, Any]

    @classmethod
    def build(
        cls,
        request_id: str,
        prompt: str,
        attachments: Sequence[Mapping[str, object]],
        options: Mapping[str, Any],
    ) -> "ChatDispatch":
        return cls(
            request_id=request_id,
            prompt=prompt,
            attachments=tuple(deepcopy(dict(value)) for value in attachments),
            options=deepcopy(dict(options)),
        )

    def worker_message(self) -> dict[str, object]:
        return {
            "type": "chat.request",
            "request_id": self.request_id,
            "prompt": self.prompt,
            "attachments": deepcopy(list(self.attachments)),
            "options": deepcopy(self.options),
        }


class ChatRequestService:
    """One shared admission/dispatch seam used by Chat Completions and Responses.

    This service intentionally owns *neither* Worker scheduling decisions nor
    broker finality. Request IDs, Worker selection and per-request diagnostics
    are established by the calling API adapter before reserve(). The existing
    broker is the sole owner of the request lease and terminal state.
    """

    def __init__(self, registry: ClientRegistry, broker: RequestBroker) -> None:
        self.registry = registry
        self.broker = broker

    async def reserve(self, request_id: str, client_id: str) -> RequestState:
        return await self.broker.create(request_id, client_id)

    async def dispatch(
        self,
        client_id: str,
        *,
        request_id: str,
        prompt: str,
        attachments: Sequence[Mapping[str, object]],
        options: Mapping[str, Any],
    ) -> None:
        # A Worker command must be backed by the exact live Broker lease. This
        # prevents a future adapter/BFF from dispatching an untracked request or
        # accidentally sending a reserved request to a different Worker.
        state = self.broker.requests.get(request_id)
        if state is None:
            raise RuntimeError("request must be reserved before Worker dispatch")
        if state.client_id != client_id:
            raise RuntimeError("request lease belongs to a different Worker")
        if state.final_future.done():
            raise RuntimeError("request is already terminal and cannot be dispatched")

        command = ChatDispatch.build(request_id, prompt, attachments, options)
        await self.registry.send(client_id, command.worker_message())
