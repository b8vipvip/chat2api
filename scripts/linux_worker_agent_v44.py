#!/usr/bin/env python3
"""Linux Worker Agent v44 compatibility shim.

Extend the v43 bounded initialization transport with a single fixed online
upgrade command. The Agent still cannot execute arbitrary shell commands: it can
only schedule the root-owned chat2api Worker upgrader installed by bootstrap.

The current bundle also replaces the historical landing-page-only proxy test
with the fixed generation-backend probe shipped in the Worker bundle. Watchdog
results are read from a root-owned state file and added to heartbeat metadata so
the server can stop routing requests to an explicitly unhealthy Linux Worker.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import linux_worker_agent as base
import linux_worker_agent_v43  # noqa: F401 - installs the v43 initialize wrapper on base


AGENT_VERSION = "0.3.6"
UPGRADE_HELPER = Path(os.environ.get("CHAT2API_UPGRADE_HELPER", "/usr/local/sbin/chat2api-worker-upgrade"))
GENERATION_PROBE_HELPER = Path(
    os.environ.get(
        "CHAT2API_GENERATION_PROBE_HELPER",
        str(base.REPO_DIR / "scripts" / "linux_worker_generation_probe.sh"),
    )
)
GENERATION_HEALTH_FILE = Path(
    os.environ.get(
        "CHAT2API_GENERATION_HEALTH_FILE",
        "/var/lib/chat2api-worker/generation-backend-health.json",
    )
)
GENERATION_HEALTH_MAX_AGE_SECONDS = 300

base.AGENT_VERSION = AGENT_VERSION
base.ALLOWED_COMMANDS = set(base.ALLOWED_COMMANDS) | {"upgrade_worker"}
base.IMPLEMENTED_COMMANDS = set(base.IMPLEMENTED_COMMANDS) | {"upgrade_worker"}


def _probe_line(line: str) -> dict[str, object] | None:
    line = str(line or "").strip()
    if not line.startswith("probe="):
        return None
    values: dict[str, str] = {}
    for token in line.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        values[key] = value
    name = str(values.get("probe") or "")[:80]
    if not name:
        return None
    return {
        "name": name,
        "ok": values.get("ok") == "true",
        "http_status": str(values.get("http_status") or "000")[:8],
        "curl_exit": int(values.get("curl_exit") or 0),
        "connect_s": str(values.get("connect_s") or "")[:24],
        "tls_s": str(values.get("tls_s") or "")[:24],
        "total_s": str(values.get("total_s") or "")[:24],
    }


def _generation_proxy_test() -> dict[str, object]:
    if not base.proxy_configured():
        return {"ok": False, "http_status": "000", "error": "proxy_not_configured"}
    if not base.service_active("chat2api-xray.service"):
        return {"ok": False, "http_status": "000", "error": "xray_not_running"}
    if not GENERATION_PROBE_HELPER.is_file():
        return {"ok": False, "http_status": "000", "error": "generation_probe_missing"}

    env = dict(os.environ)
    env["CHAT2API_PROXY_PORT"] = str(base.PROXY_PORT)
    try:
        result = subprocess.run(
            [str(GENERATION_PROBE_HELPER)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "http_status": "000", "error": "generation_backend_probe_timeout"}
    except OSError:
        return {"ok": False, "http_status": "000", "error": "generation_probe_command_failed"}

    probes: list[dict[str, object]] = []
    for line in (result.stdout or "").splitlines():
        parsed = _probe_line(line)
        if parsed:
            probes.append(parsed)
    landing = next((item for item in probes if item.get("name") == "chatgpt_home"), None)
    http_status = str((landing or {}).get("http_status") or "000")[:8]
    ready = result.returncode == 0 and bool(probes) and all(item.get("ok") is True for item in probes)
    return {
        "ok": ready,
        "http_status": http_status,
        "error": None if ready else "generation_backend_connectivity_test_failed",
        "generation_backend_ready": ready,
        "probes": probes,
    }


def _generation_health_state() -> dict[str, object] | None:
    try:
        value = json.loads(GENERATION_HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("ready"), bool):
        return None
    try:
        checked = int(value.get("checked_at_epoch") or 0)
    except (TypeError, ValueError):
        checked = 0
    age = max(0, int(time.time()) - checked) if checked else 10**9
    return {
        "ready": bool(value.get("ready")),
        "checked_at_epoch": checked,
        "age_seconds": age,
        "fresh": age <= GENERATION_HEALTH_MAX_AGE_SECONDS,
        "source": str(value.get("source") or "")[:120],
    }


_previous_health = base.health


def _health_with_generation_backend() -> dict[str, object]:
    payload = dict(_previous_health())
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    generation = _generation_health_state()
    if generation:
        metadata["generation_backend_health"] = generation
        if generation.get("fresh") is True and generation.get("ready") is False:
            payload["status"] = "degraded"
            payload["proxy_status"] = "error"
    payload["metadata"] = metadata
    return payload


base._proxy_test = _generation_proxy_test
base.health = _health_with_generation_backend
_previous_run_allowed = base.run_allowed


def _upgrade_worker() -> dict[str, object]:
    if not UPGRADE_HELPER.is_file():
        return {"ok": False, "error": "upgrade_helper_missing"}
    try:
        result = subprocess.run(
            ["sudo", "-n", str(UPGRADE_HELPER), "--schedule"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "upgrade_schedule_timeout"}
    except OSError:
        return {"ok": False, "error": "upgrade_helper_launch_failed"}

    payload: dict[str, object] = {}
    for line in reversed((result.stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            payload = value
            break

    if result.returncode != 0 or payload.get("ok") is not True:
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[-300:]
        return {
            "ok": False,
            "error": str(payload.get("error") or "upgrade_schedule_failed")[:120],
            "detail": detail,
        }

    return {
        "ok": True,
        "scheduled": True,
        "unit": str(payload.get("unit") or "")[:160],
        "agent_version": AGENT_VERSION,
    }


def run_allowed(command: str, arguments: dict | None = None) -> dict:
    if command == "upgrade_worker":
        return _upgrade_worker()
    return _previous_run_allowed(command, arguments)


base.run_allowed = run_allowed


if __name__ == "__main__":
    asyncio.run(base.main())
