from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from . import __version__ as PACKAGE_VERSION
from .live_voice_patch import LIVE_PROTOCOL_VERSION


# These values describe different compatibility surfaces on purpose. Do not
# collapse them into a single version number: package releases, the layered
# server runtime/console, the Chrome Bridge, and the realtime wire protocol can
# evolve independently.
SERVER_RUNTIME_VERSION = "0.21.4"
CHROME_BRIDGE_VERSION = "0.7.7"
PRODUCTION_ENTRYPOINT = "app.entry:app"
VERSION_CONTRACT_VERSION = 1


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
        },
        "chrome_bridge": {
            "version": CHROME_BRIDGE_VERSION,
        },
        "protocols": {
            "realtime_voice": LIVE_PROTOCOL_VERSION,
        },
    }


def install_runtime_contract(app: FastAPI) -> FastAPI:
    if getattr(app.state, "runtime_contract_installed", False):
        return app

    app.state.runtime_contract_installed = True

    @app.get("/version", tags=["system"])
    async def version_contract() -> dict[str, Any]:
        return version_contract_payload(app)

    return app
