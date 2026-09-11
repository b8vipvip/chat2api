#!/usr/bin/env python3
"""Run one isolated chat2api Worker slot on a shared Linux host.

The canonical linux_worker_agent keeps legacy unit names for slot 1.  This
wrapper remaps only host-local service names while preserving the Worker wire
protocol, binding flow, proxy parser, and remote-login implementation.
"""
from __future__ import annotations

import asyncio
import os


def _slot() -> int:
    try:
        value = int(os.environ.get("CHAT2API_WORKER_SLOT", "0"))
    except ValueError as exc:
        raise SystemExit("CHAT2API_WORKER_SLOT must be an integer") from exc
    if not 2 <= value <= 32:
        raise SystemExit("CHAT2API_WORKER_SLOT must be between 2 and 32")
    return value


SLOT = _slot()

# Import after validating the slot. The base module and its remote-login helper
# read all per-instance paths/ports from the systemd environment at import time.
import linux_worker_agent as agent  # noqa: E402

_SUFFIX = f"slot{SLOT}"
_UNIT_MAP = {
    "chat2api-xray.service": f"chat2api-xray-{_SUFFIX}.service",
    "chat2api-xvfb.service": f"chat2api-xvfb-{_SUFFIX}.service",
    "chat2api-chrome.service": f"chat2api-chrome-{_SUFFIX}.service",
}
_base_service_active = agent.service_active
_base_build_xray_config = agent.build_xray_config


def service_active(unit: str) -> bool:
    return _base_service_active(_UNIT_MAP.get(str(unit), str(unit)))


def build_xray_config(share_link: str):
    """Reuse the canonical parser but bind this slot's private SOCKS port."""
    config, summary = _base_build_xray_config(share_link)
    inbounds = config.get("inbounds") if isinstance(config, dict) else None
    if not isinstance(inbounds, list) or not inbounds or not isinstance(inbounds[0], dict):
        raise RuntimeError("canonical Xray config is missing the SOCKS inbound")
    inbounds[0]["listen"] = "127.0.0.1"
    inbounds[0]["port"] = int(agent.PROXY_PORT)
    return config, summary


agent.service_active = service_active
agent.build_xray_config = build_xray_config
agent.ALLOWED_UNITS = {
    "restart_chrome": _UNIT_MAP["chat2api-chrome.service"],
    "restart_xray": _UNIT_MAP["chat2api-xray.service"],
    "restart_xvfb": _UNIT_MAP["chat2api-xvfb.service"],
}


if __name__ == "__main__":
    asyncio.run(agent.main())
