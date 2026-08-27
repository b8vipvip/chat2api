#!/usr/bin/env python3
"""Linux Worker Agent v44 compatibility shim.

Extend the v43 bounded initialization transport with a single fixed online
upgrade command. The Agent still cannot execute arbitrary shell commands: it can
only schedule the root-owned chat2api Worker upgrader installed by bootstrap.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import linux_worker_agent as base
import linux_worker_agent_v43  # noqa: F401 - installs the v43 initialize wrapper on base


AGENT_VERSION = "0.3.6"
UPGRADE_HELPER = Path(os.environ.get("CHAT2API_UPGRADE_HELPER", "/usr/local/sbin/chat2api-worker-upgrade"))

base.AGENT_VERSION = AGENT_VERSION
base.ALLOWED_COMMANDS = set(base.ALLOWED_COMMANDS) | {"upgrade_worker"}
base.IMPLEMENTED_COMMANDS = set(base.IMPLEMENTED_COMMANDS) | {"upgrade_worker"}
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
