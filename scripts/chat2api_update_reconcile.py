#!/usr/bin/env python3
"""Finalize a host update that systemd killed before Bash's EXIT trap ran.

ExecStopPost receives SERVICE_RESULT from systemd. Never claim that a
deployment or rollback succeeded just because its subprocess was killed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from chat2api_update_storage import (
    read_bytes, read_json, validate_directory, write_bytes_atomic,
    write_json_atomic,
)


def reconcile(status_path: Path, *, service_result: str, exit_status: str = "") -> bool:
    data_dir = status_path.parent
    app_dir = data_dir.parent
    compose_backup = data_dir / ".admin-update-docker-compose.backup"
    compose_target = app_dir / "docker-compose.yml"

    validate_directory(data_dir)
    validate_directory(app_dir)

    # The updater preserves a locally customized compose file across its hard
    # checkout. Read it through a no-follow, single-link descriptor and restore
    # it atomically; never copy through a symlink or a hard-linked target.
    backup_bytes = read_bytes(compose_backup, max_bytes=4 * 1024 * 1024)
    if backup_bytes is not None:
        if service_result == "success":
            compose_backup.unlink()
        else:
            write_bytes_atomic(compose_target, backup_bytes, mode=0o644)
            compose_backup.unlink()

    if service_result in {"", "success"}:
        return False
    try:
        current = read_json(status_path)
    except (OSError, ValueError, RuntimeError):
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
    write_json_atomic(status_path, current)
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
