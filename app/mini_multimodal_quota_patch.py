from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from . import v13_patch


MINI_MODEL = "gpt-5.5-mini"
MULTIMODAL_CAPABILITIES = {"vision", "file-understanding"}
MULTIMODAL_PART_TYPES = {"image_url", "input_image", "file", "input_file"}


def _account_type(registry: Any, client_id: str) -> str:
    client = registry.clients.get(str(client_id))
    metadata = getattr(client, "metadata", None) if client else None
    value = str((metadata or {}).get("account_type") or "unknown").strip().lower()
    return value if value in {"free", "paid"} else "unknown"


def _cooldown_until_ms(registry: Any, client_id: str) -> int:
    client = registry.clients.get(str(client_id))
    metadata = getattr(client, "metadata", None) if client else None
    if not isinstance(metadata, dict):
        return 0

    for key in ("file_upload_cooldown_until_ms", "mini_multimodal_cooldown_until_ms"):
        try:
            value = int(float(metadata.get(key) or 0))
        except (TypeError, ValueError):
            value = 0
        if value > int(time.time() * 1000):
            return value

    for key in ("file_upload_cooldown_until", "mini_multimodal_cooldown_until"):
        raw = str(metadata.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            value = int(parsed.timestamp() * 1000)
            if value > int(time.time() * 1000):
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    return 0


def _multimodal_available(registry: Any, client_id: str) -> bool:
    if _account_type(registry, client_id) != "free":
        return True
    return _cooldown_until_ms(registry, client_id) <= 0


def _needs_multimodal(value: Any) -> bool:
    if isinstance(value, list):
        return any(_needs_multimodal(item) for item in value)
    if not isinstance(value, dict):
        return False

    if value.get("attachments"):
        return True
    part_type = str(value.get("type") or "").strip().lower()
    if part_type in MULTIMODAL_PART_TYPES:
        return True
    if value.get("file_id"):
        return True
    if part_type in {"image", "image_url", "input_image"} and value.get("image_url"):
        return True
    return any(_needs_multimodal(item) for item in value.values() if isinstance(item, (dict, list)))


def _iso_from_ms(value: int) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def install_mini_multimodal_quota_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    if getattr(registry, "_chat2api_mini_multimodal_quota", False):
        return app

    if not getattr(v13_patch, "_chat2api_mini_multimodal_target", False):
        base_target_from_payload = v13_patch._target_from_payload

        def target_from_payload_with_multimodal(payload: dict[str, Any]) -> dict[str, Any]:
            target = dict(base_target_from_payload(payload))
            target["needs_multimodal"] = _needs_multimodal(payload)
            return target

        v13_patch._target_from_payload = target_from_payload_with_multimodal
        v13_patch._chat2api_mini_multimodal_target = True

    base_resolve_client = registry.resolve_client

    def resolve_client_with_mini_multimodal_quota(requested: str | None) -> str:
        target = v13_patch._target_context.get() or {}
        needs_multimodal = bool(target.get("needs_multimodal"))
        if not needs_multimodal:
            return base_resolve_client(requested)

        if requested:
            selected = base_resolve_client(requested)
            if _account_type(registry, selected) == "free" and not _multimodal_available(registry, selected):
                until_ms = _cooldown_until_ms(registry, selected)
                raise LookupError(
                    "Requested ChatGPT Free Worker has file upload quota cooling down"
                    + (f" until {_iso_from_ms(until_ms)}" if until_ms else "")
                )
            return selected

        online = registry.online_client_ids()
        if not online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")
        idle = [client_id for client_id in online if client_id not in registry.busy_clients]
        if not idle:
            raise LookupError("All online Chrome extensions are busy")

        free_ready = [
            client_id for client_id in idle
            if _account_type(registry, client_id) == "free" and _multimodal_available(registry, client_id)
        ]
        if free_ready:
            return secrets.choice(free_ready)

        fallback = [client_id for client_id in idle if _account_type(registry, client_id) != "free"]
        if fallback:
            return secrets.choice(fallback)

        cooling_free = [
            client_id for client_id in idle
            if _account_type(registry, client_id) == "free" and not _multimodal_available(registry, client_id)
        ]
        if cooling_free:
            restore_times = sorted(
                value for value in (_cooldown_until_ms(registry, client_id) for client_id in cooling_free) if value > 0
            )
            restore = _iso_from_ms(restore_times[0]) if restore_times else None
            raise ConnectionError(
                "All available ChatGPT Free file upload quotas are cooling down"
                + (f" until {restore}" if restore else "")
            )
        raise ConnectionError("No compatible Chrome extension is available for attachment input")

    registry.resolve_client = resolve_client_with_mini_multimodal_quota

    base_model_catalog = registry.model_catalog

    def model_catalog_with_mini_multimodal_quota(online_only: bool = True) -> list[dict[str, Any]]:
        rows = [dict(row) for row in base_model_catalog(online_only=online_only)]
        client_ids = registry.online_client_ids() if online_only else [
            client_id for client_id, item in registry.clients.items() if item.connection_enabled
        ]
        free_clients = [client_id for client_id in client_ids if _account_type(registry, client_id) == "free"]
        ready_free = [client_id for client_id in free_clients if _multimodal_available(registry, client_id)]
        cooling_free = [client_id for client_id in free_clients if not _multimodal_available(registry, client_id)]
        fallback_clients = [client_id for client_id in client_ids if _account_type(registry, client_id) != "free"]
        multimodal_available = bool(ready_free or fallback_clients)
        restore_times = sorted(
            value for value in (_cooldown_until_ms(registry, client_id) for client_id in cooling_free) if value > 0
        )

        for row in rows:
            if str(row.get("id") or "").strip().lower() != MINI_MODEL:
                continue
            capabilities = list(dict.fromkeys(row.get("capabilities") or ["text"]))
            if "text" not in capabilities:
                capabilities.insert(0, "text")
            if multimodal_available:
                for capability in ("vision", "file-understanding"):
                    if capability not in capabilities:
                        capabilities.append(capability)
            else:
                capabilities = [item for item in capabilities if item not in MULTIMODAL_CAPABILITIES]
            row["capabilities"] = capabilities
            row["multimodal_available"] = multimodal_available
            row["native_free_multimodal_clients"] = ready_free
            row["native_free_multimodal_cooling_clients"] = cooling_free
            row["multimodal_fallback_clients"] = fallback_clients
            row["multimodal_resume_at"] = _iso_from_ms(restore_times[0]) if restore_times else None
            row["multimodal_quota_policy"] = "account-file-upload-circuit-breaker-v91"
        return rows

    registry.model_catalog = model_catalog_with_mini_multimodal_quota
    registry._chat2api_mini_multimodal_quota = True
    return app
