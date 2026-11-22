from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .private_storage import open_private_regular_file, private_file_lock


class AdminAuditError(RuntimeError):
    """Raised when the append-only administrator audit log is unsafe or invalid."""


_REDACTED = "[REDACTED]"
_SENSITIVE_AUDIT_FIELDS = frozenset({
    "password", "passwd", "passphrase", "password_hash", "secret", "secret_key",
    "private_key", "api_key", "apikey", "api_key_value", "client_secret",
    "access_token", "refresh_token", "id_token", "token", "token_hash",
    "token_value", "authorization", "proxy_authorization", "cookie", "set_cookie",
    "session_cookie", "session_token", "csrf_token", "invitation_code", "invite_code",
    "one_time_code", "otp", "bearer", "jwt", "jwt_token", "key", "key_value", "master_key",
    "signing_key", "webhook_secret",
})
_SAFE_AUDIT_IDENTIFIERS = frozenset({
    "key_id", "api_key_id", "session_id", "token_id",
    "token_count", "tokens_used", "token_quota", "reserved_tokens",
})
_SENSITIVE_AUDIT_SUFFIXES = (
    "_password", "_passwd", "_passphrase", "_secret", "_private_key",
    "_api_key", "_token", "_cookie", "_credential", "_authorization",
    "_invite_code", "_invitation_code", "_one_time_code", "_csrf_token", "_otp",
)
_SENSITIVE_AUDIT_PREFIXES = (
    "password_", "passwd_", "passphrase_", "secret_", "private_key_",
    "api_key_", "apikey_", "client_secret_", "access_token_", "refresh_token_",
    "id_token_", "session_token_", "authorization_", "cookie_",
    "invite_code_", "invitation_code_", "one_time_code_", "token_",
)


def _sensitive_audit_field(name: Any) -> bool:
    raw_name = str(name or "")
    # Normalize both lowerCamelCase and acronym-prefixed names (CSRFToken,
    # APIKey, refreshToken) before applying the field-name denylist.
    camel_separated = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1_\2", raw_name)
    camel_separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", camel_separated)
    normalized = re.sub(r"[^a-z0-9]+", "_", camel_separated.casefold()).strip("_")
    if normalized in _SAFE_AUDIT_IDENTIFIERS:
        return False
    if normalized in _SENSITIVE_AUDIT_FIELDS:
        return True
    if normalized.endswith(_SENSITIVE_AUDIT_SUFFIXES):
        return True
    if normalized.startswith(_SENSITIVE_AUDIT_PREFIXES):
        return True
    compact = normalized.replace("_", "")
    return any(part in compact for part in (
        "password", "passwd", "passphrase", "secret", "authorization",
        "cookie", "credential", "privatekey", "apikey", "invitationcode",
        "invitecode", "onetimecode",
    ))


def _redact_audit_value(value: Any) -> Any:
    """Recursively redact secret-bearing audit fields before persistence/export."""
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _sensitive_audit_field(key) else _redact_audit_value(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_audit_value(child) for child in value]
    return value


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
        try:
            fd = open_private_regular_file(self.path, os.O_RDONLY)
        except FileNotFoundError:
            return []
        except (OSError, RuntimeError) as error:
            raise AdminAuditError("Administrator audit log cannot be opened safely") from error
        try:
            with os.fdopen(fd, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if metadata.st_size > self.MAX_BYTES:
                    raise AdminAuditError("Administrator audit log reached its safety size limit")
                raw_lines = handle.read().decode("utf-8").splitlines()
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
        with self._lock, private_file_lock(self.data_dir / ".admin_audit.lock"):
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
                "details": _redact_audit_value(dict(details or {})),
                "previous_hash": events[-1]["event_hash"] if events else self.ZERO_HASH,
            }
            event_hash = hashlib.sha256(self._canonical(payload)).hexdigest()
            row = {**payload, "event_hash": event_hash}
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            self.data_dir.mkdir(parents=True, exist_ok=True)
            try:
                fd = open_private_regular_file(
                    self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                )
                try:
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
            except (OSError, RuntimeError) as error:
                raise AdminAuditError("Unable to persist administrator audit event") from error
            return row

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or limit < 1 or limit > 200:
            raise ValueError("Audit limit must be between 1 and 200")
        with self._lock, private_file_lock(self.data_dir / ".admin_audit.lock"):
            rows = list(reversed(self._read_events_locked()[-limit:]))
            projected: list[dict[str, Any]] = []
            for row in rows:
                visible = dict(row)
                original_details = visible.get("details", {})
                safe_details = _redact_audit_value(original_details)
                if safe_details != original_details:
                    # Historical hash-chained rows are never rewritten in place:
                    # their stored event_hash still authenticates the original
                    # bytes. Mark the read projection so consumers know details
                    # were redacted for display/export.
                    visible["details"] = safe_details
                    visible["details_redacted"] = True
                projected.append(visible)
            return projected
