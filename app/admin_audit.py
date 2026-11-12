from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AdminAuditError(RuntimeError):
    """Raised when the append-only administrator audit log is unsafe or invalid."""


class AdminAuditStore:
    """Small append-only, hash-chained audit log for control-plane mutations."""

    FILENAME = "admin_audit.jsonl"
    MAX_BYTES = 16 * 1024 * 1024
    ZERO_HASH = "0" * 64

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / self.FILENAME
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _read_events_locked(self) -> list[dict[str, Any]]:
        if self.path.is_symlink():
            raise AdminAuditError("Administrator audit log must not be a symbolic link")
        if not self.path.exists():
            return []
        if not self.path.is_file():
            raise AdminAuditError("Administrator audit log is not a regular file")
        try:
            if self.path.stat().st_mode & 0o777 != 0o600:
                os.chmod(self.path, 0o600)
                if self.path.stat().st_mode & 0o777 != 0o600:
                    raise OSError("administrator audit permissions could not be restricted")
            if self.path.stat().st_size > self.MAX_BYTES:
                raise AdminAuditError("Administrator audit log reached its safety size limit")
            raw_lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise AdminAuditError("Administrator audit log cannot be read") from error

        events: list[dict[str, Any]] = []
        previous_hash = self.ZERO_HASH
        for line_number, line in enumerate(raw_lines, start=1):
            try:
                row = json.loads(line)
            except (TypeError, ValueError) as error:
                raise AdminAuditError(
                    f"Administrator audit log has invalid JSON at line {line_number}"
                ) from error
            if not isinstance(row, dict):
                raise AdminAuditError(
                    f"Administrator audit log has an invalid record at line {line_number}"
                )
            supplied_hash = str(row.get("event_hash") or "")
            payload = {key: value for key, value in row.items() if key != "event_hash"}
            expected_hash = hashlib.sha256(self._canonical(payload)).hexdigest()
            if (
                payload.get("previous_hash") != previous_hash
                or not secrets.compare_digest(supplied_hash, expected_hash)
            ):
                raise AdminAuditError(
                    f"Administrator audit hash chain failed at line {line_number}"
                )
            previous_hash = supplied_hash
            events.append(row)
        return events

    def append(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        request_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            events = self._read_events_locked()
            payload = {
                "event_id": "aud_" + secrets.token_hex(12),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor": str(actor or "admin")[:120],
                "action": str(action or "")[:120],
                "target_type": str(target_type or "")[:80],
                "target_id": str(target_id or "")[:160],
                "request_id": str(request_id or "")[:128],
                "result": "completed" if str(action or "").endswith("_completed") else "requested",
                "policy_version": 1,
                "details": dict(details or {}),
                "previous_hash": events[-1]["event_hash"] if events else self.ZERO_HASH,
            }
            event_hash = hashlib.sha256(self._canonical(payload)).hexdigest()
            row = {**payload, "event_hash": event_hash}
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise AdminAuditError("Administrator audit log must not be a symbolic link")
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    view = memoryview(line)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("short write while appending administrator audit event")
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    directory_fd = os.open(self.data_dir, os.O_RDONLY)
                except OSError:
                    directory_fd = None
                if directory_fd is not None:
                    try:
                        os.fsync(directory_fd)
                    except OSError:
                        pass
                    finally:
                        os.close(directory_fd)
            except OSError as error:
                raise AdminAuditError("Unable to persist administrator audit event") from error
            return row

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or limit < 1 or limit > 200:
            raise ValueError("Audit limit must be between 1 and 200")
        with self._lock:
            return list(reversed(self._read_events_locked()[-limit:]))
