#!/usr/bin/env python3
"""Outbound-only Linux Worker agent. It intentionally has no HTTP listener."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import subprocess
from pathlib import Path

import websockets


CONFIG = Path(os.environ.get("CHAT2API_WORKER_CONFIG", "/etc/chat2api-worker/worker.json"))
ALLOWED_UNITS = {"restart_chrome": "chat2api-chrome.service", "restart_xray": "chat2api-xray.service", "restart_xvfb": "chat2api-xvfb.service"}
ALLOWED_COMMANDS = {"health_check", *ALLOWED_UNITS, "reload_extension", "test_proxy", "apply_proxy_config", "open_login_session", "close_login_session", "get_logs", "reconcile_reserve_pool"}


def service_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def health() -> dict:
    return {"hostname": socket.gethostname(), "platform": "linux", "arch": platform.machine(), "os_version": platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"), "agent_version": "0.1.0", "status": "waiting_login" if service_active("chat2api-chrome.service") else "degraded", "proxy_status": "connected" if service_active("chat2api-xray.service") else "error", "metadata": {"services": {name: service_active(f"chat2api-{name}.service") for name in ("xray", "xvfb", "chrome")}}}


def run_allowed(command: str) -> dict:
    if command not in ALLOWED_COMMANDS:
        return {"ok": False, "error": "command_not_allowed"}
    if command in ALLOWED_UNITS:
        result = subprocess.run(["sudo", "/bin/systemctl", "restart", ALLOWED_UNITS[command]], capture_output=True, text=True, timeout=30)
        return {"ok": result.returncode == 0, "error": result.stderr[-500:] if result.returncode else None}
    return {"ok": True, "health": health()}


async def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    headers = {"X-Worker-ID": config["worker_id"], "X-Worker-Token": config["worker_token"]}
    delay = 2
    while True:
        try:
            async with websockets.connect(config["websocket_url"], additional_headers=headers, ping_interval=20) as ws:
                delay = 2
                while True:
                    await ws.send(json.dumps({"type": "heartbeat", "data": health()}))
                    try:
                        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                        if message.get("type") == "command":
                            result = await asyncio.to_thread(run_allowed, str(message.get("command", "")))
                            await ws.send(json.dumps({"type": "command.result", "request_id": message.get("request_id"), "result": result}))
                    except asyncio.TimeoutError:
                        pass
                    await asyncio.sleep(15)
        except Exception as exc:
            print(f"worker connection unavailable: {type(exc).__name__}", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
