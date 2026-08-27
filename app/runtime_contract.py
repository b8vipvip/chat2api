from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import __version__ as PACKAGE_VERSION
from .live_voice_patch import LIVE_PROTOCOL_VERSION


# These values describe different compatibility surfaces on purpose. Do not
# collapse them into a single version number: package releases, the layered
# server runtime/console, the Chrome Bridge wire protocol, the shipped unpacked
# extension bundle, and the realtime wire protocol can evolve independently.
SERVER_RUNTIME_VERSION = "0.22.23"
CHROME_BRIDGE_VERSION = "0.8.1"
CHROME_BRIDGE_BUNDLE_VERSION = "0.8.4"
PRODUCTION_ENTRYPOINT = "app.entry:app"
VERSION_CONTRACT_VERSION = 1
RUNTIME_FEATURE_REVISION = "capacity-native-v37-bundle-084-runtime-logs-v1-playground-lifecycle-v1-spare-freshness-v39-response-capture-v41"
ADMIN_VERSION_ASSET = "/assets/chat2api-runtime-version.js"
ADMIN_EXTENSION_COLUMNS_ASSET = "/assets/chat2api-extension-columns.js"
ADMIN_LINUX_WORKERS_ASSET = "/assets/chat2api-linux-workers.js"
ADMIN_LINUX_PROXY_CATALOG_ASSET = "/assets/chat2api-linux-worker-proxy-catalog.js"


def version_contract_payload(app: FastAPI) -> dict[str, Any]:
    runtime_version = str(getattr(app, "version", "") or SERVER_RUNTIME_VERSION)
    return {
        "object": "chat2api.version",
        "contract_version": VERSION_CONTRACT_VERSION,
        "server": {
            "package_version": PACKAGE_VERSION,
            "runtime_version": runtime_version,
            "expected_runtime_version": SERVER_RUNTIME_VERSION,
            "entrypoint": PRODUCTION_ENTRYPOINT,
            "runtime_aligned": runtime_version == SERVER_RUNTIME_VERSION,
            "feature_revision": RUNTIME_FEATURE_REVISION,
        },
        "chrome_bridge": {
            "version": CHROME_BRIDGE_VERSION,
            "bundle_version": CHROME_BRIDGE_BUNDLE_VERSION,
            "build_revision": "capacity-native-v37-r2-spare-freshness-v39-response-capture-v41",
            "capacity_control_version": 36,
            "capacity_reporter_version": 37,
        },
        "features": {
            "runtime_logs": True,
            "runtime_log_export": True,
            "native_capacity_control": True,
            "worker_extension_runtime_diagnostics": True,
            "bridge_service_worker_cache_bust": True,
            "persistent_playground_runs": True,
            "running_request_history": True,
            "playground_cancellation": True,
            "generation_activity_watchdog": True,
            "fresh_spare_rotation": True,
            "terminal_request_recovery": True,
            "failed_route_recycle": True,
            "rendered_response_capture_recovery": True,
        },
        "protocols": {
            "realtime_voice": LIVE_PROTOCOL_VERSION,
        },
    }


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


async def runtime_version_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Chat2API-Runtime"] = SERVER_RUNTIME_VERSION
    response.headers["X-Chat2API-Chrome-Bridge"] = CHROME_BRIDGE_VERSION
    response.headers["X-Chat2API-Chrome-Bridge-Bundle"] = CHROME_BRIDGE_BUNDLE_VERSION
    return response


def install_runtime_contract(app: FastAPI) -> None:
    app.middleware("http")(runtime_version_middleware)

    @app.get("/version", include_in_schema=False)
    async def runtime_version():
        return JSONResponse(version_contract_payload(app))

    @app.get(ADMIN_VERSION_ASSET, include_in_schema=False)
    async def admin_runtime_version_asset():
        payload = json.dumps(version_contract_payload(app), ensure_ascii=False, separators=(",", ":"))
        script = f"window.__CHAT2API_RUNTIME_CONTRACT__={payload};"
        return Response(content=script, media_type="application/javascript; charset=utf-8")
