from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .timezone_utils import beijing_now_iso


USER_SESSION_COOKIE = "chat2api_user_session"
USER_COMMERCE_VERSION = 1


class WalletReservationError(RuntimeError):
    pass


class InsufficientWalletBalance(WalletReservationError):
    pass


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Reference rates are intentionally persisted as editable seed data. They are
# not used as a claim about the upstream implementation behind chat2api.
DEFAULT_MODEL_PRICING: list[dict[str, Any]] = [
    {"model_id": "gpt-6-astra", "name": "GPT-6 Astra", "enabled": False, "input_usd_per_million": 10.0, "cached_input_usd_per_million": 1.0, "output_usd_per_million": 50.0},
    {"model_id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "enabled": True, "input_usd_per_million": 4.0, "cached_input_usd_per_million": 0.4, "output_usd_per_million": 20.0},
    {"model_id": "gpt-5.6", "name": "GPT-5.6", "enabled": True, "input_usd_per_million": 4.0, "cached_input_usd_per_million": 0.4, "output_usd_per_million": 20.0},
    {"model_id": "gpt-5.6-terra", "name": "GPT-5.6 Terra", "enabled": True, "input_usd_per_million": 2.0, "cached_input_usd_per_million": 0.2, "output_usd_per_million": 12.0},
    {"model_id": "gpt-5.6-luna", "name": "GPT-5.6 Luna", "enabled": True, "input_usd_per_million": 0.2, "cached_input_usd_per_million": 0.02, "output_usd_per_million": 1.2},
    {"model_id": "gpt-5.5", "name": "GPT-5.5", "enabled": True, "input_usd_per_million": 5.0, "cached_input_usd_per_million": 0.5, "output_usd_per_million": 30.0},
    {"model_id": "gpt-5.4", "name": "GPT-5.4", "enabled": True, "input_usd_per_million": 2.5, "cached_input_usd_per_million": 0.25, "output_usd_per_million": 15.0},
    {"model_id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "enabled": True, "input_usd_per_million": 0.75, "cached_input_usd_per_million": 0.075, "output_usd_per_million": 4.5},
    {"model_id": "gpt-5.3-codex", "name": "GPT-5.3 Codex", "enabled": True, "input_usd_per_million": 1.75, "cached_input_usd_per_million": 0.175, "output_usd_per_million": 14.0},
    {"model_id": "gpt-5.2", "name": "GPT-5.2", "enabled": True, "input_usd_per_million": 1.75, "cached_input_usd_per_million": 0.175, "output_usd_per_million": 14.0},
]


def _now() -> str:
    return beijing_now_iso()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _token_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _password_hash(password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return base64.urlsafe_b64encode(salt).decode("ascii"), _password_hash(password, salt)


def _verify_password(password: str, salt_text: str, expected: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        actual = _password_hash(password, salt)
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)


class UserAccountStore:
    def __init__(self, data_dir: Path, session_ttl_seconds: int = 7 * 24 * 3600) -> None:
        self.path = data_dir / "user_accounts.json"
        self.session_ttl_seconds = max(3600, int(session_ttl_seconds))
        self.lock = threading.RLock()
        self.users: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            users = payload.get("users") if isinstance(payload, dict) else None
            sessions = payload.get("sessions") if isinstance(payload, dict) else None
            if isinstance(users, list):
                self.users = {str(row.get("user_id")): dict(row) for row in users if isinstance(row, dict) and row.get("user_id")}
            if isinstance(sessions, dict):
                self.sessions = {str(key): dict(value) for key, value in sessions.items() if isinstance(value, dict)}
            self._cleanup_sessions(save=False)
        except (OSError, ValueError, TypeError):
            self.users = {}
            self.sessions = {}

    def _save(self) -> None:
        _atomic_json(self.path, {"version": USER_COMMERCE_VERSION, "users": list(self.users.values()), "sessions": self.sessions})

    def _cleanup_sessions(self, *, save: bool = True) -> None:
        now = time.time()
        changed = False
        for digest, row in list(self.sessions.items()):
            try:
                expires_at = float(row.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at <= now or str(row.get("user_id") or "") not in self.users:
                self.sessions.pop(digest, None)
                changed = True
        if changed and save:
            self._save()

    def register(self, email: str, password: str, display_name: str = "") -> dict[str, Any]:
        normalized = str(email or "").strip().lower()
        if len(normalized) > 254 or not _EMAIL_RE.match(normalized):
            raise ValueError("请输入有效邮箱地址")
        if len(password) < 8 or len(password) > 256:
            raise ValueError("密码长度需要为 8-256 个字符")
        with self.lock:
            if any(str(row.get("email") or "").lower() == normalized for row in self.users.values()):
                raise ValueError("该邮箱已经注册")
            user_id = "usr_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
            salt, password_hash = _password_record(password)
            now = _now()
            row = {
                "user_id": user_id,
                "email": normalized,
                "display_name": str(display_name or "").strip()[:80] or normalized.split("@", 1)[0][:80],
                "password_salt": salt,
                "password_hash": password_hash,
                "enabled": True,
                "key_ids": [],
                "created_at": now,
                "updated_at": now,
            }
            self.users[user_id] = row
            self._save()
            return self.public(row)

    def verify(self, email: str, password: str) -> dict[str, Any] | None:
        normalized = str(email or "").strip().lower()
        with self.lock:
            row = next((item for item in self.users.values() if str(item.get("email") or "").lower() == normalized), None)
            if not row or not row.get("enabled"):
                return None
            if not _verify_password(password, str(row.get("password_salt") or ""), str(row.get("password_hash") or "")):
                return None
            return self.public(row)

    def create_session(self, user_id: str) -> str:
        with self.lock:
            if user_id not in self.users or not self.users[user_id].get("enabled"):
                raise KeyError("用户不存在或已停用")
            self._cleanup_sessions(save=False)
            token = secrets.token_urlsafe(40)
            self.sessions[_token_hash(token)] = {"user_id": user_id, "expires_at": time.time() + self.session_ttl_seconds}
            self._save()
            return token

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        digest = _token_hash(token)
        with self.lock:
            row = self.sessions.get(digest)
            if not row:
                return None
            try:
                expired = float(row.get("expires_at") or 0) <= time.time()
            except (TypeError, ValueError):
                expired = True
            user = self.users.get(str(row.get("user_id") or ""))
            if expired or not user or not user.get("enabled"):
                self.sessions.pop(digest, None)
                self._save()
                return None
            return self.public(user)

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self.lock:
            if self.sessions.pop(_token_hash(token), None) is not None:
                self._save()

    def public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": str(row.get("user_id") or ""),
            "email": str(row.get("email") or ""),
            "display_name": str(row.get("display_name") or ""),
            "enabled": bool(row.get("enabled", True)),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def update_profile(self, user_id: str, *, display_name: str) -> dict[str, Any]:
        name = str(display_name or "").strip()
        if not name:
            raise ValueError("昵称不能为空")
        with self.lock:
            row = self.users.get(user_id)
            if not row:
                raise KeyError("用户不存在")
            row["display_name"] = name[:80]
            row["updated_at"] = _now()
            self._save()
            return self.public(row)

    def attach_key(self, user_id: str, key_id: str) -> None:
        with self.lock:
            row = self.users.get(user_id)
            if not row:
                raise KeyError("用户不存在")
            ids = [str(value) for value in row.get("key_ids") or []]
            if key_id not in ids:
                ids.append(key_id)
                row["key_ids"] = ids
                row["updated_at"] = _now()
                self._save()

    def key_ids(self, user_id: str) -> set[str]:
        with self.lock:
            row = self.users.get(user_id) or {}
            return {str(value) for value in row.get("key_ids") or [] if value}

    def owns_key(self, user_id: str, key_id: str) -> bool:
        return str(key_id or "") in self.key_ids(user_id)

    def owner_for_key(self, key_id: str) -> str | None:
        wanted = str(key_id or "")
        if not wanted:
            return None
        with self.lock:
            for user_id, row in self.users.items():
                if wanted in {str(value) for value in row.get("key_ids") or []}:
                    return user_id
        return None

    def admin_list(self) -> list[dict[str, Any]]:
        """Return a safe administrator projection; never expose password/session hashes."""
        with self.lock:
            self._cleanup_sessions(save=True)
            session_counts: dict[str, int] = {}
            for session in self.sessions.values():
                user_id = str(session.get("user_id") or "")
                if user_id:
                    session_counts[user_id] = session_counts.get(user_id, 0) + 1
            rows = []
            for user_id, row in self.users.items():
                item = self.public(row)
                item["key_count"] = len({str(value) for value in row.get("key_ids") or [] if value})
                item["active_sessions"] = session_counts.get(user_id, 0)
                rows.append(item)
            rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return rows

    def admin_get(self, user_id: str) -> dict[str, Any] | None:
        wanted = str(user_id or "")
        return next((row for row in self.admin_list() if row["user_id"] == wanted), None)

    def is_enabled(self, user_id: str) -> bool:
        with self.lock:
            row = self.users.get(str(user_id or ""))
            return bool(row and row.get("enabled", True))

    def set_enabled(self, user_id: str, enabled: bool) -> tuple[dict[str, Any], int]:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self.lock:
            row = self.users.get(str(user_id))
            if not row:
                raise KeyError("用户不存在")
            row["enabled"] = enabled
            row["updated_at"] = _now()
            revoked = 0
            if not enabled:
                for digest, session in list(self.sessions.items()):
                    if str(session.get("user_id") or "") == str(user_id):
                        self.sessions.pop(digest, None)
                        revoked += 1
            self._save()
            return self.public(row), revoked

    def revoke_user_sessions(self, user_id: str) -> int:
        with self.lock:
            if str(user_id) not in self.users:
                raise KeyError("用户不存在")
            revoked = 0
            for digest, session in list(self.sessions.items()):
                if str(session.get("user_id") or "") == str(user_id):
                    self.sessions.pop(digest, None)
                    revoked += 1
            if revoked:
                self._save()
            return revoked


class PricingStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "user_pricing.json"
        self.lock = threading.RLock()
        self.payload: dict[str, Any] = {
            "version": USER_COMMERCE_VERSION,
            "billing_enabled": False,
            "usd_cny_rate": 7.2,
            "price_reference": "OpenAI public pricing reference; administrator editable",
            "models": [dict(row) for row in DEFAULT_MODEL_PRICING],
            "updated_at": _now(),
        }
        self._load()

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        version = payload.get("version", USER_COMMERCE_VERSION)
        if type(version) is not int or version != USER_COMMERCE_VERSION:
            raise ValueError("pricing configuration version is unsupported")
        if type(payload.get("billing_enabled")) is not bool:
            raise ValueError("pricing billing_enabled must be boolean")
        raw_rate = payload.get("usd_cny_rate")
        if isinstance(raw_rate, bool):
            raise ValueError("pricing USD/CNY rate must be numeric")
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("pricing USD/CNY rate must be numeric") from exc
        if not math.isfinite(rate) or rate <= 0 or rate > 100:
            raise ValueError("pricing USD/CNY rate must be finite and between 0 and 100")
        models = payload.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("pricing models must be a non-empty list")
        seen: set[str] = set()
        for row in models:
            if not isinstance(row, dict):
                raise ValueError("pricing model row must be an object")
            model_id = row.get("model_id")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 120 or model_id in seen:
                raise ValueError("pricing model identity is invalid or duplicated")
            seen.add(model_id)
            if type(row.get("enabled", True)) is not bool:
                raise ValueError("pricing model enabled must be boolean")
            for field in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million"):
                raw_value = row.get(field, 0)
                if isinstance(raw_value, bool):
                    raise ValueError(f"pricing {field} must be numeric")
                try:
                    value = float(raw_value or 0)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"pricing {field} must be numeric") from exc
                if not math.isfinite(value) or value < 0 or value > 100000:
                    raise ValueError(f"pricing {field} must be finite and within range")

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("pricing configuration root must be an object")
            candidate = copy.deepcopy(self.payload)
            candidate.update(raw)
            self._validate_payload(candidate)
            self.payload = candidate
        except (OSError, ValueError, TypeError, OverflowError) as error:
            # Pricing is an accounting input, not a disposable cache. Never
            # replace unreadable rates with defaults and continue billing.
            raise RuntimeError(
                "Pricing configuration is unreadable; refusing to use fallback rates"
            ) from error

    def _save(self) -> None:
        _atomic_json(self.path, self.payload)

    def public(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.payload, ensure_ascii=False))

    def update(self, *, billing_enabled: bool, usd_cny_rate: float, models: list[dict[str, Any]]) -> dict[str, Any]:
        if type(billing_enabled) is not bool or isinstance(usd_cny_rate, bool):
            raise ValueError("计费开关必须为布尔值，美元兑人民币汇率必须为数字")
        try:
            rate = float(usd_cny_rate)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("美元兑人民币计费换算率必须为数字") from exc
        if not math.isfinite(rate) or rate <= 0 or rate > 100:
            raise ValueError("美元兑人民币计费换算率必须为有限正数且不超过 100")
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in models:
            if not isinstance(raw, dict):
                raise ValueError("模型价格配置必须为对象")
            model_id = str(raw.get("model_id") or "").strip()
            if not model_id or len(model_id) > 120 or model_id in seen:
                continue
            enabled = raw.get("enabled", True)
            if type(enabled) is not bool:
                raise ValueError(f"模型 {model_id} 的启用状态必须为布尔值")
            values: dict[str, float] = {}
            for key in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million"):
                raw_value = raw.get(key, 0)
                if raw_value is None or raw_value == "":
                    raw_value = 0
                if isinstance(raw_value, bool):
                    raise ValueError(f"模型 {model_id} 的价格必须为数字")
                try:
                    value = float(raw_value)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"模型 {model_id} 的价格必须为数字") from exc
                if not math.isfinite(value) or value < 0 or value > 100000:
                    raise ValueError(f"模型 {model_id} 的价格必须为有限非负数且不超过 100000")
                values[key] = value
            seen.add(model_id)
            cleaned.append({
                "model_id": model_id,
                "name": str(raw.get("name") or model_id).strip()[:120],
                "enabled": enabled,
                **values,
            })
        if not cleaned:
            raise ValueError("至少需要保留一个模型价格配置")
        candidate = {
            "billing_enabled": billing_enabled,
            "usd_cny_rate": rate,
            "models": cleaned,
        }
        validation = copy.deepcopy(self.payload)
        validation.update(candidate)
        self._validate_payload(validation)
        with self.lock:
            previous = copy.deepcopy(self.payload)
            try:
                self.payload.update({**candidate, "updated_at": _now()})
                self._save()
            except Exception:
                self.payload = previous
                raise
            return self.public()

    def model(self, model_id: str) -> dict[str, Any] | None:
        wanted = str(model_id or "").strip().lower()
        aliases = {"gpt-5.6": "gpt-5.6-sol"}
        wanted = aliases.get(wanted, wanted)
        with self.lock:
            for row in self.payload.get("models") or []:
                current = str(row.get("model_id") or "").lower()
                current = aliases.get(current, current)
                if current == wanted:
                    return dict(row)
        return None


class BillingStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "user_billing.json"
        self.lock = threading.RLock()
        self.wallets: dict[str, int] = {}
        self.charges: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.reservations: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("billing ledger root must be an object")
            # Version 1 is the only supported on-disk schema. Missing version
            # is accepted as the original v1 legacy representation, but a
            # future/unknown version must never be rewritten by this reader.
            ledger_version = payload.get("version", USER_COMMERCE_VERSION)
            if type(ledger_version) is not int or ledger_version != USER_COMMERCE_VERSION:
                raise ValueError("billing ledger version is unsupported")

            raw_wallets = payload.get("wallets", {})
            if not isinstance(raw_wallets, dict):
                raise ValueError("billing ledger wallets must be an object")
            wallets: dict[str, int] = {}
            for user_id, balance in raw_wallets.items():
                # bool is an int subclass and int(1.5) silently truncates.
                # Neither is an acceptable representation of integer cents.
                if not isinstance(user_id, str) or not user_id or type(balance) is not int:
                    raise ValueError("billing ledger wallet balance must be integer cents")
                wallets[user_id] = balance

            def read_rows(
                name: str,
                id_field: str,
                integer_fields: tuple[str, ...],
                nullable_integer_fields: tuple[str, ...] = (),
                required_fields: tuple[str, ...] = (),
            ) -> dict[str, dict[str, Any]]:
                raw_rows = payload.get(name, {})
                if not isinstance(raw_rows, dict):
                    raise ValueError(f"billing ledger {name} must be an object")
                rows: dict[str, dict[str, Any]] = {}
                for row_id, raw_row in raw_rows.items():
                    if not isinstance(row_id, str) or not row_id or not isinstance(raw_row, dict):
                        raise ValueError(f"billing ledger {name} contains an invalid row")
                    row = dict(raw_row)
                    missing = [field for field in required_fields if field not in row]
                    if missing:
                        raise ValueError(
                            f"billing ledger {name} is missing required fields: {', '.join(missing)}"
                        )
                    if row.get(id_field) != row_id:
                        raise ValueError(f"billing ledger {name}.{id_field} does not match its ledger key")
                    if not isinstance(row.get("user_id"), str) or not row["user_id"]:
                        raise ValueError(f"billing ledger {name}.user_id must be a non-empty string")
                    for field in integer_fields:
                        if field in row and (type(row[field]) is not int or row[field] < 0):
                            raise ValueError(f"billing ledger {name}.{field} must be non-negative integer units")
                    for field in nullable_integer_fields:
                        value = row.get(field)
                        if field in row and value is not None and (type(value) is not int or value < 0):
                            raise ValueError(f"billing ledger {name}.{field} must be null or non-negative integer units")
                    rows[row_id] = row
                return rows

            # Parse and validate every collection into locals before mutating
            # the store. A malformed secondary row must not leave a partially
            # loaded wallet projection available to callers.
            charges = read_rows(
                "charges",
                "request_id",
                ("input_tokens", "cached_input_tokens", "output_tokens", "cost_usd_micros", "cost_cny_cents"),
                required_fields=(
                    "request_id", "user_id", "model_id", "input_tokens",
                    "cached_input_tokens", "output_tokens", "cost_usd_micros",
                    "cost_cny_cents", "debited", "usd_cny_rate",
                    "price_snapshot", "created_at",
                ),
            )
            orders = read_rows(
                "orders", "order_id", ("amount_cents",),
                required_fields=(
                    "order_id", "merchant_trade_no", "user_id", "amount_cents",
                    "channel", "status", "provider_trade_no", "pay_url",
                    "qr_image_url", "qr_payload", "created_at", "paid_at", "updated_at",
                ),
            )
            reservations = read_rows(
                "reservations",
                "request_id",
                ("estimated_input_tokens", "estimated_output_tokens", "reserved_cny_cents"),
                ("actual_cny_cents",),
                required_fields=(
                    "request_id", "user_id", "model_id", "estimated_input_tokens",
                    "estimated_output_tokens", "reserved_cny_cents", "actual_cny_cents",
                    "price_snapshot", "usd_cny_rate", "state", "created_at", "updated_at",
                ),
            )
            refunds = read_rows(
                "refunds", "refund_id", ("amount_cents", "charge_cny_cents"),
                required_fields=(
                    "refund_id", "request_id", "user_id", "amount_cents",
                    "reason", "charge_cny_cents", "created_at",
                ),
            )

            # These fields control money movement and reservation accounting;
            # accepting truthy strings or unknown states would let corrupted
            # records masquerade as paid/debited or disappear from active holds.
            def validate_price_snapshot(snapshot: Any, collection: str, model_id: str) -> None:
                if (
                    not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get("model_id"), str)
                    or not snapshot["model_id"]
                ):
                    raise ValueError(f"billing ledger {collection}.price_snapshot identity is invalid")
                if "enabled" in snapshot and type(snapshot["enabled"]) is not bool:
                    raise ValueError(f"billing ledger {collection}.price_snapshot.enabled must be boolean")
                for field in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million"):
                    value = snapshot.get(field, 0)
                    if type(value) not in {int, float} or not math.isfinite(float(value)) or value < 0 or value > 100000:
                        raise ValueError(f"billing ledger {collection}.price_snapshot.{field} is invalid")

            for charge in charges.values():
                if type(charge.get("debited")) is not bool:
                    raise ValueError("billing ledger charges.debited must be boolean")
                if (
                    not isinstance(charge.get("model_id"), str) or not charge["model_id"]
                    or not isinstance(charge.get("created_at"), str) or not charge["created_at"]
                    or type(charge.get("usage_is_estimated", False)) is not bool
                ):
                    raise ValueError("billing ledger charge metadata is invalid")
                validate_price_snapshot(charge.get("price_snapshot"), "charges", charge["model_id"])
                rate = charge.get("usd_cny_rate")
                if type(rate) not in {int, float} or not math.isfinite(float(rate)) or rate < 0:
                    raise ValueError("billing ledger charges.usd_cny_rate must be finite and non-negative")
            allowed_order_states = {
                "pending", "reviewing", "rejected", "paid",
                "failed", "cancelled", "expired",
            }
            allowed_order_channels = {"alipay", "wechat", "paypal", "usdt"}
            for order in orders.values():
                if order.get("status") not in allowed_order_states:
                    raise ValueError("billing ledger orders.status is unsupported")
                if order.get("channel") not in allowed_order_channels:
                    raise ValueError("billing ledger orders.channel is unsupported")
                if (
                    type(order.get("amount_cents")) is not int
                    or not 1 <= order["amount_cents"] <= 10_000_000
                    or not isinstance(order.get("merchant_trade_no"), str)
                    or not order["merchant_trade_no"]
                    or not isinstance(order.get("provider_trade_no"), str)
                    or any(not isinstance(order.get(field), str) for field in ("pay_url", "qr_image_url", "qr_payload"))
                    or not isinstance(order.get("created_at"), str) or not order["created_at"]
                    or not isinstance(order.get("updated_at"), str) or not order["updated_at"]
                    or (order.get("paid_at") is not None and not isinstance(order.get("paid_at"), str))
                ):
                    raise ValueError("billing ledger payment order metadata is invalid")
            for reservation in reservations.values():
                if reservation.get("state") not in {"reserved", "settled", "released"}:
                    raise ValueError("billing ledger reservations.state is unsupported")
                if (
                    not isinstance(reservation.get("model_id"), str) or not reservation["model_id"]
                    or reservation["reserved_cny_cents"] <= 0
                    or not isinstance(reservation.get("created_at"), str) or not reservation["created_at"]
                    or not isinstance(reservation.get("updated_at"), str) or not reservation["updated_at"]
                ):
                    raise ValueError("billing ledger reservation metadata is invalid")
                validate_price_snapshot(reservation.get("price_snapshot"), "reservations", reservation["model_id"])
                rate = reservation.get("usd_cny_rate")
                if type(rate) not in {int, float} or not math.isfinite(float(rate)) or rate < 0:
                    raise ValueError("billing ledger reservations.usd_cny_rate must be finite and non-negative")
                if reservation["state"] == "settled" and reservation.get("actual_cny_cents") is None:
                    raise ValueError("settled wallet reservation must include actual cost")
            for refund in refunds.values():
                if (
                    not re.fullmatch(r"rf_[A-Za-z0-9_-]{8,100}", str(refund.get("refund_id") or ""))
                    or not isinstance(refund.get("request_id"), str) or not refund["request_id"]
                    or not isinstance(refund.get("reason"), str)
                    or not 5 <= len(refund["reason"].strip()) <= 500
                    or refund["amount_cents"] <= 0
                    or refund["charge_cny_cents"] <= 0
                    or not isinstance(refund.get("created_at"), str) or not refund["created_at"]
                ):
                    raise ValueError("billing ledger refund identity, amount or reason is invalid")

            # A refund is a derived credit against one immutable, debited
            # charge. Reject orphaned, cross-owner or cumulatively excessive
            # journal entries instead of silently accepting inconsistent money.
            refunded_by_request: dict[str, int] = {}
            for refund in refunds.values():
                request_id = str(refund.get("request_id") or "")
                charge = charges.get(request_id)
                if (
                    not charge
                    or charge.get("debited") is not True
                    or str(charge.get("user_id") or "") != str(refund.get("user_id") or "")
                    or type(charge.get("cost_cny_cents")) is not int
                    or refund.get("charge_cny_cents") != charge.get("cost_cny_cents")
                ):
                    raise ValueError("billing ledger refund does not match a debited owner charge")
                refunded_by_request[request_id] = (
                    refunded_by_request.get(request_id, 0) + int(refund["amount_cents"])
                )
                if refunded_by_request[request_id] > int(charge["cost_cny_cents"]):
                    raise ValueError("billing ledger refunds exceed the original wallet charge")

            self.wallets = wallets
            self.charges = charges
            self.orders = orders
            self.reservations = reservations
            self.refunds = refunds
        except (OSError, ValueError, TypeError) as error:
            # This is a money ledger, not a disposable cache. Silently treating
            # unreadable or corrupt persisted balances as an empty store would
            # let a later credit/charge overwrite the only on-disk record.
            # Fail closed and preserve the original bytes for offline recovery.
            raise RuntimeError(
                "Billing ledger is unreadable; refusing to replace it with empty wallet state"
            ) from error

    def _save(self) -> None:
        _atomic_json(self.path, {
            "version": USER_COMMERCE_VERSION,
            "wallets": self.wallets,
            "charges": self.charges,
            "orders": self.orders,
            "reservations": self.reservations,
            "refunds": self.refunds,
        })

    def balance_cents(self, user_id: str) -> int:
        with self.lock:
            return int(self.wallets.get(user_id, 0))

    def reserved_cents(self, user_id: str) -> int:
        with self.lock:
            return sum(
                max(0, int(row.get("reserved_cny_cents") or 0))
                for row in self.reservations.values()
                if row.get("user_id") == user_id and row.get("state") == "reserved"
            )

    def active_reservations_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return durable active holds so terminal recovery is not limited to the telemetry cache."""
        with self.lock:
            rows = [
                dict(row) for row in self.reservations.values()
                if row.get("user_id") == user_id and row.get("state") == "reserved"
            ]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""))

    def active_reservation_user_ids(self) -> list[str]:
        with self.lock:
            return sorted({
                str(row.get("user_id") or "")
                for row in self.reservations.values()
                if row.get("state") == "reserved" and row.get("user_id")
            })

    def available_balance_cents(self, user_id: str) -> int:
        with self.lock:
            reserved = sum(
                max(0, int(row.get("reserved_cny_cents") or 0))
                for row in self.reservations.values()
                if row.get("user_id") == user_id and row.get("state") == "reserved"
            )
            return int(self.wallets.get(user_id, 0)) - reserved

    def reserve_for_request(
        self, user_id: str, request_id: str, *,
        model_id: str, estimated_cny_cents: int,
        input_tokens: int, output_tokens: int,
        price_snapshot: dict[str, Any], usd_cny_rate: float,
    ) -> dict[str, Any] | None:
        if not user_id or not request_id:
            raise WalletReservationError("user_id and request_id are required")
        if type(estimated_cny_cents) is not int or estimated_cny_cents < 0:
            raise WalletReservationError("estimated wallet amount must be non-negative integer cents")
        if estimated_cny_cents == 0:
            return None
        now = _now()
        with self.lock:
            existing_charge = self.charges.get(request_id)
            if existing_charge:
                if str(existing_charge.get("user_id") or "") != user_id:
                    raise WalletReservationError("Request is already charged to another user")
                # A settled request ID is never a new dispatch authorization.
                # Returning a synthetic "settled" hold here would let the
                # caller reach a Worker without any active wallet capacity.
                raise WalletReservationError("request_id was already charged and cannot be reused")
            existing = self.reservations.get(request_id)
            if existing:
                if str(existing.get("user_id") or "") != user_id:
                    raise WalletReservationError("Request wallet reservation belongs to another user")
                if existing.get("state") != "reserved":
                    raise WalletReservationError("request_id belongs to a terminal wallet reservation and cannot be reused")
                if (
                    str(existing.get("model_id") or "") != str(model_id or "")
                    or int(existing.get("reserved_cny_cents") or 0) != estimated_cny_cents
                    or int(existing.get("estimated_input_tokens") or 0) != max(0, int(input_tokens))
                    or int(existing.get("estimated_output_tokens") or 0) != max(0, int(output_tokens))
                ):
                    raise WalletReservationError("Request wallet reservation payload does not match the original admission")
                return dict(existing)
            active = sum(
                max(0, int(row.get("reserved_cny_cents") or 0))
                for row in self.reservations.values()
                if row.get("user_id") == user_id and row.get("state") == "reserved"
            )
            available = int(self.wallets.get(user_id, 0)) - active
            if available < estimated_cny_cents:
                raise InsufficientWalletBalance(
                    f"Insufficient wallet balance: available {available} cents, "
                    f"estimated request requires {estimated_cny_cents} cents"
                )
            row = {
                "request_id": request_id,
                "user_id": user_id,
                "model_id": str(model_id or ""),
                "estimated_input_tokens": max(0, int(input_tokens)),
                "estimated_output_tokens": max(0, int(output_tokens)),
                "reserved_cny_cents": estimated_cny_cents,
                "actual_cny_cents": None,
                "price_snapshot": dict(price_snapshot),
                "usd_cny_rate": float(usd_cny_rate),
                "state": "reserved",
                "created_at": now,
                "updated_at": now,
            }
            self.reservations[request_id] = row
            try:
                self._save()
            except Exception:
                # Admission must not expose a non-durable hold as successful
                # and must not strand the in-memory available balance.
                self.reservations.pop(request_id, None)
                raise
            return dict(row)

    def release_reservation(self, request_id: str, user_id: str, reason: str = "request_not_completed") -> bool:
        with self.lock:
            row = self.reservations.get(str(request_id or ""))
            if row is None:
                return False
            if str(row.get("user_id") or "") != user_id:
                raise WalletReservationError("Request wallet reservation belongs to another user")
            if row.get("state") != "reserved":
                return False
            previous = dict(row)
            row.update({
                "state": "released",
                "release_reason": str(reason or "request_not_completed")[:120],
                "updated_at": _now(),
            })
            try:
                self._save()
            except Exception:
                self.reservations[str(request_id)] = previous
                raise
            return True

    def credit(self, user_id: str, amount_cents: int) -> int:
        # Wallet balances are denominated in integer cents. Do not silently
        # truncate floats or accept bool (an int subclass) at this accounting
        # boundary: callers must pass the exact amount already validated by
        # their payment/order contract.
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError("充值金额必须为正整数分")
        amount = amount_cents
        with self.lock:
            # A failed durable write must not leave a phantom credit available
            # to another request in the current process.
            previous = self.wallets.get(user_id)
            self.wallets[user_id] = int(previous or 0) + amount
            try:
                self._save()
            except Exception:
                if previous is None:
                    self.wallets.pop(user_id, None)
                else:
                    self.wallets[user_id] = previous
                raise
            return self.wallets[user_id]

    def record_charge(self, user_id: str, request_row: dict[str, Any], pricing: PricingStore) -> dict[str, Any] | None:
        request_id = str(request_row.get("request_id") or "")
        if not request_id or str(request_row.get("status") or "") != "completed":
            return None
        with self.lock:
            existing = self.charges.get(request_id)
            if existing:
                if str(existing.get("user_id") or "") != user_id:
                    raise WalletReservationError("Request is already charged to another user")
                reservation = self.reservations.get(request_id)
                if reservation and reservation.get("state") == "reserved":
                    previous = dict(reservation)
                    reservation.update({
                        "state": "settled" if existing.get("debited") else "released",
                        "actual_cny_cents": int(existing.get("cost_cny_cents") or 0),
                        "updated_at": _now(),
                    })
                    try:
                        self._save()
                    except Exception:
                        self.reservations[request_id] = previous
                        raise
                return dict(existing)
            diagnostics = request_row.get("diagnostics") if isinstance(request_row.get("diagnostics"), dict) else {}
            model_id = str(diagnostics.get("actual_model") or request_row.get("requested_model") or "")
            reservation = self.reservations.get(request_id)
            if reservation and str(reservation.get("user_id") or "") != user_id:
                raise WalletReservationError("Request wallet reservation belongs to another user")
            reserved_model = str((reservation or {}).get("model_id") or "")
            if reservation and reservation.get("state") == "reserved" and reserved_model == model_id:
                price = dict(reservation.get("price_snapshot") or {})
                fx = float(reservation.get("usd_cny_rate") or 0)
            else:
                price = pricing.model(model_id)
                if price and price.get("enabled", True) is not True:
                    price = None
                fx = float(pricing.public().get("usd_cny_rate") or 0)
                if not price and reservation and reservation.get("state") == "reserved":
                    # If served_model is unknown or has no configured price,
                    # settle against the requested-model snapshot that was
                    # actually held at admission rather than silently dropping
                    # a priced request's wallet charge.
                    model_id = reserved_model
                    price = dict(reservation.get("price_snapshot") or {})
                    fx = float(reservation.get("usd_cny_rate") or fx)
            if not price:
                if reservation and reservation.get("state") == "reserved":
                    previous = dict(reservation)
                    reservation.update({"state": "released", "release_reason": "pricing_unavailable", "updated_at": _now()})
                    try:
                        self._save()
                    except Exception:
                        self.reservations[request_id] = previous
                        raise
                return None
            usage = request_row.get("usage") if isinstance(request_row.get("usage"), dict) else {}
            if reservation and reservation.get("state") == "reserved":
                # A completed status alone is not usage evidence. In
                # particular, int(None)/int("bad") fallbacks and int(1.5)
                # truncation must never turn a missing or malformed usage
                # payload into a zero-cost settlement that releases a hold.
                raw_input = (
                    usage.get("prompt_tokens")
                    if "prompt_tokens" in usage else usage.get("input_tokens")
                )
                raw_output = (
                    usage.get("completion_tokens")
                    if "completion_tokens" in usage else usage.get("output_tokens")
                )
                raw_total = usage.get("total_tokens")
                raw_cached = usage.get("cached_input_tokens", 0)
                if (
                    type(raw_input) is not int or raw_input < 0
                    or type(raw_output) is not int or raw_output < 0
                    or type(raw_total) is not int or raw_total <= 0
                    or raw_total != raw_input + raw_output
                    or type(raw_cached) is not int or raw_cached < 0 or raw_cached > raw_input
                ):
                    # Keep the original admission hold. A later terminal
                    # replay with valid usage can settle the same request.
                    return None
            input_tokens = max(0, int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0))
            # Cached tokens are a subset of input tokens. A malformed or
            # inconsistent upstream usage payload must not charge more cached
            # input than the entire request contained.
            cached_tokens = min(input_tokens, max(0, int(usage.get("cached_input_tokens") or 0)))
            output_tokens = max(0, int(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
            uncached_tokens = input_tokens - cached_tokens
            usd = (
                uncached_tokens * float(price.get("input_usd_per_million") or 0)
                + cached_tokens * float(price.get("cached_input_usd_per_million") or 0)
                + output_tokens * float(price.get("output_usd_per_million") or 0)
            ) / 1_000_000
            usd_micros = max(0, int(round(usd * 1_000_000)))
            cny_cents = max(0, int(round(usd * fx * 100)))
            billing_enabled = bool(pricing.public().get("billing_enabled"))
            prior_balance = self.wallets.get(user_id)
            prior_reservation = dict(reservation) if reservation else None
            if billing_enabled and cny_cents:
                self.wallets[user_id] = int(self.wallets.get(user_id, 0)) - cny_cents
            row = {
                "request_id": request_id,
                "user_id": user_id,
                "model_id": model_id,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_tokens,
                "output_tokens": output_tokens,
                "cost_usd_micros": usd_micros,
                "cost_cny_cents": cny_cents,
                "debited": billing_enabled,
                "usage_is_estimated": bool(usage.get("estimated", True)),
                "usd_cny_rate": fx,
                "price_snapshot": price,
                "created_at": _now(),
            }
            self.charges[request_id] = row
            if reservation and reservation.get("state") == "reserved":
                reservation.update({
                    "state": "settled" if billing_enabled else "released",
                    "actual_cny_cents": cny_cents,
                    "release_reason": None if billing_enabled else "billing_disabled_before_terminal",
                    "updated_at": _now(),
                })
            try:
                self._save()
            except Exception:
                # The charge and debit are one atomic JSON write. Restore the
                # in-memory ledger if that write failed, otherwise a later
                # retry would falsely see a charge that was never durable.
                self.charges.pop(request_id, None)
                if billing_enabled and cny_cents:
                    if prior_balance is None:
                        self.wallets.pop(user_id, None)
                    else:
                        self.wallets[user_id] = prior_balance
                if prior_reservation is not None:
                    self.reservations[request_id] = prior_reservation
                raise
            return dict(row)

    def charges_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = [dict(row) for row in self.charges.values() if row.get("user_id") == user_id]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def charge_for_request(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.charges.get(str(request_id or ""))
            return dict(row) if row else None

    def refunds_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = [dict(row) for row in self.refunds.values() if row.get("user_id") == user_id]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def refund_charge(
        self, request_id: str, refund_id: str, amount_cents: int, reason: str,
    ) -> dict[str, Any]:
        """Credit back an already debited internal wallet charge, exactly once.

        Not a payment-provider refund. The original request charge stays
        immutable; refund_id is a durable idempotency key in the same JSON
        transaction as the wallet credit.
        """
        request_id = str(request_id or "").strip()
        refund_id = str(refund_id or "").strip()
        reason = str(reason or "").strip()
        if not request_id or not re.fullmatch(r"rf_[A-Za-z0-9_-]{8,100}", refund_id):
            raise WalletReservationError("A valid request_id and refund_id are required")
        if type(amount_cents) is not int or amount_cents <= 0:
            raise WalletReservationError("Refund amount must be positive integer cents")
        if not 5 <= len(reason) <= 500:
            raise WalletReservationError("A refund reason of 5-500 characters is required")
        with self.lock:
            existing = self.refunds.get(refund_id)
            if existing is not None:
                if (
                    str(existing.get("request_id") or "") != request_id
                    or int(existing.get("amount_cents") or 0) != amount_cents
                    or str(existing.get("reason") or "") != reason
                ):
                    raise WalletReservationError("refund_id is already used for another refund payload")
                return dict(existing)
            charge = self.charges.get(request_id)
            if not charge or not bool(charge.get("debited")):
                raise WalletReservationError("Request has no completed debited wallet charge")
            charged = int(charge.get("cost_cny_cents") or 0)
            refunded = sum(
                int(row.get("amount_cents") or 0)
                for row in self.refunds.values()
                if str(row.get("request_id") or "") == request_id
            )
            if charged <= 0 or amount_cents > charged - refunded:
                raise WalletReservationError("Refund exceeds the remaining debited charge")
            user_id = str(charge.get("user_id") or "")
            if not user_id:
                raise WalletReservationError("Charge has no owner")
            old_balance = self.wallets.get(user_id)
            row = {
                "refund_id": refund_id,
                "request_id": request_id,
                "user_id": user_id,
                "amount_cents": amount_cents,
                "reason": reason,
                "charge_cny_cents": charged,
                "created_at": _now(),
            }
            self.wallets[user_id] = int(old_balance or 0) + amount_cents
            self.refunds[refund_id] = row
            try:
                self._save()
            except Exception:
                self.refunds.pop(refund_id, None)
                if old_balance is None:
                    self.wallets.pop(user_id, None)
                else:
                    self.wallets[user_id] = old_balance
                raise
            return dict(row)

    def create_order(self, user_id: str, amount_cents: int, channel: str) -> dict[str, Any]:
        if type(amount_cents) is not int:
            raise ValueError("充值金额必须为整数分")
        amount = amount_cents
        if amount < 1 or amount > 10_000_000:
            raise ValueError("充值金额需要在 0.01 至 100000.00 元之间")
        if channel not in {"alipay", "wechat"}:
            raise ValueError("暂不支持该支付方式")
        order_id = "pay_" + secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        trade_no = (str(int(time.time() * 1000)) + f"{secrets.randbelow(10**12):012d}")[:32]
        row = {
            "order_id": order_id,
            "merchant_trade_no": trade_no,
            "user_id": user_id,
            "amount_cents": amount,
            "channel": channel,
            "status": "pending",
            "provider_trade_no": "",
            "pay_url": "",
            "qr_image_url": "",
            "qr_payload": "",
            "created_at": _now(),
            "paid_at": None,
            "updated_at": _now(),
        }
        with self.lock:
            self.orders[order_id] = row
            try:
                self._save()
            except Exception:
                self.orders.pop(order_id, None)
                raise
            return dict(row)

    def update_order_checkout(self, order_id: str, *, pay_url: str = "", qr_image_url: str = "", qr_payload: str = "") -> dict[str, Any]:
        with self.lock:
            row = self.orders.get(order_id)
            if not row:
                raise KeyError("充值订单不存在")
            previous = dict(row)
            row.update({"pay_url": pay_url, "qr_image_url": qr_image_url, "qr_payload": qr_payload, "updated_at": _now()})
            try:
                self._save()
            except Exception:
                self.orders[order_id] = previous
                raise
            return dict(row)

    def order_by_trade_no(self, trade_no: str) -> dict[str, Any] | None:
        with self.lock:
            row = next((item for item in self.orders.values() if item.get("merchant_trade_no") == trade_no), None)
            return dict(row) if row else None

    def settle_order(self, order_id: str, provider_trade_no: str) -> dict[str, Any]:
        with self.lock:
            row = self.orders.get(order_id)
            if not row:
                raise KeyError("充值订单不存在")
            if row.get("status") == "paid":
                return dict(row)
            previous_order = dict(row)
            row["status"] = "paid"
            row["provider_trade_no"] = str(provider_trade_no or "")[:160]
            row["paid_at"] = _now()
            row["updated_at"] = _now()
            user_id = str(row.get("user_id") or "")
            previous_balance = self.wallets.get(user_id)
            had_balance = user_id in self.wallets
            self.wallets[user_id] = int(previous_balance or 0) + int(row.get("amount_cents") or 0)
            try:
                self._save()
            except Exception:
                self.orders[order_id] = previous_order
                if had_balance:
                    self.wallets[user_id] = int(previous_balance or 0)
                else:
                    self.wallets.pop(user_id, None)
                raise
            return dict(row)

    def settle_usdt_order(self, order_id: str, txid: str) -> dict[str, Any]:
        """Atomically enforce one-TRON-TXID/one-USDT-order wallet settlement."""
        normalized_txid = str(txid or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_txid):
            raise WalletReservationError("A valid 64-character TRON transaction hash is required")
        with self.lock:
            row = self.orders.get(str(order_id or ""))
            if not row or row.get("channel") != "usdt":
                raise KeyError("USDT order does not exist")
            stored_txid = str(row.get("txid") or "").strip().lower()
            if stored_txid != normalized_txid:
                raise WalletReservationError("Submitted TRON transaction hash does not match the order")
            if row.get("status") == "paid":
                return dict(row)
            if row.get("status") != "reviewing":
                raise WalletReservationError("USDT order is not awaiting manual review")
            duplicate = next(
                (
                    item for other_id, item in self.orders.items()
                    if other_id != order_id
                    and item.get("channel") == "usdt"
                    and item.get("status") == "paid"
                    and secrets.compare_digest(str(item.get("txid") or "").strip().lower(), normalized_txid)
                ),
                None,
            )
            if duplicate:
                raise WalletReservationError("TRON transaction hash has already credited another USDT order")
            # RLock keeps the duplicate check and the underlying atomic ledger
            # write in one critical section. settle_order also rolls back its
            # in-memory mutation if persistence fails.
            return self.settle_order(order_id, normalized_txid)

    def orders_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = [dict(row) for row in self.orders.values() if row.get("user_id") == user_id]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)


class PaymentConfigStore:
    def __init__(self, data_dir: Path, secret: str) -> None:
        self.path = data_dir / "user_payment_config.json"
        key = base64.urlsafe_b64encode(hashlib.sha256(str(secret or "chat2api-user-payments").encode("utf-8")).digest())
        self.cipher = Fernet(key)
        self.lock = threading.RLock()
        self.payload: dict[str, Any] = {
            "version": USER_COMMERCE_VERSION,
            "provider": "zpay",
            "enabled": False,
            "pid": "",
            "key_ciphertext": "",
            "alipay_enabled": True,
            "wechat_enabled": True,
            "alipay_cid": "",
            "wechat_cid": "",
            "public_origin": "",
            "updated_at": _now(),
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.payload.update(raw)
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        _atomic_json(self.path, self.payload)

    def secret_key(self) -> str:
        value = str(self.payload.get("key_ciphertext") or "")
        if not value:
            return ""
        try:
            return self.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    def public(self) -> dict[str, Any]:
        with self.lock:
            pid = str(self.payload.get("pid") or "")
            key = self.secret_key()
            return {
                "provider": "zpay",
                "enabled": bool(self.payload.get("enabled")),
                "configured": bool(pid and key),
                "pid": pid,
                "pid_hint": f"{pid[:4]}…{pid[-4:]}" if len(pid) > 8 else pid,
                "key_configured": bool(key),
                "alipay_enabled": bool(self.payload.get("alipay_enabled", True)),
                "wechat_enabled": bool(self.payload.get("wechat_enabled", True)),
                "alipay_cid": str(self.payload.get("alipay_cid") or ""),
                "wechat_cid": str(self.payload.get("wechat_cid") or ""),
                "public_origin": str(self.payload.get("public_origin") or ""),
                "updated_at": self.payload.get("updated_at"),
            }

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        pid = str(data.get("pid") or "").strip()[:80]
        alipay_cid = str(data.get("alipay_cid") or "").strip().replace(" ", "")[:240]
        wechat_cid = str(data.get("wechat_cid") or "").strip().replace(" ", "")[:240]
        for value in (alipay_cid, wechat_cid):
            if value and not re.fullmatch(r"\d+(?:,\d+)*", value):
                raise ValueError("支付渠道 ID 只能填写数字，多个 ID 使用英文逗号分隔")
        origin = str(data.get("public_origin") or "").strip().rstrip("/")[:2048]
        if origin and not origin.startswith("https://"):
            raise ValueError("公网回调地址必须使用 https://")
        with self.lock:
            self.payload.update({
                "enabled": bool(data.get("enabled")),
                "pid": pid,
                "alipay_enabled": bool(data.get("alipay_enabled", True)),
                "wechat_enabled": bool(data.get("wechat_enabled", True)),
                "alipay_cid": alipay_cid,
                "wechat_cid": wechat_cid,
                "public_origin": origin,
                "updated_at": _now(),
            })
            supplied_key = data.get("key")
            if supplied_key is not None and str(supplied_key):
                self.payload["key_ciphertext"] = self.cipher.encrypt(str(supplied_key).encode("utf-8")).decode("ascii")
            if bool(data.get("clear_key")):
                self.payload["key_ciphertext"] = ""
            self._save()
            return self.public()
