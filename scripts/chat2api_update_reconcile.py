#!/usr/bin/env python3
"""Finalize a host update that systemd killed before Bash's EXIT trap ran.

ExecStopPost receives SERVICE_RESULT from systemd. Never claim that a
deployment or rollback succeeded just because its subprocess was killed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def reconcile(status_path: Path, *, service_result: str, exit_status: str = "") -> bool:
    if service_result in {"", "success"} or not status_path.is_file():
        return False
    try:
        current = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(current, dict) or current.get("status") not in {"running", "queued"}:
        return False

    now = datetime.now(timezone.utc).isoformat()
    phase = str(current.get("stage") or "unknown")
    current.update({
        "status": "failed",
        "stage": phase,
        "message": (
            f"更新服务异常退出（systemd={service_result}，exit={exit_status or 'unknown'}）；"
            "旧服务及工作区状态请以 Docker/Git 实际检查为准，不能推定已回滚"
        ),
        "updated_at": now,
        "completed_at": now,
        "rollback_succeeded": False,
    })
    fd, temp_name = tempfile.mkstemp(prefix=".admin-update-terminal.",
                                     dir=str(status_path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, status_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


if __name__ == "__main__":
    root = Path(os.environ.get("APP_DIR", "/opt/chat2api"))
    result = os.environ.get("SERVICE_RESULT", "")
    code = os.environ.get("EXIT_STATUS", "")
    try:
        if reconcile(root / "data" / "admin-update-status.json",
                     service_result=result, exit_status=code):
            print(f"chat2api updater: reconciled stale status after {result}", flush=True)
    except Exception as error:
        print(f"chat2api updater: reconciliation failed: {error}", file=sys.stderr,
              flush=True)
        sys.exit(1)
