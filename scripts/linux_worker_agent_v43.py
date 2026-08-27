#!/usr/bin/env python3
"""Linux Worker Agent v43 compatibility shim.

Keep the long-lived Agent implementation in linux_worker_agent.py while adding
one narrowly-scoped host initialization command. This avoids turning the Agent
into a general shell executor and keeps existing Worker protocol behavior intact.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import linux_worker_agent as base


AGENT_VERSION = "0.3.5"
INITIALIZE_HELPER = Path(os.environ.get("CHAT2API_INITIALIZE_HELPER", "/usr/local/sbin/chat2api-worker-initialize"))

base.AGENT_VERSION = AGENT_VERSION
base.ALLOWED_COMMANDS = set(base.ALLOWED_COMMANDS) | {"initialize_worker"}
base.IMPLEMENTED_COMMANDS = set(base.IMPLEMENTED_COMMANDS) | {"initialize_worker"}
_base_run_allowed = base.run_allowed


def _initialize_worker() -> dict[str, object]:
    if not INITIALIZE_HELPER.is_file():
        return {"ok": False, "error": "initialize_helper_missing"}
    try:
        result = subprocess.run(
            ["sudo", "-n", str(INITIALIZE_HELPER), "--schedule"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "initialize_schedule_timeout"}
    except OSError:
        return {"ok": False, "error": "initialize_helper_launch_failed"}
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
        return {"ok": False, "error": str(payload.get("error") or "initialize_schedule_failed")[:120], "detail": detail}
    return {
        "ok": True,
        "scheduled": True,
        "unit": str(payload.get("unit") or "")[:160],
        "agent_version": AGENT_VERSION,
    }


def run_allowed(command: str, arguments: dict | None = None) -> dict:
    if command == "initialize_worker":
        return _initialize_worker()
    return _base_run_allowed(command, arguments)


base.run_allowed = run_allowed


if __name__ == "__main__":
    asyncio.run(base.main())
