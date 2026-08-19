#!/usr/bin/env python3
"""Outbound-only Linux Worker agent. It intentionally has no HTTP listener."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import websockets

from linux_worker_proxy import ProxyConfigError, build_xray_config
from linux_worker_remote_login import capture_frame, close_session, inject_worker_binding, open_session, send_input


AGENT_VERSION = "0.3.2"
CONFIG = Path(os.environ.get("CHAT2API_WORKER_CONFIG", "/etc/chat2api-worker/worker.json"))
XRAY_CONFIG = Path(os.environ.get("CHAT2API_XRAY_CONFIG", "/etc/chat2api-worker/xray.json"))
PROXY_APPLY_HELPER = Path(os.environ.get("CHAT2API_PROXY_APPLY_HELPER", "/usr/local/sbin/chat2api-worker-proxy-apply"))
PROXY_PORT = int(os.environ.get("CHAT2API_PROXY_PORT", "10808"))
PROXY_TEST_URL = os.environ.get("CHAT2API_PROXY_TEST_URL", "https://chatgpt.com/")
REPO_DIR = Path(__file__).resolve().parents[1]
MANIFEST = REPO_DIR / "chrome_extension" / "manifest.json"
HEARTBEAT_SECONDS = 15.0
BINDING_RETRY_SECONDS = 20.0
BINDING_POST_INJECT_SECONDS = 12.0
BINDING_BOUND_POLL_SECONDS = 60.0
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
    "login_session_frame",
    "login_session_input",
    "get_logs",
    "reconcile_reserve_pool",
}
IMPLEMENTED_COMMANDS = {
    "health_check",
    *ALLOWED_UNITS,
    "test_proxy",
    "apply_proxy_config",
    "open_login_session",
    "close_login_session",
    "login_session_frame",
    "login_session_input",
}


def service_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def _manifest_version() -> str:
    try:
        return str(json.loads(MANIFEST.read_text(encoding="utf-8")).get("version") or "")[:40]
    except Exception:
        return ""


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
    if not proxy_configured():
        return {"ok": False, "http_status": "000", "error": "proxy_not_configured"}
    if not service_active("chat2api-xray.service"):
        return {"ok": False, "http_status": "000", "error": "xray_not_running"}
    try:
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
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "http_status": "000", "error": "proxy_test_command_failed"}
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
        "agent_version": AGENT_VERSION,
        "chrome_bridge_version": _manifest_version(),
        "status": status,
        "proxy_status": "error" if not services["xray"] else ("connected" if has_proxy else "waiting"),
        "metadata": metadata,
    }


def _helper_failure_name(returncode: int, stderr: str) -> str:
    text = str(stderr or "").lower()
    if "not allowed to execute" in text or "may not run sudo" in text or "not in the sudoers" in text:
        return "proxy_helper_not_authorized"
    if "no new privileges" in text:
        return "proxy_helper_privilege_blocked"
    if "no such file" in text or returncode == 127:
        return "proxy_helper_missing"
    if "permission denied" in text:
        return "proxy_helper_permission_denied"
    return "proxy_helper_failed"


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
    except OSError:
        return {"ok": False, "error": "proxy_helper_launch_failed"}

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
        error = str(helper.get("error") or _helper_failure_name(result.returncode, result.stderr))[:120]
        response = {
            "ok": False,
            "error": error,
            "rolled_back": bool(helper.get("rolled_back")),
            "helper_exit_code": int(helper.get("exit_code") or result.returncode or 0),
        }
        stage = str(helper.get("stage") or "")[:80]
        if stage:
            response["stage"] = stage
        return response

    return {
        "ok": True,
        "proxy": summary,
        "test": {"http_status": str(helper.get("http_status") or "")[:8]},
        "health": health(),
    }


def run_allowed(command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if command not in ALLOWED_COMMANDS:
        return {"ok": False, "error": "command_not_allowed"}
    args = dict(arguments or {})
    if command == "health_check":
        return {"ok": True, "health": health()}
    if command == "test_proxy":
        return _proxy_test()
    if command == "apply_proxy_config":
        return _apply_proxy(args)
    if command == "open_login_session":
        # The authenticated server endpoint performs a live proxy test immediately
        # before this command. Honor that short-lived preflight only while a real
        # non-freedom proxy config is still present. Direct command callers still
        # have to pass a Worker-side connectivity test.
        if args.get("proxy_prevalidated") is True and proxy_configured():
            return open_session()
        proxy = _proxy_test()
        if not proxy.get("ok"):
            return {"ok": False, "error": "proxy_required_for_login", "proxy": proxy}
        return open_session()
    if command == "close_login_session":
        return close_session()
    if command == "login_session_frame":
        return capture_frame()
    if command == "login_session_input":
        return send_input(args)
    if command in ALLOWED_UNITS:
        result = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "restart", ALLOWED_UNITS[command]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": result.returncode == 0, "error": result.stderr[-500:] if result.returncode else None}
    return {"ok": False, "error": "not_implemented"}


def _server_from_websocket(websocket_url: str) -> str:
    parsed = urlsplit(str(websocket_url or ""))
    scheme = "https" if parsed.scheme == "wss" else "http" if parsed.scheme == "ws" else ""
    if not scheme or not parsed.netloc:
        raise ValueError("Invalid Worker websocket URL")
    return urlunsplit((scheme, parsed.netloc, "", "", "")).rstrip("/")


def _request_binding_ticket(config: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch one Worker-bound ticket without ever logging Worker credentials or ticket data."""
    try:
        server = _server_from_websocket(str(config.get("websocket_url") or ""))
        request = Request(
            f"{server}/api/workers/extension-binding-ticket",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Worker-ID": str(config.get("worker_id") or ""),
                "X-Worker-Token": str(config.get("worker_token") or ""),
            },
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(131072).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, HTTPError, URLError, json.JSONDecodeError):
        return None


