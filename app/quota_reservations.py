"""C-06a in-flight token quota reservation journal.

ApiKeyStore.account_completed_usage remains the only committed token-usage
authority. This SQLite journal reserves estimated in-flight capacity, then
coordinates idempotent completion against that existing API-key ledger.
It does not charge user wallets or claim provider-verified usage.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


ACTIVE_STATES = ("reserved", "committing")


class QuotaReservationError(ValueError):
    pass


class QuotaExceeded(QuotaReservationError):
    pass


class QuotaReservationStore:
    def __init__(self, data_dir: Path | str, *, reservation_ttl_seconds: int = 1800) -> None:
        self.path = Path(data_dir) / "quota_reservations_v1.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise QuotaReservationError("Quota reservation store cannot be a symbolic link")
        self.reservation_ttl_seconds = max(60, int(reservation_ttl_seconds))
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS quota_reservations(
              request_id TEXT PRIMARY KEY,
              key_id TEXT NOT NULL,
              reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens >= 0),
              actual_tokens INTEGER,
              state TEXT NOT NULL CHECK(state IN ('reserved','committing','settled','released','expired')),
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS quota_reservations_key_state_idx ON quota_reservations(key_id,state)")
        os.chmod(self.path, 0o600)

    @staticmethod
    def _validate(request_id: str, key_id: str, amount: int) -> None:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise QuotaReservationError("Invalid request_id")
        if not isinstance(key_id, str) or not key_id or len(key_id) > 160:
            raise QuotaReservationError("Invalid key_id")
        if type(amount) is not int or amount < 0:
            raise QuotaReservationError("Token amounts must be non-negative integers")

    @staticmethod
    def _row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "request_id": row[0], "key_id": row[1],
            "reserved_tokens": int(row[2]), "actual_tokens": row[3],
            "state": row[4], "created_at": float(row[5]),
            "expires_at": float(row[6]), "updated_at": float(row[7]),
        }

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT request_id,key_id,reserved_tokens,actual_tokens,state,created_at,expires_at,updated_at
                   FROM quota_reservations WHERE request_id=?""",
                (request_id,),
            ).fetchone()
        return self._row(row)

    def active_reserved_tokens(self, key_id: str) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT COALESCE(SUM(reserved_tokens),0) FROM quota_reservations WHERE key_id=? AND state IN ('reserved','committing')",
                (key_id,),
            ).fetchone()
        return max(0, int(row[0] or 0))

    def list_reservations(
        self, *, state: str | None = None, key_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if state is not None and state not in {"reserved", "committing", "settled", "released", "expired"}:
            raise QuotaReservationError("Unknown reservation state")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise QuotaReservationError("limit must be between 1 and 500")
        clauses = []
        params: list[Any] = []
        if state:
            clauses.append("state=?")
            params.append(state)
        if key_id:
            clauses.append("key_id=?")
            params.append(key_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self.lock:
            rows = self.db.execute(
                """SELECT request_id,key_id,reserved_tokens,actual_tokens,state,created_at,expires_at,updated_at
                   FROM quota_reservations""" + where + " ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row(row) for row in rows if row is not None]

    def reserve(
        self,
        request_id: str,
        key_id: str,
        estimated_tokens: int,
        *,
        quota_limit: int | None,
        completed_tokens: int,
    ) -> dict[str, Any]:
        self._validate(request_id, key_id, estimated_tokens)
        self._validate("completed", key_id, completed_tokens)
        if quota_limit is not None and (type(quota_limit) is not int or quota_limit < 0):
            raise QuotaReservationError("quota_limit must be a non-negative integer or None")
        now = time.time()
        expires = now + self.reservation_ttl_seconds
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                existing = self.db.execute(
                    """SELECT request_id,key_id,reserved_tokens,actual_tokens,state,created_at,expires_at,updated_at
                       FROM quota_reservations WHERE request_id=?""",
                    (request_id,),
                ).fetchone()
                if existing:
                    value = self._row(existing)
                    if value["key_id"] != key_id:
                        raise QuotaReservationError("request_id is already reserved by another API key")
                    # Idempotent retries may observe an in-flight reservation or
                    # the same completed settlement. A terminal release/expiry
                    # must never be returned as if it were a fresh reservation:
                    # otherwise a reused request_id would bypass quota admission.
                    if value["state"] in {"released", "expired"}:
                        raise QuotaReservationError(
                            "request_id belongs to a released or expired reservation and cannot be reused"
                        )
                    return value
                active = self.db.execute(
                    "SELECT COALESCE(SUM(reserved_tokens),0) FROM quota_reservations WHERE key_id=? AND state IN ('reserved','committing')",
                    (key_id,),
                ).fetchone()[0]
                if quota_limit is not None and int(completed_tokens) + int(active or 0) + estimated_tokens > quota_limit:
                    raise QuotaExceeded("Estimated request usage exceeds remaining API-key token quota")
                self.db.execute(
                    """INSERT INTO quota_reservations
                       (request_id,key_id,reserved_tokens,actual_tokens,state,created_at,expires_at,updated_at)
                       VALUES(?,?,?,NULL,'reserved',?,?,?)""",
                    (request_id, key_id, estimated_tokens, now, expires, now),
                )
        return self.get(request_id) or {}

    def reserve_for_key(self, api_keys: Any, request_id: str, key_id: str, estimated_tokens: int) -> dict[str, Any]:
        item = getattr(api_keys, "keys", {}).get(str(key_id))
        if item is None:
            raise QuotaReservationError("Unknown API key")
        public = item.public() if callable(getattr(item, "public", None)) else {}
        if not public.get("enabled", False):
            raise QuotaReservationError("API key is disabled, expired or quota-exhausted")
        return self.reserve(
            request_id, str(key_id), estimated_tokens,
            quota_limit=item.token_quota,
            completed_tokens=max(0, int(item.tokens_used or 0)),
        )

    def begin_commit(self, request_id: str, key_id: str, actual_tokens: int) -> dict[str, Any]:
        self._validate(request_id, key_id, actual_tokens)
        now = time.time()
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT key_id,reserved_tokens,actual_tokens,state FROM quota_reservations WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown quota reservation")
                if row[0] != key_id:
                    raise QuotaReservationError("Reservation API key mismatch")
                if row[3] in {"released", "expired"}:
                    raise QuotaReservationError("Released or expired reservation cannot be committed")
                previous = max(0, int(row[2] or 0))
                actual = max(previous, actual_tokens)
                if row[3] == "settled" and actual == previous:
                    return self.get(request_id) or {}
                self.db.execute(
                    """UPDATE quota_reservations
                       SET state='committing',reserved_tokens=MAX(reserved_tokens,?),
                           actual_tokens=?,updated_at=? WHERE request_id=?""",
                    (actual, actual, now, request_id),
                )
        return self.get(request_id) or {}

    def mark_settled(self, request_id: str, key_id: str, actual_tokens: int) -> dict[str, Any]:
        self._validate(request_id, key_id, actual_tokens)
        now = time.time()
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT key_id,actual_tokens,state FROM quota_reservations WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown quota reservation")
                if row[0] != key_id:
                    raise QuotaReservationError("Reservation API key mismatch")
                if row[2] not in {"committing", "settled"}:
                    raise QuotaReservationError("Reservation must enter committing before settlement")
                actual = max(int(row[1] or 0), actual_tokens)
                self.db.execute(
                    "UPDATE quota_reservations SET state='settled',actual_tokens=?,updated_at=? WHERE request_id=?",
                    (actual, now, request_id),
                )
        return self.get(request_id) or {}

    def release(self, request_id: str, key_id: str) -> bool:
        now = time.time()
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT key_id,state FROM quota_reservations WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    return False
                if row[0] != key_id:
                    raise QuotaReservationError("Reservation API key mismatch")
                if row[1] in {"released", "expired", "settled"}:
                    return False
                if row[1] == "committing":
                    raise QuotaReservationError("Committing usage must be reconciled, not released")
                self.db.execute(
                    "UPDATE quota_reservations SET state='released',updated_at=? WHERE request_id=?",
                    (now, request_id),
                )
                return True

    def expire_reservations(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                cursor = self.db.execute(
                    """UPDATE quota_reservations SET state='expired',updated_at=?
                       WHERE state='reserved' AND expires_at<=?""",
                    (current, current),
                )
                return int(cursor.rowcount)

    def reconcile(self, api_keys: Any) -> int:
        """Settle interrupted commits only when ApiKeyStore proves the request charge."""
        with self.lock:
            rows = self.db.execute(
                """SELECT request_id,key_id,actual_tokens FROM quota_reservations
                   WHERE state='committing'"""
            ).fetchall()
        reconciled = 0
        for request_id, key_id, actual_tokens in rows:
            item = getattr(api_keys, "keys", {}).get(str(key_id))
            ledger = getattr(item, "charged_requests", None) if item else None
            if not isinstance(ledger, dict):
                # Production ApiKeyStore keeps the ledger on ManagedApiKey;
                # adapters may expose the same idempotency proof at store level.
                ledger = getattr(api_keys, "charged_requests", None)
            charged = max(0, int((ledger or {}).get(request_id, 0) or 0))
            expected = max(0, int(actual_tokens or 0))
            # A zero/missing usage estimate is not proof that a completed
            # request consumed zero tokens. Keep its reservation active until
            # the existing ApiKeyStore ledger proves a positive charge.
            if charged > 0 and charged >= expected:
                self.mark_settled(request_id, key_id, max(expected, charged))
                reconciled += 1
        return reconciled

    def _list_active_reservations(self) -> list[dict[str, Any]]:
        """Return every in-flight reservation for startup terminal recovery."""
        with self.lock:
            rows = self.db.execute(
                """SELECT request_id,key_id,reserved_tokens,actual_tokens,state,created_at,expires_at,updated_at
                   FROM quota_reservations
                   WHERE state IN ('reserved','committing')
                   ORDER BY created_at ASC"""
            ).fetchall()
        return [value for row in rows if (value := self._row(row)) is not None]

    async def recover_committing(self, telemetry: Any, api_keys: Any) -> int:
        """Replay persisted terminal telemetry for every active reservation.

        TelemetryStore appends a terminal record before invoking its accounting
        callback. A crash can therefore leave the reservation either reserved
        (before begin_commit/callback) or committing (during settlement).
        Recover both states before expiring old reservations. ApiKeyStore
        remains the sole charge authority and de-duplicates by request_id.
        """
        reservations = self._list_active_reservations()
        request_ids = {str(row["request_id"]) for row in reservations}
        if not request_ids:
            return 0
        get_many = getattr(telemetry, "get_many_persisted", None)
        getter = getattr(telemetry, "get", None)
        if callable(get_many):
            records = get_many(request_ids)
        elif callable(getter):
            records = {request_id: getter(request_id) for request_id in request_ids}
        else:
            return 0
        replayed = 0
        terminal_statuses = {"completed", "error", "cancelled", "stalled", "failed", "timeout"}
        for reservation in reservations:
            record = records.get(str(reservation["request_id"]))
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "").lower() not in terminal_statuses:
                continue
            result = await self.commit_completed_usage(record, api_keys)
            if result == "settled":
                replayed += 1
        return replayed

    async def commit_completed_usage(self, record: dict[str, Any], api_keys: Any) -> str:
        """Coordinate settlement without becoming a second completed-usage ledger."""
        request_id = str(record.get("request_id") or "")
        key_id = str(record.get("api_key_id") or "")
        if not request_id or not key_id:
            return "ignored"
        status = str(record.get("status") or "").lower()
        if status in {"error", "cancelled", "stalled", "failed", "timeout"}:
            self.release(request_id, key_id)
            return "released"
        if status != "completed":
            # TelemetryStore calls the accounting hook for queued/running as
            # well as terminal snapshots. Nonterminal updates must never
            # release a live reservation.
            return "pending"
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        raw_actual = usage.get("total_tokens")
        # Token counts are integral protocol values. Do not truncate floats or
        # coerce malformed strings into a smaller charge.
        actual = raw_actual if type(raw_actual) is int and raw_actual > 0 else 0
        reservation = self.get(request_id)
        # Uploaded-audio conversations can legitimately produce no text
        # transcript and have no provider-verified audio usage yet. In that
        # narrow, explicitly estimated zero-text case, release the admission
        # hold instead of freezing a permanent committing reservation. This
        # does not claim that provider-side audio cost was zero.
        if (
            reservation
            and reservation["state"] == "reserved"
            and actual == 0
            and str(record.get("request_type") or "") == "voice_conversation"
            and usage.get("estimated") is True
        ):
            self.release(request_id, key_id)
            return "released_zero_text_estimate"
        if reservation and reservation["key_id"] != key_id:
            raise QuotaReservationError("Completion API key does not match the request reservation")
        if reservation and reservation["state"] not in {"released", "expired"}:
            if actual <= 0:
                # Fail closed: a completed request with absent/zero usage must
                # not release its in-flight reservation. Freeze it in
                # committing so startup/admin reconciliation can settle only
                # after ApiKeyStore proves a positive request charge.
                self.begin_commit(request_id, key_id, 0)
                return "usage_pending"
            self.begin_commit(request_id, key_id, actual)
            await api_keys.account_completed_usage(record)
            # ApiKeyStore is the sole charge authority. Its API intentionally
            # returns no status, so prove the durable request ledger was
            # updated before dropping the in-flight reservation. A missing
            # key or interrupted/no-op charge must remain recoverable.
            item = getattr(api_keys, "keys", {}).get(key_id)
            ledger = getattr(item, "charged_requests", None) if item else None
            if not isinstance(ledger, dict):
                # Production ApiKeyStore persists the ledger on ManagedApiKey;
                # lightweight accounting adapters may expose the same
                # idempotency ledger directly on the store.
                ledger = getattr(api_keys, "charged_requests", None)
            raw_charged = (ledger or {}).get(request_id, 0) if isinstance(ledger, dict) else 0
            charged = raw_charged if type(raw_charged) is int and raw_charged > 0 else 0
            if charged < actual:
                return "charge_pending"
            self.mark_settled(request_id, key_id, max(actual, charged))
            return "settled"
        # Preserve legacy final accounting for requests admitted before this
        # reservation journal was installed or whose reservation expired.
        await api_keys.account_completed_usage(record)
        return "accounted_without_reservation"

    def close(self) -> None:
        self.db.close()
