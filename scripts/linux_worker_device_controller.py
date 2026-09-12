#!/usr/bin/env python3
"""Single per-device Linux controller for all logical Workers.

Only this process owns the outbound control-plane websocket. Worker 2..N are
logical identities multiplexed over the primary socket; they share the device
proxy and pairing authority while keeping an independent Chrome profile/window.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import websockets

import linux_worker_agent_v44 as legacy

AGENT_VERSION = "0.3.9"
CONFIG = Path(os.environ.get("CHAT2API_WORKER_CONFIG", "/etc/chat2api-worker/worker.json"))
REGISTRY = Path(os.environ.get("CHAT2API_DEVICE_WORKERS", "/var/lib/chat2api-worker/controller/workers.json"))
CONTROLLER_HELPER = Path(os.environ.get("CHAT2API_DEVICE_CONTROLLER_HELPER", "/usr/local/sbin/chat2api-device-controller"))
REPO_DIR = Path(__file__).resolve().parents[1]
MANIFEST = REPO_DIR / "chrome_extension" / "manifest.json"
HEARTBEAT_SECONDS = 15.0
BINDING_RETRY_SECONDS = 20.0
BINDING_BOUND_POLL_SECONDS = 60.0
MAX_DIAGNOSTIC_CHARS = 450_000
REMOTE_MODULES: dict[int, tuple[Any, Any]] = {}


def _manifest_version() -> str:
    try:
        return str(json.loads(MANIFEST.read_text(encoding="utf-8")).get("version") or "")[:40]
    except Exception:
        return ""


def _server_from_websocket(websocket_url: str) -> str:
    parsed = urlsplit(str(websocket_url or ""))
    scheme = "https" if parsed.scheme == "wss" else "http" if parsed.scheme == "ws" else ""
    if not scheme or not parsed.netloc:
        raise ValueError("Invalid Worker websocket URL")
    return urlunsplit((scheme, parsed.netloc, "", "", "")).rstrip("/")


def _slot_layout(slot: int) -> dict[str, Any]:
    slot = int(slot)
    if slot < 1 or slot > 32:
        raise ValueError("worker_slot_out_of_range")
    if slot == 1:
        return {
            "slot": 1,
            "display": ":99",
            "cdp": 9222,
            "profile": "/home/chat2api/.config/chat2api-chrome-worker-01",
            "chrome_unit": "chat2api-chrome.service",
            "xvfb_unit": "chat2api-xvfb.service",
        }
    return {
        "slot": slot,
        "display": f":{98 + slot}",
        "cdp": 9221 + slot,
        "profile": f"/home/chat2api/.config/chat2api-chrome-worker-{slot:02d}",
        "chrome_unit": f"chat2api-chrome-slot{slot}.service",
        "xvfb_unit": f"chat2api-xvfb-slot{slot}.service",
    }


def _service_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def _registry_rows() -> list[dict[str, Any]]:
    try:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("workers") if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        worker_id = str(row.get("worker_id") or "")
        try:
            slot = int(row.get("worker_slot") or 0)
        except (TypeError, ValueError):
            continue
        if worker_id and 2 <= slot <= 32:
            result.append({"worker_id": worker_id, "worker_slot": slot, "device_name": str(row.get("device_name") or "")[:80]})
    return sorted(result, key=lambda item: int(item["worker_slot"]))


def _save_registry(rows: list[dict[str, Any]]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    temp = REGISTRY.with_suffix(".tmp")
    temp.write_text(json.dumps({"revision": 127, "workers": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(REGISTRY)


def _register_child(worker_id: str, slot: int, device_name: str) -> None:
    rows = [row for row in _registry_rows() if row["worker_id"] != worker_id and int(row["worker_slot"]) != slot]
    rows.append({"worker_id": worker_id, "worker_slot": slot, "device_name": device_name[:80]})
    _save_registry(rows)


def _target(config: dict[str, Any], worker_id: str) -> dict[str, Any] | None:
    primary_id = str(config.get("worker_id") or "")
    if worker_id == primary_id:
        return {"worker_id": primary_id, "worker_slot": 1, "device_name": str(config.get("extension_name") or "")}
    return next((row for row in _registry_rows() if row["worker_id"] == worker_id), None)


def _load_remote_modules(slot: int) -> tuple[Any, Any]:
    cached = REMOTE_MODULES.get(slot)
    if cached:
        return cached
    layout = _slot_layout(slot)
    remote_path = REPO_DIR / "scripts" / "linux_worker_remote_login.py"
    clipboard_path = REPO_DIR / "scripts" / "linux_worker_remote_clipboard_v45.py"
    remote_name = f"chat2api_remote_login_slot_{slot}"
    clipboard_name = f"chat2api_remote_clipboard_slot_{slot}"
    previous = {key: os.environ.get(key) for key in (
        "CHAT2API_LOGIN_DISPLAY", "CHAT2API_LOGIN_CHROME_PROFILE", "CHAT2API_LOGIN_CHROME_DEBUG_URL"
    )}
    os.environ["CHAT2API_LOGIN_DISPLAY"] = str(layout["display"])
    os.environ["CHAT2API_LOGIN_CHROME_PROFILE"] = str(layout["profile"])
    os.environ["CHAT2API_LOGIN_CHROME_DEBUG_URL"] = f"http://127.0.0.1:{layout['cdp']}"
    try:
        spec = importlib.util.spec_from_file_location(remote_name, remote_path)
        if not spec or not spec.loader:
            raise RuntimeError("remote_login_module_unavailable")
        remote = importlib.util.module_from_spec(spec)
        sys.modules[remote_name] = remote
        spec.loader.exec_module(remote)
        canonical = sys.modules.get("linux_worker_remote_login")
        sys.modules["linux_worker_remote_login"] = remote
        try:
            clip_spec = importlib.util.spec_from_file_location(clipboard_name, clipboard_path)
            if not clip_spec or not clip_spec.loader:
                raise RuntimeError("remote_clipboard_module_unavailable")
            clipboard = importlib.util.module_from_spec(clip_spec)
            sys.modules[clipboard_name] = clipboard
            clip_spec.loader.exec_module(clipboard)
        finally:
            if canonical is None:
                sys.modules.pop("linux_worker_remote_login", None)
            else:
                sys.modules["linux_worker_remote_login"] = canonical
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    REMOTE_MODULES[slot] = (remote, clipboard)
    return remote, clipboard


def _generation_health() -> dict[str, Any] | None:
    try:
        value = legacy._generation_health_state()
    except Exception:
        return None
    return dict(value) if isinstance(value, dict) else None


def _health(slot: int) -> dict[str, Any]:
    layout = _slot_layout(slot)
    xray = _service_active("chat2api-xray.service")
    xvfb = _service_active(str(layout["xvfb_unit"]))
    chrome = _service_active(str(layout["chrome_unit"]))
    try:
        proxy_summary = legacy.base._current_proxy_summary() if xray else None
    except Exception:
        proxy_summary = None
    has_proxy = isinstance(proxy_summary, dict)
    status = "degraded" if not (xray and xvfb and chrome) else ("waiting_login" if has_proxy else "waiting_proxy")
    metadata: dict[str, Any] = {"services": {"xray": xray, "xvfb": xvfb, "chrome": chrome}, "device_controller_revision": 127, "worker_slot": slot}
    if proxy_summary:
        metadata["proxy_summary"] = proxy_summary
    generation = _generation_health()
    if generation:
        metadata["generation_backend_health"] = generation
        if generation.get("fresh") is True and generation.get("ready") is False:
            status = "degraded"
    return {
        "hostname": socket.gethostname(),
        "platform": "linux",
        "arch": legacy.base.platform.machine(),
        "os_version": legacy.base.platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"),
        "agent_version": AGENT_VERSION,
        "chrome_bridge_version": _manifest_version(),
        "status": status,
        "proxy_status": "error" if not xray else ("connected" if has_proxy else "waiting"),
        "metadata": metadata,
    }


def _run_helper(action: str, slot: int, *, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", str(CONTROLLER_HELPER), action, str(int(slot))],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _diagnostics(slot: int) -> dict[str, Any]:
    try:
        result = _run_helper("diagnostics", slot, timeout=30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "diagnostics_timeout"}
    except OSError:
        return {"ok": False, "error": "diagnostics_helper_launch_failed"}
    if result.returncode != 0:
        return {"ok": False, "error": "diagnostics_helper_failed", "detail": (result.stderr or "")[-240:]}
    logs = str(result.stdout or "")
    if len(logs) > MAX_DIAGNOSTIC_CHARS:
        logs = "[chat2api] diagnostics truncated to newest output\n" + logs[-MAX_DIAGNOSTIC_CHARS:]
    return {"ok": True, "filename": f"chat2api-worker-slot{slot}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.log", "logs": logs, "truncated": False}


def _execute(config: dict[str, Any], target: dict[str, Any], command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    slot = int(target["worker_slot"])
    primary = slot == 1
    if command == "health_check":
        return {"ok": True, "health": _health(slot)}
    if command == "provision_worker":
        if not primary:
            return {"ok": False, "error": "controller_only_command"}
        worker_id = str(arguments.get("worker_id") or "")
        device_name = str(arguments.get("device_name") or "")[:80]
        try:
            child_slot = int(arguments.get("worker_slot") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_worker_slot"}
        if not worker_id.startswith("wrk_") or not (2 <= child_slot <= 32):
            return {"ok": False, "error": "invalid_worker_identity"}
        try:
            result = _run_helper("provision", child_slot, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "worker_provision_timeout"}
        if result.returncode != 0:
            return {"ok": False, "error": "worker_provision_failed", "detail": (result.stderr or result.stdout or "")[-300:]}
        _register_child(worker_id, child_slot, device_name)
        _load_remote_modules(child_slot)
        return {"ok": True, "worker_id": worker_id, "worker_slot": child_slot, "profile": _slot_layout(child_slot)["profile"]}
    if command == "test_proxy":
        return legacy.base._proxy_test()
    if command == "apply_proxy_config":
        return legacy.base._apply_proxy(arguments)
    if command == "upgrade_worker":
        if not primary:
            return {"ok": False, "error": "device_upgrade_via_primary_only"}
        return legacy._upgrade_worker()
    if command in {"initialize_worker", "restart_chrome", "restart_xvfb", "reload_extension"}:
        action = "initialize" if command == "initialize_worker" else ("restart-chrome" if command in {"restart_chrome", "reload_extension"} else "restart-xvfb")
        try:
            result = _run_helper(action, slot, timeout=60)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"{action}_timeout"}
        return {"ok": result.returncode == 0, "scheduled": False, "unit": str(_slot_layout(slot)["chrome_unit"]), "error": None if result.returncode == 0 else f"{action}_failed", "detail": (result.stderr or "")[-240:]}
    if command == "restart_xray":
        if not primary:
            return {"ok": False, "error": "shared_proxy_resource"}
        return legacy.base.run_allowed(command, arguments)
    if command == "get_logs":
        return _diagnostics(slot)
    remote, clipboard = _load_remote_modules(slot)
    if command == "open_login_session":
        if arguments.get("proxy_prevalidated") is not True:
            proxy = legacy.base._proxy_test()
            if not proxy.get("ok"):
                return {"ok": False, "error": "proxy_required_for_login", "proxy": proxy}
        return clipboard.open_session()
    if command == "close_login_session":
        return clipboard.close_session()
    if command == "login_session_frame":
        return remote.capture_frame()
    if command == "login_session_input":
        return clipboard.send_input(arguments)
    return {"ok": False, "error": "not_implemented"}


def _binding_ticket(config: dict[str, Any], target_worker_id: str) -> dict[str, Any] | None:
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
                "X-Target-Worker-ID": target_worker_id,
            },
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(131072).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, HTTPError, URLError, json.JSONDecodeError):
        return None


async def _binding_loop(config: dict[str, Any], target: dict[str, Any]) -> None:
    worker_id = str(target["worker_id"])
    slot = int(target["worker_slot"])
    remote, clipboard = _load_remote_modules(slot)
    while True:
        if clipboard.remote.session_active():
            await asyncio.sleep(BINDING_RETRY_SECONDS)
            continue
        payload = await asyncio.to_thread(_binding_ticket, config, worker_id)
        if payload and payload.get("bound") is True:
            await asyncio.sleep(BINDING_BOUND_POLL_SECONDS)
            continue
        if not payload:
            await asyncio.sleep(BINDING_RETRY_SECONDS)
            continue
        ticket = str(payload.get("ticket") or "")
        server_url = str(payload.get("server_url") or "")
        if not ticket or not server_url or not _service_active(str(_slot_layout(slot)["chrome_unit"])):
            await asyncio.sleep(BINDING_RETRY_SECONDS)
            continue
        result = await asyncio.to_thread(remote.inject_worker_binding, ticket, server_url)
        await asyncio.sleep(12.0 if result.get("ok") else BINDING_RETRY_SECONDS)


async def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    primary_id = str(config["worker_id"])
    headers = {"X-Worker-ID": primary_id, "X-Worker-Token": str(config["worker_token"])}
    delay = 2
    while True:
        binding_tasks: dict[str, asyncio.Task] = {}
        try:
            async with websockets.connect(config["websocket_url"], additional_headers=headers, ping_interval=20) as ws:
                delay = 2
                next_heartbeat = 0.0
                while True:
                    targets = [{"worker_id": primary_id, "worker_slot": 1, "device_name": str(config.get("extension_name") or "")}, *_registry_rows()]
                    for target in targets:
                        worker_id = str(target["worker_id"])
                        if worker_id not in binding_tasks or binding_tasks[worker_id].done():
                            binding_tasks[worker_id] = asyncio.create_task(_binding_loop(config, target))
                    for worker_id, task in list(binding_tasks.items()):
                        if worker_id not in {str(item["worker_id"]) for item in targets}:
                            task.cancel()
                            binding_tasks.pop(worker_id, None)

                    now = time.monotonic()
                    if now >= next_heartbeat:
                        await ws.send(json.dumps({"type": "heartbeat", "data": _health(1)}))
                        for child in _registry_rows():
                            await ws.send(json.dumps({"type": "worker.heartbeat", "worker_id": child["worker_id"], "data": _health(int(child["worker_slot"]))}))
                        next_heartbeat = now + HEARTBEAT_SECONDS

                    timeout = max(0.05, next_heartbeat - time.monotonic())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    message = json.loads(raw)
                    if message.get("type") != "command":
                        continue
                    target_id = str(message.get("target_worker_id") or primary_id)
                    target = _target(config, target_id)
                    if not target:
                        result = {"ok": False, "error": "unknown_target_worker"}
                    else:
                        result = await asyncio.to_thread(_execute, config, target, str(message.get("command") or ""), dict(message.get("arguments") or {}))
                    await ws.send(json.dumps({"type": "command.result", "request_id": message.get("request_id"), "result": result}))
        except Exception as exc:
            print(f"device controller connection unavailable: {type(exc).__name__}", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        finally:
            for task in binding_tasks.values():
                task.cancel()
            for task in binding_tasks.values():
                try:
                    await task
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    asyncio.run(main())