async def _binding_loop(config: dict[str, Any]) -> None:
    """Keep explicit Worker↔Bridge identity healthy for the lifetime of the Agent.

    A fresh Worker intentionally stays on about:blank until a real proxy node is
    configured and can reach ChatGPT. Only then may the binding flow restore a
    ChatGPT tab, preventing pre-login direct-network navigation.
    """
    while True:
        payload = await asyncio.to_thread(_request_binding_ticket, config)
        if payload and payload.get("bound") is True:
            await asyncio.sleep(BINDING_BOUND_POLL_SECONDS)
            continue
        if not proxy_configured():
            await asyncio.sleep(BINDING_RETRY_SECONDS)
            continue
        proxy = await asyncio.to_thread(_proxy_test)
        if not proxy.get("ok"):
            await asyncio.sleep(BINDING_RETRY_SECONDS)
            continue
        ticket = str((payload or {}).get("ticket") or "")
        server_url = str((payload or {}).get("server_url") or "")
        if ticket and server_url and service_active("chat2api-chrome.service"):
            result = await asyncio.to_thread(inject_worker_binding, ticket, server_url)
            if result.get("ok"):
                await asyncio.sleep(BINDING_POST_INJECT_SECONDS)
                continue
        await asyncio.sleep(BINDING_RETRY_SECONDS)


async def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    headers = {"X-Worker-ID": config["worker_id"], "X-Worker-Token": config["worker_token"]}
    delay = 2
    while True:
        binding_task: asyncio.Task | None = None
        try:
            async with websockets.connect(config["websocket_url"], additional_headers=headers, ping_interval=20) as ws:
                delay = 2
                binding_task = asyncio.create_task(_binding_loop(config))
                next_heartbeat = 0.0
                while True:
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        await ws.send(json.dumps({"type": "heartbeat", "data": health()}))
                        next_heartbeat = now + HEARTBEAT_SECONDS

                    timeout = max(0.05, next_heartbeat - time.monotonic())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue

                    message = json.loads(raw)
                    if message.get("type") != "command":
                        continue
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
        except Exception as exc:
            print(f"worker connection unavailable: {type(exc).__name__}", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        finally:
            if binding_task:
                binding_task.cancel()
                try:
                    await binding_task
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    asyncio.run(main())
