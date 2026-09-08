from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from types import MethodType
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE as ADMIN_SESSION_COOKIE
from .timezone_utils import beijing_now_iso
from .user_commerce import USER_SESSION_COOKIE
from .user_console_v104_patch import _parse_json_maybe_wrapped, _safe_url, _verify_zpay, _zpay_sign


PATCH_REVISION = 106
USER_ASSET = "/assets/chat2api-user-payments-v106.js"
ADMIN_ASSET = "/assets/chat2api-admin-payments-v106.js"
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def _now() -> str:
    return beijing_now_iso()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _valid_tron_address(value: str) -> bool:
    return bool(re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", str(value or "").strip()))


class PaymentChannelsStore:
    """Provider-separated payment configuration.

    Aggregate providers expose their supported end-user rails explicitly. Direct
    providers have one provider-level enable switch. Secrets are encrypted at rest
    and are never returned by public/admin read APIs.
    """

    def __init__(self, data_dir: Path, secret: str) -> None:
        self.path = data_dir / "user_payment_channels_v106.json"
        key = base64.urlsafe_b64encode(hashlib.sha256(str(secret or "chat2api-payment-v106").encode()).digest())
        self.cipher = Fernet(key)
        self.payload: dict[str, Any] = {
            "version": 106,
            "public_origin": "",
            "aggregate": {
                "zpay": {
                    "enabled": False,
                    "pid": "",
                    "key_ciphertext": "",
                    "alipay_enabled": False,
                    "wechat_enabled": False,
                    "alipay_cid": "",
                    "wechat_cid": "",
                }
            },
            "direct": {
                "paypal": {
                    "enabled": False,
                    "sandbox": False,
                    "client_id": "",
                    "client_secret_ciphertext": "",
                },
                "usdt": {
                    "enabled": False,
                    "network": "TRON (TRC20)",
                    "address": "",
                    "qr_image_url": "",
                    "address_link": "",
                },
            },
            "updated_at": _now(),
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        self.payload["public_origin"] = str(raw.get("public_origin") or "")
        for group in ("aggregate", "direct"):
            incoming = raw.get(group)
            if not isinstance(incoming, dict):
                continue
            for provider, values in incoming.items():
                if provider in self.payload[group] and isinstance(values, dict):
                    self.payload[group][provider].update(values)
        self.payload["updated_at"] = raw.get("updated_at") or self.payload["updated_at"]

    def _save(self) -> None:
        _atomic_json(self.path, self.payload)

    def _decrypt(self, value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        try:
            return self.cipher.decrypt(text.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    def zpay_key(self) -> str:
        return self._decrypt(self.payload["aggregate"]["zpay"].get("key_ciphertext"))

    def paypal_secret(self) -> str:
        return self._decrypt(self.payload["direct"]["paypal"].get("client_secret_ciphertext"))

    def admin_public(self) -> dict[str, Any]:
        zpay = dict(self.payload["aggregate"]["zpay"])
        paypal = dict(self.payload["direct"]["paypal"])
        usdt = dict(self.payload["direct"]["usdt"])
        zpay.pop("key_ciphertext", None)
        paypal.pop("client_secret_ciphertext", None)
        zpay["key_configured"] = bool(self.zpay_key())
        zpay["configured"] = bool(zpay.get("pid") and self.zpay_key())
        paypal["secret_configured"] = bool(self.paypal_secret())
        paypal["configured"] = bool(paypal.get("client_id") and self.paypal_secret())
        usdt["configured"] = bool(_valid_tron_address(str(usdt.get("address") or "")))
        return {
            "version": 106,
            "public_origin": str(self.payload.get("public_origin") or ""),
            "aggregate": {"zpay": zpay},
            "direct": {"paypal": paypal, "usdt": usdt},
            "updated_at": self.payload.get("updated_at"),
        }

    def user_public(self) -> dict[str, Any]:
        admin = self.admin_public()
        zpay = admin["aggregate"]["zpay"]
        paypal = admin["direct"]["paypal"]
        usdt = admin["direct"]["usdt"]
        methods: list[dict[str, Any]] = []
        if zpay.get("enabled") and zpay.get("configured"):
            if zpay.get("alipay_enabled"):
                methods.append({"id": "alipay", "name": "支付宝", "provider": "zpay", "kind": "aggregate"})
            if zpay.get("wechat_enabled"):
                methods.append({"id": "wechat", "name": "微信支付", "provider": "zpay", "kind": "aggregate"})
        if paypal.get("enabled") and paypal.get("configured"):
            methods.append({"id": "paypal", "name": "PayPal", "provider": "paypal", "kind": "direct"})
        if usdt.get("enabled") and usdt.get("configured"):
            methods.append({
                "id": "usdt", "name": "USDT", "provider": "usdt", "kind": "direct",
                "network": "TRON (TRC20)", "address": usdt.get("address"),
                "qr_image_url": _safe_url(usdt.get("qr_image_url")),
                "address_link": _safe_url(usdt.get("address_link")),
            })
        return {"enabled": bool(methods), "methods": methods}

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        origin = str(data.get("public_origin") or "").strip().rstrip("/")[:2048]
        if origin and not origin.startswith("https://"):
            raise ValueError("公网 Origin 必须使用 https://")
        aggregate = data.get("aggregate") if isinstance(data.get("aggregate"), dict) else {}
        direct = data.get("direct") if isinstance(data.get("direct"), dict) else {}
        z_in = aggregate.get("zpay") if isinstance(aggregate.get("zpay"), dict) else {}
        p_in = direct.get("paypal") if isinstance(direct.get("paypal"), dict) else {}
        u_in = direct.get("usdt") if isinstance(direct.get("usdt"), dict) else {}

        zpay = self.payload["aggregate"]["zpay"]
        zpay.update({
            "enabled": bool(z_in.get("enabled")),
            "pid": str(z_in.get("pid") or "").strip()[:80],
            "alipay_enabled": bool(z_in.get("alipay_enabled")),
            "wechat_enabled": bool(z_in.get("wechat_enabled")),
            "alipay_cid": str(z_in.get("alipay_cid") or "").strip().replace(" ", "")[:240],
            "wechat_cid": str(z_in.get("wechat_cid") or "").strip().replace(" ", "")[:240],
        })
        for cid in (zpay["alipay_cid"], zpay["wechat_cid"]):
            if cid and not re.fullmatch(r"\d+(?:,\d+)*", cid):
                raise ValueError("ZPAY 渠道 ID 只能填写数字，多个 ID 使用英文逗号分隔")
        if z_in.get("key"):
            zpay["key_ciphertext"] = self.cipher.encrypt(str(z_in["key"]).encode()).decode("ascii")
        if z_in.get("clear_key"):
            zpay["key_ciphertext"] = ""

        paypal = self.payload["direct"]["paypal"]
        paypal.update({
            "enabled": bool(p_in.get("enabled")),
            "sandbox": bool(p_in.get("sandbox")),
            "client_id": str(p_in.get("client_id") or "").strip()[:240],
        })
        if p_in.get("client_secret"):
            paypal["client_secret_ciphertext"] = self.cipher.encrypt(str(p_in["client_secret"]).encode()).decode("ascii")
        if p_in.get("clear_secret"):
            paypal["client_secret_ciphertext"] = ""

        address = str(u_in.get("address") or "").strip()
        if address and not _valid_tron_address(address):
            raise ValueError("USDT 收款地址必须是有效的 TRON (TRC20) Base58 地址")
        qr = str(u_in.get("qr_image_url") or "").strip()[:2048]
        link = str(u_in.get("address_link") or "").strip()[:2048]
        if qr and not _safe_url(qr):
            raise ValueError("USDT 二维码图片地址必须是 https:// URL")
        if link and not _safe_url(link):
            raise ValueError("USDT 地址链接必须是 https:// URL")
        self.payload["direct"]["usdt"].update({
            "enabled": bool(u_in.get("enabled")),
            "network": "TRON (TRC20)",
            "address": address,
            "qr_image_url": qr,
            "address_link": link,
        })
        self.payload["public_origin"] = origin
        self.payload["updated_at"] = _now()
        self._save()
        return self.admin_public()


class PaymentChannelsBody(BaseModel):
    public_origin: str = Field(default="", max_length=2048)
    aggregate: dict[str, Any] = Field(default_factory=dict)
    direct: dict[str, Any] = Field(default_factory=dict)


class RechargeV106Body(BaseModel):
    amount_cents: int = Field(ge=1, le=10_000_000)
    channel: str = Field(pattern=r"^(alipay|wechat|paypal|usdt)$")


class UsdtTxBody(BaseModel):
    txid: str = Field(min_length=32, max_length=128)


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_payment_channels_v106_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "payment_channels_v106_installed", False):
        return app
    app.state.payment_channels_v106_installed = True

    settings = app.state.settings
    accounts = app.state.user_accounts
    billing = app.state.user_billing
    pricing = app.state.user_pricing
    admin_sessions = getattr(app.state, "admin_sessions", None)
    data_dir = Path(settings.data_dir)
    secret_path = data_dir / ".chat2api_data_secret"
    try:
        data_secret = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        data_secret = str(getattr(settings, "api_key", "") or "chat2api")
    channels = PaymentChannelsStore(data_dir, data_secret)
    app.state.user_payment_channels_v106 = channels

    original_create_order = billing.create_order

    def create_order_extended(self: Any, user_id: str, amount_cents: int, channel: str) -> dict[str, Any]:
        if channel in {"alipay", "wechat"}:
            return original_create_order(user_id, amount_cents, channel)
        amount = int(amount_cents)
        if amount < 1 or amount > 10_000_000 or channel not in {"paypal", "usdt"}:
            raise ValueError("不支持的充值订单")
        order_id = "pay_" + secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        row = {
            "order_id": order_id,
            "merchant_trade_no": (str(int(time.time() * 1000)) + f"{secrets.randbelow(10**12):012d}")[:32],
            "user_id": user_id,
            "amount_cents": amount,
            "channel": channel,
            "status": "pending",
            "provider_trade_no": "",
            "pay_url": "",
            "qr_image_url": "",
            "qr_payload": "",
            "provider_order_id": "",
            "provider_amount": "",
            "provider_currency": "",
            "txid": "",
            "created_at": _now(), "paid_at": None, "updated_at": _now(),
        }
        with self.lock:
            self.orders[order_id] = row
            self._save()
        return dict(row)

    billing.create_order = MethodType(create_order_extended, billing)

    def current_user(request: Request) -> dict[str, Any]:
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="请先登录用户控制台")
        return user

    def admin_ok(request: Request) -> bool:
        if admin_sessions and admin_sessions.authenticate(request.cookies.get(ADMIN_SESSION_COOKIE)):
            return True
        supplied = str(request.headers.get("x-api-key") or "").strip()
        auth = str(request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        master = str(getattr(settings, "api_key", "") or "")
        return bool(supplied and master and secrets.compare_digest(supplied, master))

    def origin_for(request: Request) -> str:
        return str(channels.admin_public().get("public_origin") or "").rstrip("/") or str(request.base_url).rstrip("/")

    def update_order(order_id: str, **values: Any) -> dict[str, Any]:
        with billing.lock:
            row = billing.orders.get(order_id)
            if not row:
                raise KeyError("充值订单不存在")
            row.update(values)
            row["updated_at"] = _now()
            billing._save()
            return dict(row)

    async def paypal_token() -> tuple[str, str]:
        cfg = channels.admin_public()["direct"]["paypal"]
        secret = channels.paypal_secret()
        if not cfg.get("enabled") or not cfg.get("configured") or not secret:
            raise HTTPException(status_code=409, detail="PayPal 尚未配置完成")
        base = "https://api-m.sandbox.paypal.com" if cfg.get("sandbox") else "https://api-m.paypal.com"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{base}/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(str(cfg.get("client_id") or ""), secret))
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="PayPal API 网络连接失败") from error
        payload = _parse_json_maybe_wrapped(response.text) or {}
        token = str(payload.get("access_token") or "")
        if response.status_code >= 400 or not token:
            raise HTTPException(status_code=409, detail="PayPal Client ID / Secret 校验失败")
        return base, token

    async def zpay_checkout(order: dict[str, Any], request: Request) -> dict[str, Any]:
        cfg = channels.admin_public()["aggregate"]["zpay"]
        key = channels.zpay_key()
        channel = str(order.get("channel") or "")
        if not cfg.get("enabled") or not cfg.get("configured") or not key:
            raise HTTPException(status_code=409, detail="ZPAY 聚合支付尚未配置完成")
        if channel == "alipay" and not cfg.get("alipay_enabled"):
            raise HTTPException(status_code=409, detail="支付宝当前未启用")
        if channel == "wechat" and not cfg.get("wechat_enabled"):
            raise HTTPException(status_code=409, detail="微信支付当前未启用")
        params: dict[str, Any] = {
            "pid": str(cfg.get("pid") or ""),
            "type": "alipay" if channel == "alipay" else "wxpay",
            "out_trade_no": str(order.get("merchant_trade_no") or ""),
            "notify_url": f"{origin_for(request)}/api/user/payments/v106/zpay/notify",
            "return_url": f"{origin_for(request)}/console#billing",
            "name": "chat2api 账户充值",
            "money": f"{int(order.get('amount_cents') or 0) / 100:.2f}",
            "param": str(order.get("order_id") or ""),
        }
        cid = str(cfg.get("alipay_cid") if channel == "alipay" else cfg.get("wechat_cid") or "")
        if cid:
            params["cid"] = cid
        params["sign"] = _zpay_sign(params, key)
        params["sign_type"] = "MD5"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.post("https://zpayz.cn/mapi.php", data=params, headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="ZPAY 网络连接失败") from error
        payload = _parse_json_maybe_wrapped(response.text) or {}
        if response.status_code >= 400 or int(payload.get("code") or 0) != 1:
            raise HTTPException(status_code=502, detail=str(payload.get("msg") or "ZPAY 返回失败")[:300])
        pay_url = _safe_url(payload.get("payurl")) or _safe_url(payload.get("payurl2"))
        qr_image_url = _safe_url(payload.get("img"))
        qr_payload = str(payload.get("qrcode") or "")[:2048]
        updated = billing.update_order_checkout(str(order["order_id"]), pay_url=pay_url, qr_image_url=qr_image_url, qr_payload=qr_payload)
        return {"order": updated, "checkout": {"pay_url": pay_url, "qr_image_url": qr_image_url, "qr_payload": qr_payload}}

    async def paypal_checkout(order: dict[str, Any], request: Request) -> dict[str, Any]:
        base, token = await paypal_token()
        fx = max(0.0001, float(pricing.public().get("usd_cny_rate") or 7.2))
        usd_value = max(0.01, round((int(order.get("amount_cents") or 0) / 100) / fx, 2))
        order_id = str(order["order_id"])
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": order_id, "description": "chat2api account recharge", "amount": {"currency_code": "USD", "value": f"{usd_value:.2f}"}}],
            "application_context": {
                "return_url": f"{origin_for(request)}/api/user/payments/v106/paypal/return?local_order_id={order_id}",
                "cancel_url": f"{origin_for(request)}/console#billing",
                "user_action": "PAY_NOW",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{base}/v2/checkout/orders", json=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "PayPal-Request-Id": order_id})
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="PayPal 创建订单失败") from error
        data = _parse_json_maybe_wrapped(response.text) or {}
        provider_id = str(data.get("id") or "")
        approval = next((_safe_url(item.get("href")) for item in data.get("links") or [] if isinstance(item, dict) and item.get("rel") == "approve"), "")
        if response.status_code >= 400 or not provider_id or not approval:
            raise HTTPException(status_code=502, detail="PayPal 未返回可用的付款链接")
        updated = update_order(order_id, provider_order_id=provider_id, provider_amount=f"{usd_value:.2f}", provider_currency="USD", pay_url=approval)
        return {"order": updated, "checkout": {"pay_url": approval, "provider_amount": f"{usd_value:.2f}", "provider_currency": "USD"}}

    def usdt_checkout(order: dict[str, Any]) -> dict[str, Any]:
        cfg = channels.admin_public()["direct"]["usdt"]
        if not cfg.get("enabled") or not cfg.get("configured"):
            raise HTTPException(status_code=409, detail="USDT TRC20 尚未配置完成")
        fx = max(0.0001, float(pricing.public().get("usd_cny_rate") or 7.2))
        usdt_value = round((int(order.get("amount_cents") or 0) / 100) / fx, 6)
        updated = update_order(str(order["order_id"]), provider_amount=f"{usdt_value:.6f}", provider_currency="USDT", qr_image_url=str(cfg.get("qr_image_url") or ""), qr_payload=str(cfg.get("address") or ""))
        return {"order": updated, "checkout": {"network": "TRON (TRC20)", "address": cfg.get("address"), "address_link": cfg.get("address_link"), "qr_image_url": cfg.get("qr_image_url"), "provider_amount": f"{usdt_value:.6f}", "provider_currency": "USDT", "manual_confirmation": True}}

    @app.get(USER_ASSET, include_in_schema=False)
    async def payment_user_asset() -> Response:
        return Response(Path(__file__).with_name("user_payments_v106.js").read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get(ADMIN_ASSET, include_in_schema=False)
    async def payment_admin_asset() -> Response:
        return Response(Path(__file__).with_name("admin_payments_v106.js").read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/api/user/payment-channels-v106")
    async def user_payment_channels(request: Request) -> dict[str, Any]:
        current_user(request)
        return channels.user_public()

    @app.post("/api/user/billing/recharge-v106")
    async def recharge_v106(body: RechargeV106Body, request: Request) -> dict[str, Any]:
        user = current_user(request)
        allowed = {row["id"] for row in channels.user_public().get("methods") or []}
        if body.channel not in allowed:
            raise HTTPException(status_code=409, detail="该支付方式当前未启用")
        order = billing.create_order(str(user["user_id"]), body.amount_cents, body.channel)
        if body.channel in {"alipay", "wechat"}:
            return await zpay_checkout(order, request)
        if body.channel == "paypal":
            return await paypal_checkout(order, request)
        return usdt_checkout(order)

    @app.post("/api/user/payments/v106/usdt/{order_id}/txid")
    async def submit_usdt_txid(order_id: str, body: UsdtTxBody, request: Request) -> dict[str, Any]:
        user = current_user(request)
        txid = body.txid.strip()
        if not re.fullmatch(r"[0-9A-Fa-f]{64}", txid):
            raise HTTPException(status_code=400, detail="请输入 64 位 TRON 交易哈希")
        with billing.lock:
            order = billing.orders.get(order_id)
            if not order or str(order.get("user_id") or "") != str(user["user_id"]) or order.get("channel") != "usdt":
                raise HTTPException(status_code=404, detail="USDT 充值订单不存在")
            if order.get("status") == "paid":
                return {"ok": True, "order": dict(order)}
            order["txid"] = txid.lower()
            order["status"] = "reviewing"
            order["updated_at"] = _now()
            billing._save()
            return {"ok": True, "order": dict(order)}

    @app.api_route("/api/user/payments/v106/zpay/notify", methods=["GET", "POST"], include_in_schema=False)
    async def zpay_notify_v106(request: Request) -> Response:
        data: dict[str, Any] = dict(request.query_params)
        if request.method == "POST":
            form = await request.form()
            data.update(dict(form))
        cfg = channels.admin_public()["aggregate"]["zpay"]
        key = channels.zpay_key()
        if not key or not cfg.get("configured") or not _verify_zpay(data, key):
            return Response("fail", status_code=400, media_type="text/plain")
        if str(data.get("pid") or "") != str(cfg.get("pid") or "") or str(data.get("trade_status") or "") != "TRADE_SUCCESS":
            return Response("fail", status_code=400, media_type="text/plain")
        order = billing.order_by_trade_no(str(data.get("out_trade_no") or ""))
        if not order or order.get("channel") not in {"alipay", "wechat"}:
            return Response("fail", status_code=404, media_type="text/plain")
        try:
            amount_ok = abs(float(str(data.get("money") or "-1")) - int(order.get("amount_cents") or 0) / 100) < 0.000001
        except ValueError:
            amount_ok = False
        expected_type = "alipay" if order.get("channel") == "alipay" else "wxpay"
        if not amount_ok or str(data.get("type") or "") != expected_type:
            return Response("fail", status_code=400, media_type="text/plain")
        billing.settle_order(str(order["order_id"]), str(data.get("trade_no") or ""))
        return Response("success", media_type="text/plain")

    @app.get("/api/user/payments/v106/paypal/return", include_in_schema=False)
    async def paypal_return(request: Request, local_order_id: str = Query(min_length=1), token: str = Query(min_length=1)) -> RedirectResponse:
        with billing.lock:
            raw = billing.orders.get(local_order_id)
            order = dict(raw) if raw else None
        if not order or order.get("channel") != "paypal" or str(order.get("provider_order_id") or "") != token:
            return RedirectResponse("/console#billing", status_code=303)
        if order.get("status") == "paid":
            return RedirectResponse("/console#billing", status_code=303)
        base, access_token = await paypal_token()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{base}/v2/checkout/orders/{token}/capture", headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "PayPal-Request-Id": f"capture-{local_order_id}"}, json={})
        except httpx.HTTPError:
            return RedirectResponse("/console#billing&payment=failed", status_code=303)
        data = _parse_json_maybe_wrapped(response.text) or {}
        captures = (((data.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or []) if isinstance(data, dict) else []
        capture = captures[0] if captures and isinstance(captures[0], dict) else {}
        amount = capture.get("amount") if isinstance(capture.get("amount"), dict) else {}
        valid_amount = str(amount.get("currency_code") or "") == str(order.get("provider_currency") or "") and str(amount.get("value") or "") == str(order.get("provider_amount") or "")
        if response.status_code < 400 and str(data.get("status") or "") == "COMPLETED" and valid_amount:
            billing.settle_order(local_order_id, str(capture.get("id") or token))
            return RedirectResponse("/console#billing&payment=success", status_code=303)
        return RedirectResponse("/console#billing&payment=failed", status_code=303)

    @app.get("/api/admin/payment-channels-v106")
    async def admin_get_channels(request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        return channels.admin_public()

    @app.put("/api/admin/payment-channels-v106")
    async def admin_put_channels(body: PaymentChannelsBody, request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        try:
            return channels.update(body.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/admin/payment-channels-v106/test")
    async def admin_test_channel(request: Request, provider: str = Query(pattern=r"^(zpay|paypal)$")) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        if provider == "paypal":
            await paypal_token()
            return {"ok": True, "provider": "paypal", "message": "PayPal OAuth 凭据校验成功"}
        cfg = channels.admin_public()["aggregate"]["zpay"]
        key = channels.zpay_key()
        if not cfg.get("configured") or not key:
            raise HTTPException(status_code=409, detail="请先保存 ZPAY PID 和商户密钥")
        trade_no = ("99" + str(int(time.time() * 1000)) + f"{secrets.randbelow(10**6):06d}").ljust(32, "0")[:32]
        params = {"act": "order", "pid": str(cfg.get("pid") or ""), "key": key, "out_trade_no": trade_no}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get("https://zpayz.cn/api.php?" + urlencode(params), headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="ZPAY API 网络连接失败") from error
        payload = _parse_json_maybe_wrapped(response.text) or {}
        if response.status_code >= 400 or not payload:
            raise HTTPException(status_code=502, detail="ZPAY API 未返回有效 JSON")
        return {"ok": True, "provider": "zpay", "message": str(payload.get("msg") or "ZPAY API 可达")[:300], "upstream_code": payload.get("code")}

    @app.get("/api/admin/payment-channels-v106/usdt-orders")
    async def admin_usdt_orders(request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        with billing.lock:
            rows = [dict(row) for row in billing.orders.values() if row.get("channel") == "usdt" and row.get("status") in {"pending", "reviewing"}]
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return {"data": rows[:100]}

    @app.post("/api/admin/payment-channels-v106/usdt-orders/{order_id}/{action}")
    async def admin_review_usdt(order_id: str, action: str, request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        if action not in {"confirm", "reject"}:
            raise HTTPException(status_code=400, detail="不支持的审核动作")
        with billing.lock:
            row = billing.orders.get(order_id)
            if not row or row.get("channel") != "usdt":
                raise HTTPException(status_code=404, detail="USDT 订单不存在")
            txid = str(row.get("txid") or "")
            if action == "confirm" and not re.fullmatch(r"[0-9a-f]{64}", txid):
                raise HTTPException(status_code=409, detail="用户尚未提交有效交易哈希")
            if action == "reject":
                row["status"] = "rejected"
                row["updated_at"] = _now()
                billing._save()
                return {"ok": True, "order": dict(row)}
        return {"ok": True, "order": billing.settle_order(order_id, txid)}

    @app.middleware("http")
    async def payment_assets_v106(request: Request, call_next):
        response = await call_next(request)
        if request.url.path not in {"/console", "/admin"} or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        asset = USER_ASSET if request.url.path == "/console" else ADMIN_ASSET
        marker = f'<script src="{asset}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
