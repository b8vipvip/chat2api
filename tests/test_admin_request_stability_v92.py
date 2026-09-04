from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, Query
from fastapi.routing import APIRoute

from app.request_device_identity_patch import _install_request_route_stability


class AsyncTelemetry:
    async def query(self, *, limit=50, offset=0, status=None, model=None, key_id=None, q=None):
        await asyncio.sleep(0)
        return {
            "data": [{"request_id": "req_async", "client_id": "ext_test"}],
            "total": 1,
            "limit": limit,
            "offset": offset,
        }

    async def summary(self):
        await asyncio.sleep(0)
        return {"requests": 1}

    async def get(self, request_id: str):
        await asyncio.sleep(0)
        if request_id == "req_async":
            return {"request_id": request_id, "client_id": "ext_test"}
        return None


def route_for(app: FastAPI, path: str) -> APIRoute:
    return next(route for route in app.routes if isinstance(route, APIRoute) and route.path == path)


def test_request_history_final_owner_accepts_async_telemetry_contract() -> None:
    telemetry = AsyncTelemetry()
    app = FastAPI()
    app.state.telemetry = telemetry

    @app.get("/api/admin/requests")
    async def legacy_requests(
        limit: int = Query(default=50),
        offset: int = Query(default=0),
        status_filter: str | None = Query(default=None, alias="status"),
        model: str | None = Query(default=None),
        key_id: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ):
        # This intentionally reproduces the historical crash contract: query()
        # became async while the endpoint still unpacked it synchronously.
        result = telemetry.query(limit=limit, offset=offset, status=status_filter, model=model, key_id=key_id, q=q)
        return {**result, "summary": telemetry.summary()}

    @app.get("/api/admin/requests/{request_id}")
    async def legacy_detail(request_id: str):
        return telemetry.get(request_id)

    _install_request_route_stability(app, telemetry)

    request_call = route_for(app, "/api/admin/requests").dependant.call
    assert getattr(request_call, "__chat2api_request_stability_v92__", False)
    payload = asyncio.run(
        request_call(
            limit=100,
            offset=0,
            status_filter=None,
            model=None,
            key_id=None,
            q=None,
        )
    )
    assert payload["total"] == 1
    assert payload["data"][0]["request_id"] == "req_async"
    assert payload["summary"] == {"requests": 1}

    detail_call = route_for(app, "/api/admin/requests/{request_id}").dependant.call
    detail = asyncio.run(detail_call(request_id="req_async"))
    assert detail["request_id"] == "req_async"


def test_request_identity_query_wrapper_preserves_awaitable_base_contract() -> None:
    source = __import__("pathlib").Path("app/request_device_identity_patch.py").read_text(encoding="utf-8")
    assert "inspect.isawaitable(raw)" in source
    assert "async def resolve_query()" in source
    assert "async def resolve_get()" in source
    assert "maps = device_maps()" in source
