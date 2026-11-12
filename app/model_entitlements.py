"""C-03a model entitlement policy store.

This is authorization policy only. Worker availability, model picker evidence,
routing, aliases and served-model provenance remain owned by the existing
model resolver/Worker capability layers.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model_catalog import normalize_concrete_model_id


SUBJECT_TYPES = frozenset({"global", "key", "user", "tenant", "tenant_role"})
EFFECTS = frozenset({"allow", "deny"})
MAX_MODELS = 200
SUBJECT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class ModelEntitlementError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _models(values: Any) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_MODELS:
        raise ModelEntitlementError("models must contain 1-200 concrete model IDs")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        model = normalize_concrete_model_id(raw)
        if not model:
            raise ModelEntitlementError("Invalid or non-concrete model ID")
        if model not in seen:
            result.append(model)
            seen.add(model)
    return result


class ModelEntitlementStore:
    """SQLite owner for global, API-key and user model policy."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "model_entitlements_v3.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ModelEntitlementError("Model entitlement store cannot be a symbolic link")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS model_entitlement_rules(
              subject_type TEXT NOT NULL CHECK(subject_type IN ('global','tenant','tenant_role','key','user')),
              subject_id TEXT NOT NULL,
              effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
              models_json TEXT NOT NULL,
              enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              updated_at TEXT NOT NULL,
              PRIMARY KEY(subject_type,subject_id)
            )
        """)
        os.chmod(self.path, 0o600)

    @staticmethod
    def _subject(subject_type: str, subject_id: str) -> tuple[str, str]:
        if subject_type not in SUBJECT_TYPES:
            raise ModelEntitlementError("Unsupported subject_type")
        if subject_type == "global":
            if subject_id != "default":
                raise ModelEntitlementError("Global policy subject_id must be 'default'")
            return subject_type, subject_id
        if not isinstance(subject_id, str) or not SUBJECT_ID.fullmatch(subject_id):
            raise ModelEntitlementError("Invalid subject_id")
        return subject_type, subject_id

    def upsert(
        self,
        subject_type: str,
        subject_id: str,
        effect: str,
        models: Any,
        *,
        enabled: bool = True,
    ) -> dict[str, Any]:
        subject_type, subject_id = self._subject(subject_type, subject_id)
        if effect not in EFFECTS:
            raise ModelEntitlementError("effect must be allow or deny")
        if type(enabled) is not bool:
            raise ModelEntitlementError("enabled must be a boolean")
        normalized = _models(models)
        updated = _now()
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("""
                    INSERT INTO model_entitlement_rules
                      (subject_type,subject_id,effect,models_json,enabled,updated_at)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(subject_type,subject_id) DO UPDATE SET
                      effect=excluded.effect,models_json=excluded.models_json,
                      enabled=excluded.enabled,updated_at=excluded.updated_at
                """, (subject_type, subject_id, effect, encoded, int(enabled), updated))
        return {
            "subject_type": subject_type, "subject_id": subject_id,
            "effect": effect, "models": normalized, "enabled": enabled,
            "updated_at": updated,
        }

    def delete(self, subject_type: str, subject_id: str) -> bool:
        subject_type, subject_id = self._subject(subject_type, subject_id)
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                cursor = self.db.execute(
                    "DELETE FROM model_entitlement_rules WHERE subject_type=? AND subject_id=?",
                    (subject_type, subject_id),
                )
                return cursor.rowcount > 0

    def list_rules(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("""
                SELECT subject_type,subject_id,effect,models_json,enabled,updated_at
                FROM model_entitlement_rules
                ORDER BY subject_type,subject_id
            """).fetchall()
        return [{
            "subject_type": kind, "subject_id": sid, "effect": effect,
            "models": json.loads(models), "enabled": bool(enabled), "updated_at": updated,
        } for kind, sid, effect, models, enabled, updated in rows]

    def rules_for(
        self, *, key_id: str | None, user_id: str | None, tenant_id: str | None = None,
        tenant_role: str | None = None,
    ) -> list[dict[str, Any]]:
        pairs = [("global", "default")]
        if tenant_id:
            pairs.append(("tenant", str(tenant_id)))
        if tenant_role:
            pairs.append(("tenant_role", str(tenant_role)))
        if user_id:
            pairs.append(("user", str(user_id)))
        if key_id and key_id != "master":
            pairs.append(("key", str(key_id)))
        clauses = " OR ".join("(subject_type=? AND subject_id=?)" for _ in pairs)
        params = [value for pair in pairs for value in pair]
        with self.lock:
            rows = self.db.execute(
                f"""SELECT subject_type,subject_id,effect,models_json,enabled,updated_at
                    FROM model_entitlement_rules WHERE ({clauses})""",
                params,
            ).fetchall()
        return [{
            "subject_type": kind, "subject_id": sid, "effect": effect,
            "models": json.loads(models), "enabled": bool(enabled), "updated_at": updated,
        } for kind, sid, effect, models, enabled, updated in rows if enabled]

    def evaluate(
        self,
        model_id: str,
        *,
        key_id: str | None,
        user_id: str | None,
        tenant_id: str | None = None,
        tenant_role: str | None = None,
    ) -> dict[str, Any]:
        rules = self.rules_for(
            key_id=key_id, user_id=user_id, tenant_id=tenant_id, tenant_role=tenant_role
        )
        if not rules:
            return {"allowed": True, "reason": "no_policy", "model": model_id, "matched_rules": []}
        model = normalize_concrete_model_id(model_id)
        if not model:
            return {
                "allowed": False, "reason": "unresolved_model_alias",
                "model": str(model_id or "")[:128],
                "matched_rules": [{"subject_type": r["subject_type"], "subject_id": r["subject_id"]} for r in rules],
            }
        denied = [r for r in rules if r["effect"] == "deny" and model in r["models"]]
        if denied:
            return {
                "allowed": False, "reason": "explicit_deny", "model": model,
                "matched_rules": [{"subject_type": r["subject_type"], "subject_id": r["subject_id"]} for r in denied],
            }
        allow_rules = [r for r in rules if r["effect"] == "allow"]
        missing = [r for r in allow_rules if model not in r["models"]]
        if missing:
            return {
                "allowed": False, "reason": "allowlist_intersection",
                "model": model,
                "matched_rules": [{"subject_type": r["subject_type"], "subject_id": r["subject_id"]} for r in missing],
            }
        return {
            "allowed": True, "reason": "policy_allow", "model": model,
            "matched_rules": [{"subject_type": r["subject_type"], "subject_id": r["subject_id"]} for r in rules],
        }

    def close(self) -> None:
        self.db.close()
