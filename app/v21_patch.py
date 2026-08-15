from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .broker import RequestState


PATCH_VERSION = "0.21.0"
CAPACITY_UNITS = 3
CAPACITY_WAIT_SECONDS = 1.5


def request_weight(request_id: str) -> int:
    value = str(request_id or "")
    if value.startswith(("imgreq_", "voicereq_", "live_")):
        return 2
    return 1


class _CapacityBusyView:
    """Compatibility view for legacy code that still checks registry.busy_clients.

    Membership means that the extension has no room for another weight-1 request.
    add()/discard() intentionally become no-ops because broker admission is now the
    source of truth instead of a single boolean client busy flag.
    """

    def __init__(self, broker) -> None:
        self.broker = broker

    def __contains__(self, client_id: object) -> bool:
        return not self.broker.can_accept(str(client_id), 1)

    def __iter__(self):
        for client_id in list(self.broker.client_active_requests):
            if client_id in self:
                yield client_id

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def add(self, _client_id: str) -> None:
        return None

    def discard(self, _client_id: str) -> None:
        return None

    def clear(self) -> None:
        return None


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_v21_patch(app: FastAPI) -> FastAPI:
    broker = app.state.broker
    registry = app.state.registry
    app.version = PATCH_VERSION

    # Keep the historical client_requests mapping as a first-request compatibility
    # index while tracking all active requests separately for real concurrency.
    broker.client_active_requests = getattr(broker, "client_active_requests", {})
    broker.capacity_units = CAPACITY_UNITS
    broker.capacity_wait_seconds = CAPACITY_WAIT_SECONDS
    broker._chat2api_v21_condition = asyncio.Condition(broker.lock)

    def used_units(client_id: str) -> int:
        active = broker.client_active_requests.get(str(client_id), {})
        return sum(int(weight) for weight in active.values())

    def can_accept(client_id: str, weight: int = 1) -> bool:
        weight = max(1, int(weight or 1))
        return used_units(client_id) + weight <= CAPACITY_UNITS

    def active_request_ids(client_id: str) -> list[str]:
        return list(broker.client_active_requests.get(str(client_id), {}))

    def capacity_snapshot(client_id: str) -> dict[str, Any]:
        active = broker.client_active_requests.get(str(client_id), {})
        used = used_units(client_id)
        return {
            "limit_units": CAPACITY_UNITS,
            "used_units": used,
            "available_units": max(0, CAPACITY_UNITS - used),
            "active_requests": len(active),
            "request_weights": dict(active),
        }

    broker.client_used_units = used_units
    broker.can_accept = can_accept
    broker.active_request_ids = active_request_ids
    broker.capacity_snapshot = capacity_snapshot

    async def create_concurrent(request_id: str, client_id: str):
        request_id = str(request_id)
        client_id = str(client_id)
        weight = request_weight(request_id)
        started = time.perf_counter()
        condition = broker._chat2api_v21_condition
        deadline = asyncio.get_running_loop().time() + CAPACITY_WAIT_SECONDS

        async with condition:
            while not can_accept(client_id, weight):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "extension capacity exhausted "
                        f"(used={snapshot['used_units']}/{CAPACITY_UNITS}, requested_weight={weight})"
                    )
                try:
                    await asyncio.wait_for(condition.wait(), timeout=remaining)
                except asyncio.TimeoutError as error:
                    snapshot = capacity_snapshot(client_id)
                    raise RuntimeError(
                        "extension capacity exhausted "
                        f"(used={snapshot['used_units']}/{CAPACITY_UNITS}, requested_weight={weight})"
                    ) from error

            if request_id in broker.requests:
                raise RuntimeError(f"Duplicate request_id: {request_id}")
            loop = asyncio.get_running_loop()
            state = RequestState(
                request_id=request_id,
                client_id=client_id,
                final_future=loop.create_future(),
            )
            before = used_units(client_id)
            broker.requests[request_id] = state
            broker.client_active_requests.setdefault(client_id, {})[request_id] = weight
            broker.client_requests.setdefault(client_id, request_id)
            state.diagnostics.update({
                "extension_capacity_limit_units": CAPACITY_UNITS,
                "extension_capacity_weight": weight,
                "extension_capacity_used_before": before,
                "extension_capacity_used_after": before + weight,
                "extension_capacity_wait_ms": round((time.perf_counter() - started) * 1000, 1),
                "extension_concurrency_v21": True,
            })

        # Preserve the v19 non-stream disconnect tracker even though v21 replaces
        # broker.create after v19 was installed.
        tracked = getattr(broker, "_chat2api_v19_tracked_states", None)
        if isinstance(tracked, dict):
            try:
                from .v19_patch import _http_request_marker
                marker = _http_request_marker.get()
                if marker:
                    tracked[marker] = state
            except Exception:
                pass
        return state

    async def release_concurrent(request_id: str) -> None:
        condition = broker._chat2api_v21_condition
        async with condition:
            state = broker.requests.pop(request_id, None)
            if state and state.final_future and state.final_future.done() and not state.final_future.cancelled():
                try:
                    state.final_future.exception()
                except asyncio.CancelledError:
                    pass
            if state:
                active = broker.client_active_requests.get(state.client_id)
                if isinstance(active, dict):
                    active.pop(request_id, None)
                    if not active:
                        broker.client_active_requests.pop(state.client_id, None)
                if broker.client_requests.get(state.client_id) == request_id:
                    replacement = next(iter(broker.client_active_requests.get(state.client_id, {})), None)
                    if replacement:
                        broker.client_requests[state.client_id] = replacement
                    else:
                        broker.client_requests.pop(state.client_id, None)
            condition.notify_all()

    broker.create = create_concurrent
    broker.release = release_concurrent
    broker._chat2api_v21_weighted_capacity = True

    # Legacy routes and patches still call add()/discard(). Replace that boolean set
    # with a capacity-aware compatibility view so those calls cannot collapse the
    # new multi-request accounting back to a single busy flag.
    registry.busy_clients = _CapacityBusyView(broker)

    base_summaries = registry.summaries
    if not getattr(registry, "_chat2api_v21_summaries", False):
        def summaries_with_capacity() -> list[dict[str, Any]]:
            rows = base_summaries()
            for row in rows:
                client_id = str(row.get("client_id") or "")
                snapshot = capacity_snapshot(client_id)
                row["busy"] = snapshot["used_units"] > 0
                row["capacity"] = snapshot
            return rows

        registry.summaries = summaries_with_capacity
        registry._chat2api_v21_summaries = True

    base_detach = registry.detach
    if not getattr(registry, "_chat2api_v21_detach", False):
        async def detach_all_requests(client_id: str, websocket) -> None:
            request_ids = active_request_ids(client_id)
            await base_detach(client_id, websocket)
            for request_id in request_ids:
                await broker.publish(request_id, {
                    "type": "chat.error",
                    "request_id": request_id,
                    "error": "Chrome extension disconnected",
                })

        registry.detach = detach_all_requests
        registry._chat2api_v21_detach = True

    @app.get("/assets/chat2api-v21.js")
    async def admin_v21_js() -> Response:
        path = Path(__file__).with_name("admin_v21.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v21_capacity_and_docs(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        raw = None
        if response.status_code == 409 and "application/json" in content_type:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = None
            detail = str(payload.get("detail") or "") if isinstance(payload, dict) else ""
            if detail.startswith("extension capacity exhausted"):
                return JSONResponse(
                    {"detail": detail, "code": "extension_capacity_exhausted", "retryable": True},
                    status_code=429,
                    headers={"Retry-After": "1", "Cache-Control": "no-store"},
                )

        if "application/json" in content_type and (
            path in {"/", "/healthz", "/api/admin/overview"}
            or path.startswith("/api/admin/")
        ):
            if raw is None:
                raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if "server_version" in payload or path.endswith("/log"):
                    payload["server_version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities.update({
                            "extension_weighted_concurrency": True,
                            "extension_capacity_units": CAPACITY_UNITS,
                            "extension_capacity_wait_ms": int(CAPACITY_WAIT_SECONDS * 1000),
                            "text_request_weight": 1,
                            "image_request_weight": 2,
                            "voice_request_weight": 2,
                            "live_voice_request_weight": 2,
                            "live_voice_text_input": True,
                        })
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            if raw is None:
                raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v21.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
