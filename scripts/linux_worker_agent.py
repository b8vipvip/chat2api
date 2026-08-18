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
from typing import Any

import websockets

from linux_worker_proxy import ProxyConfigError, build_xray_config


CONFIG = Path(os.environ.get("CHAT2API_WORKER_CONFIG", "/etc/chat2api-worker/worker.json"))
XRAY_CONFIG = Path(os.environ.get("CHAT2API_XRAY_CONFIG", "/etc/chat2api-worker/xray.json"))
PROXY_APPLY_HELPER = Path(os.environ.get("CHAT2API_PROXY_APPLY_HELPER", "/usr/local/sbin/chat2api-worker-proxy-apply"))
PROXY_PORT = int(os.environ.get("CHAT2API_PROXY_PORT", "10808"))
PROXY_TEST_URL = os.environ.get("CHAT2API_PROXY_TEST_URL", "https://chatgpt.com/")
ALLOWED_UNITS = {
    "restart_chrome": "chat2api-chrome.service",
    "restart_xray": "chat2api-xray.service",
    "restart_xvfb": "chat2api-xvfb.service",
}
ALLOWED_COMMANDS = {
    "health_check",
    *ALLOWED_UNITS,
    "reload_extension",
    "test_proxy",
    "apply_proxy_config",
    "open_login_session",
    "close_login_session",
    "get_logs",
    "reconcile_reserve_pool",
}
IMPLEMENTED_COMMANDS = {"health_check", *ALLOWED_UNITS, "test_proxy", "apply_proxy_config"}


def service_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def _current_proxy_summary() -> dict[str, Any] | None:
    try:
        payload = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return None
    for outbound in payload.get("outbounds") or []:
        if not isinstance(outbound, dict):
            continue
        protocol = str(outbound.get("protocol") or "").lower()
        if not protocol or protocol in {"freedom", "blackhole", "dns"}:
            continue
        settings = outbound.get("settings") or {}
        endpoint: dict[str, Any] = {}
        if protocol in {"vless", "vmess"}:
            nodes = settings.get("vnext") or []
            endpoint = nodes[0] if nodes and isinstance(nodes[0], dict) else {}
        elif protocol in {"trojan", "shadowsocks"}:
            nodes = settings.get("servers") or []
            endpoint = nodes[0] if nodes and isinstance(nodes[0], dict) else {}
        return {
            "protocol": "ss" if protocol == "shadowsocks" else protocol,
            "server": str(endpoint.get("address") or "")[:200],
            "port": int(endpoint.get("port") or 0),
        }
    return None


def proxy_configured() -> bool:
    return _current_proxy_summary() is not None


def _proxy_test() -> dict[str, Any]:
    result = subprocess.run(
        [
            "curl",
            "--proxy",
            f"socks5h://127.0.0.1:{PROXY_PORT}",
            "--connect-timeout",
            "10",
            "--max-time",
            "20",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            PROXY_TEST_URL,
        ],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    code = (result.stdout or "").strip()
    reachable = result.returncode == 0 and bool(code) and code != "000"
    return {"ok": reachable, "http_status": code or "000", "error": None if reachable else "proxy_connectivity_test_failed"}


def health() -> dict[str, Any]:
    services = {name: service_active(f"chat2api-{name}.service") for name in ("xray", "xvfb", "chrome")}
    proxy_summary = _current_proxy_summary() if services["xray"] else None
    has_proxy = proxy_summary is not None
    if not services["xray"] or not services["xvfb"] or not services["chrome"]:
        status = "degraded"
    elif not has_proxy:
        status = "waiting_proxy"
    else:
        status = "waiting_login"
    metadata: dict[str, Any] = {"services": services}
    if proxy_summary:
        metadata["proxy_summary"] = proxy_summary
    return {
        "hostname": socket.gethostname(),
        "platform": "linux",
        "arch": platform.machine(),
        "os_version": platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"),
        "agent_version": "0.2.0",
        "status": status,
        "proxy_status": "error" if not services["xray"] else ("connected" if has_proxy else "waiting"),
        "metadata": metadata,
    }


def _apply_proxy(arguments: dict[str, Any]) -> dict[str, Any]:
    share_link = str(arguments.get("share_link") or "").strip()
    try:
        xray_config, summary = build_xray_config(share_link)
    except ProxyConfigError as exc:
        return {"ok": False, "error": "invalid_proxy_config", "detail": str(exc)[:240]}

    try:
        result = subprocess.run(
            ["sudo", "-n", str(PROXY_APPLY_HELPER)],
            input=json.dumps(xray_config, ensure_ascii=False, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "proxy_apply_timeout"}

    helper: dict[str, Any] = {}
    for line in reversed((result.stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            helper = value
            break
    if result.returncode != 0 or not helper.get("ok"):
        return {
            "ok": False,
            "error": str(helper.get("error") or "proxy_apply_failed")[:120],
            "rolled_back": bool(helper.get("rolled_back")),
        }
    return {
        "ok": True,
        "proxy": summary,
        "test": {"http_status": str(helper.get("http_status") or "")[:8]},
        "health": health(),
    }


def run_allowed(command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if command not in ALLOWED_COMMANDS:
        return {"ok": False, "error": "command_not_allowed"}
    if command == "health_check":
        return {"ok": True, "health": health()}
    if command == "test_proxy":
        return _proxy_test()
    if command == "apply_proxy_config":
        return _apply_proxy(dict(arguments or {}))
    if command in ALLOWED_UNITS:
        result = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "restart", ALLOWED_UNITS[command]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": result.returncode == 0, "error": result.stderr[-500:] if result.returncode else None}
    return {"ok": False, "error": "not_implemented"}


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
                            result = await asyncio.to_thread(
                                run_allowed,
                                str(message.get("command", "")),
                                dict(message.get("arguments") or {}),
                            )
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "command.result",
                                        "request_id": message.get("request_id"),
                                        "result": result,
                                    }
                                )
                            )
                    except asyncio.TimeoutError:
                        pass
                    await asyncio.sleep(15)
        except Exception as exc:
            print(f"worker connection unavailable: {type(exc).__name__}", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
