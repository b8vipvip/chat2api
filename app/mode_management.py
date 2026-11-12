from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE_SETTINGS_VERSION = 1
MODE_SETTINGS_FILENAME = "mode_management.json"

# This is an authorization/implementation gate, not an administrator preference.
# It must remain false until the separate legal/security review explicitly clears
# hosted multi-user private-web proxying and a separately reviewed runtime exists.
MODE1_COOKIE_PROXY_AUTHORIZATION_APPROVED = False
MODE1_COOKIE_PROXY_RUNTIME_IMPLEMENTED = False

MODE1_COOKIE_PROXY_NO_GO_REASON = (
    "Hosted multi-user private-web proxying is NO-GO pending explicit written "
    "OpenAI authorization/partner approval. This setting records test intent only; "
    "it never enables a proxy or handles ChatGPT cookies, sessions, or private APIs."
)


class ModeManagementError(ValueError):
    """Invalid or unreadable persisted mode-management state."""


class ModeManagementStore:
    """Persist administrator mode preferences separately from runtime authorization.

    Mode 2 remains the fixed default. The Mode 1 checkbox stores only an operator's
    test/configuration intent; effective availability is independently gated by
    authorization and implementation constants and therefore remains disabled.
    """

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / MODE_SETTINGS_FILENAME
        self._lock = threading.RLock()

    def _read_locked(self) -> tuple[bool, str | None]:
        if self.path.is_symlink():
            raise ModeManagementError("Mode settings file must not be a symbolic link")
        if not self.path.exists():
            return False, None
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModeManagementError("Mode settings file is unreadable or corrupt") from error
        if not isinstance(payload, dict) or payload.get("version") != MODE_SETTINGS_VERSION:
            raise ModeManagementError("Unsupported mode settings file format")
        mode1 = payload.get("mode1_cookie_proxy")
        if not isinstance(mode1, dict) or type(mode1.get("requested_enabled")) is not bool:
            raise ModeManagementError("Mode 1 test setting must be a boolean")
        updated_at = payload.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            raise ModeManagementError("Mode settings timestamp is invalid")
        return mode1["requested_enabled"], updated_at

    @staticmethod
    def _effective_enabled(requested_enabled: bool) -> bool:
        return bool(
            requested_enabled
            and MODE1_COOKIE_PROXY_AUTHORIZATION_APPROVED
            and MODE1_COOKIE_PROXY_RUNTIME_IMPLEMENTED
        )

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            requested_enabled, updated_at = self._read_locked()
        effective_enabled = self._effective_enabled(requested_enabled)
        return {
            "default_mode": "mode2",
            "mode2": {
                "enabled": True,
                "is_default": True,
                "label": "Mode 2 · OpenAI 兼容 API",
            },
            "mode1_cookie_proxy": {
                "requested_enabled": requested_enabled,
                "effective_enabled": effective_enabled,
                "authorization_gate": (
                    "approved" if MODE1_COOKIE_PROXY_AUTHORIZATION_APPROVED else "NO-GO"
                ),
                "runtime_implemented": MODE1_COOKIE_PROXY_RUNTIME_IMPLEMENTED,
                "status": "available" if effective_enabled else "blocked_no_go",
                "reason": "" if effective_enabled else MODE1_COOKIE_PROXY_NO_GO_REASON,
                "updated_at": updated_at,
            },
        }

    def set_mode1_cookie_proxy_requested(self, enabled: bool) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ModeManagementError("mode1_cookie_proxy_requested must be a boolean")
        with self._lock:
            current, updated_at = self._read_locked()
            if current == enabled and self.path.exists():
                return self.public_state()
            payload = {
                "version": MODE_SETTINGS_VERSION,
                "mode1_cookie_proxy": {"requested_enabled": enabled},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_locked(payload)
        return self.public_state()

    def _write_locked(self, payload: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ModeManagementError("Mode settings file must not be a symbolic link")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            try:
                directory_fd = os.open(self.data_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ModeManagementError("Unable to persist mode settings") from error
        finally:
            temporary.unlink(missing_ok=True)
