from __future__ import annotations

import base64
import hashlib
import json
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

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.payload.update(raw)
            if not isinstance(self.payload.get("models"), list):
                self.payload["models"] = [dict(row) for row in DEFAULT_MODEL_PRICING]
        except (OSError, ValueError, TypeError):
            self.payload["models"] = [dict(row) for row in DEFAULT_MODEL_PRICING]

    def _save(self) -> None:
        _atomic_json(self.path, self.payload)

    def public(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.payload, ensure_ascii=False))

    def update(self, *, billing_enabled: bool, usd_cny_rate: float, models: list[dict[str, Any]]) -> dict[str, Any]:
        rate = float(usd_cny_rate)
        if rate <= 0 or rate > 100:
            raise ValueError("美元兑人民币计费换算率必须大于 0 且不超过 100")
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in models:
            model_id = str(raw.get("model_id") or "").strip()
            if not model_id or len(model_id) > 120 or model_id in seen:
                continue
            values: dict[str, float] = {}
            for key in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million"):
                value = float(raw.get(key) or 0)
                if value < 0 or value > 100000:
                    raise ValueError(f"模型 {model_id} 的价格超出允许范围")
                values[key] = value
            seen.add(model_id)
            cleaned.append({
                "model_id": model_id,
                "name": str(raw.get("name") or model_id).strip()[:120],
                "enabled": bool(raw.get("enabled", True)),
                **values,
            })
        if not cleaned:
            raise ValueError("至少需要保留一个模型价格配置")
        with self.lock:
            self.payload.update({"billing_enabled": bool(billing_enabled), "usd_cny_rate": rate, "models": cleaned, "updated_at": _now()})
            self._save()
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
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.wallets = {str(k): int(v) for k, v in (payload.get("wallets") or {}).items()}
            self.charges = {str(k): dict(v) for k, v in (payload.get("charges") or {}).items() if isinstance(v, dict)}
            self.orders = {str(k): dict(v) for k, v in (payload.get("orders") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            self.wallets, self.charges, self.orders = {}, {}, {}

    def _save(self) -> None:
        _atomic_json(self.path, {"version": USER_COMMERCE_VERSION, "wallets": self.wallets, "charges": self.charges, "orders": self.orders})

    def balance_cents(self, user_id: str) -> int:
        with self.lock:
            return int(self.wallets.get(user_id, 0))

    def credit(self, user_id: str, amount_cents: int) -> int:
        amount = int(amount_cents)
        if amount <= 0:
            raise ValueError("充值金额必须大于 0")
        with self.lock:
            self.wallets[user_id] = int(self.wallets.get(user_id, 0)) + amount
            self._save()
            return self.wallets[user_id]

    def record_charge(self, user_id: str, request_row: dict[str, Any], pricing: PricingStore) -> dict[str, Any] | None:
        request_id = str(request_row.get("request_id") or "")
        if not request_id or str(request_row.get("status") or "") != "completed":
            return None
        with self.lock:
            existing = self.charges.get(request_id)
            if existing:
                return dict(existing)
            diagnostics = request_row.get("diagnostics") if isinstance(request_row.get("diagnostics"), dict) else {}
            model_id = str(diagnostics.get("actual_model") or request_row.get("requested_model") or "")
            price = pricing.model(model_id)
            if not price:
                return None
            usage = request_row.get("usage") if isinstance(request_row.get("usage"), dict) else {}
            input_tokens = max(0, int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0))
            cached_tokens = max(0, int(usage.get("cached_input_tokens") or 0))
            output_tokens = max(0, int(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
            uncached_tokens = max(0, input_tokens - cached_tokens)
            usd = (
                uncached_tokens * float(price.get("input_usd_per_million") or 0)
                + cached_tokens * float(price.get("cached_input_usd_per_million") or 0)
                + output_tokens * float(price.get("output_usd_per_million") or 0)
            ) / 1_000_000
            usd_micros = max(0, int(round(usd * 1_000_000)))
            fx = float(pricing.public().get("usd_cny_rate") or 0)
            cny_cents = max(0, int(round(usd * fx * 100)))
            billing_enabled = bool(pricing.public().get("billing_enabled"))
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
                "usd_cny_rate": fx,
                "price_snapshot": price,
                "created_at": _now(),
            }
            self.charges[request_id] = row
            self._save()
            return dict(row)

    def charges_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = [dict(row) for row in self.charges.values() if row.get("user_id") == user_id]
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def charge_for_request(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.charges.get(str(request_id or ""))
            return dict(row) if row else None

    def create_order(self, user_id: str, amount_cents: int, channel: str) -> dict[str, Any]:
        amount = int(amount_cents)
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
            self._save()
            return dict(row)

    def update_order_checkout(self, order_id: str, *, pay_url: str = "", qr_image_url: str = "", qr_payload: str = "") -> dict[str, Any]:
        with self.lock:
            row = self.orders.get(order_id)
            if not row:
                raise KeyError("充值订单不存在")
            row.update({"pay_url": pay_url, "qr_image_url": qr_image_url, "qr_payload": qr_payload, "updated_at": _now()})
            self._save()
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
            row["status"] = "paid"
            row["provider_trade_no"] = str(provider_trade_no or "")[:160]
            row["paid_at"] = _now()
            row["updated_at"] = _now()
            user_id = str(row.get("user_id") or "")
            self.wallets[user_id] = int(self.wallets.get(user_id, 0)) + int(row.get("amount_cents") or 0)
            self._save()
            return dict(row)

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
