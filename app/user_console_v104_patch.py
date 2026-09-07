from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE as ADMIN_SESSION_COOKIE
from .api_keys import load_or_create_data_secret, token_hash
from .user_commerce import (
    BillingStore,
    PaymentConfigStore,
    PricingStore,
    USER_SESSION_COOKIE,
    UserAccountStore,
)


PATCH_REVISION = 104
USER_JS_ASSET = "/assets/chat2api-user-console-v104.js"
ADMIN_JS_ASSET = "/assets/chat2api-admin-commerce-v104.js"


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=80)


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ProfileBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class UserKeyBody(BaseModel):
    name: str = Field(default="API Key", max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class PricingBody(BaseModel):
    billing_enabled: bool = False
    usd_cny_rate: float = Field(gt=0, le=100)
    models: list[dict[str, Any]]


class PaymentBody(BaseModel):
    enabled: bool = False
    pid: str = Field(default="", max_length=80)
    key: str | None = Field(default=None, max_length=512)
    clear_key: bool = False
    alipay_enabled: bool = True
    wechat_enabled: bool = True
    alipay_cid: str = Field(default="", max_length=240)
    wechat_cid: str = Field(default="", max_length=240)
    public_origin: str = Field(default="", max_length=2048)


class RechargeBody(BaseModel):
    amount_cents: int = Field(ge=1, le=10_000_000)
    channel: str = Field(pattern=r"^(alipay|wechat)$")


class PlaygroundBody(BaseModel):
    key_id: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20000)


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


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = httpx.URL(text)
        if parsed.scheme != "https" or not parsed.host or parsed.userinfo:
            return ""
        return str(parsed)
    except Exception:
        return ""


def _zpay_sign(data: dict[str, Any], key: str) -> str:
    pairs = sorted(
        (str(name), str(value))
        for name, value in data.items()
        if name not in {"sign", "sign_type"} and value is not None and str(value) != ""
    )
    canonical = "&".join(f"{name}={value}" for name, value in pairs)
    return hashlib.md5((canonical + str(key or "")).encode("utf-8")).hexdigest()  # noqa: S324 - provider contract


def _verify_zpay(data: dict[str, Any], key: str) -> bool:
    supplied = str(data.get("sign") or "").lower()
    if not supplied or str(data.get("sign_type") or "").upper() != "MD5":
        return False
    expected = _zpay_sign(data, key)
    return len(supplied) == len(expected) and secrets.compare_digest(supplied, expected)


def _parse_json_maybe_wrapped(text: str) -> dict[str, Any] | None:
    try:
        value: Any = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def install_user_console_v104_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "user_console_v104_installed", False):
        return app
    app.state.user_console_v104_installed = True

    settings = app.state.settings
    data_dir = Path(settings.data_dir)
    accounts = UserAccountStore(data_dir)
    pricing = PricingStore(data_dir)
    billing = BillingStore(data_dir)
    data_secret = load_or_create_data_secret(data_dir)
    payments = PaymentConfigStore(data_dir, data_secret)
    api_keys = app.state.api_keys
    telemetry = app.state.telemetry
    admin_sessions = getattr(app.state, "admin_sessions", None)

    app.state.user_accounts = accounts
    app.state.user_pricing = pricing
    app.state.user_billing = billing
    app.state.user_payments = payments

    def admin_ok(request: Request) -> bool:
        if admin_sessions and admin_sessions.authenticate(request.cookies.get(ADMIN_SESSION_COOKIE)):
            return True
        supplied = str(request.headers.get("x-api-key") or "").strip()
        authorization = str(request.headers.get("authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        master = str(getattr(settings, "api_key", "") or "")
        return bool(supplied and master and secrets.compare_digest(supplied, master))

    def current_user(request: Request) -> dict[str, Any]:
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="请先登录用户控制台")
        return user

    def public_key(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "key_id", "name", "prefix", "created_at", "expires_at", "last_used_at",
                "enabled", "configured_enabled", "expired", "revoked_at", "scopes", "secret_recoverable",
            )
        }

    def sync_user_charges(user_id: str) -> None:
        owned = accounts.key_ids(user_id)
        if not owned:
            return
        for row in telemetry.recent(500):
            if str(row.get("api_key_id") or "") in owned and str(row.get("status") or "") == "completed":
                billing.record_charge(user_id, row, pricing)

    def public_request(row: dict[str, Any]) -> dict[str, Any]:
        diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
        request_id = str(row.get("request_id") or "")
        charge = billing.charge_for_request(request_id)
        status = str(row.get("status") or "")
        return {
            "request_id": request_id,
            "recorded_at": row.get("recorded_at"),
            "finished_at": row.get("finished_at"),
            "status": status,
            "type": str(row.get("type") or row.get("request_type") or "chat"),
            "model": str(diagnostics.get("actual_model") or row.get("requested_model") or ""),
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "estimated": True,
            },
            "latency": {
                "first_token_ms": timings.get("first_token_ms"),
                "total_ms": timings.get("total_ms"),
            },
            "cost": {
                "usd_micros": int((charge or {}).get("cost_usd_micros") or 0),
                "cny_cents": int((charge or {}).get("cost_cny_cents") or 0),
                "debited": bool((charge or {}).get("debited")),
                "estimated": True,
            },
            "error": "" if status == "completed" else ("请求仍在处理中" if status == "running" else "请求未成功完成"),
        }

    def user_rows(user_id: str) -> list[dict[str, Any]]:
        owned = accounts.key_ids(user_id)
        rows = [row for row in telemetry.recent(500) if str(row.get("api_key_id") or "") in owned]
        return rows

    original_upsert = telemetry.upsert

    async def metered_upsert(item: dict[str, Any]) -> dict[str, Any]:
        row = await original_upsert(item)
        if str(row.get("status") or "") == "completed":
            owner = accounts.owner_for_key(str(row.get("api_key_id") or ""))
            if owner:
                billing.record_charge(owner, row, pricing)
        return row

    telemetry.upsert = metered_upsert

    @app.get("/console", include_in_schema=False)
    async def user_console() -> HTMLResponse:
        template = Path(__file__).with_name("user_console_v104.html").read_text(encoding="utf-8")
        return HTMLResponse(template, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

    @app.get(USER_JS_ASSET, include_in_schema=False)
    async def user_console_js() -> Response:
        return Response(
            Path(__file__).with_name("user_console_v104.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get(ADMIN_JS_ASSET, include_in_schema=False)
    async def admin_commerce_js() -> Response:
        return Response(
            Path(__file__).with_name("admin_user_commerce_v104.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.post("/api/user/register")
    async def user_register(body: RegisterBody, request: Request) -> JSONResponse:
        try:
            user = accounts.register(body.email, body.password, body.display_name)
            token = accounts.create_session(str(user["user_id"]))
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        response = JSONResponse({"ok": True, "user": user})
        response.set_cookie(USER_SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=accounts.session_ttl_seconds, path="/")
        return response

    @app.post("/api/user/login")
    async def user_login(body: LoginBody, request: Request) -> JSONResponse:
        user = accounts.verify(body.email, body.password)
        if not user:
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        token = accounts.create_session(str(user["user_id"]))
        response = JSONResponse({"ok": True, "user": user})
        response.set_cookie(USER_SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=accounts.session_ttl_seconds, path="/")
        return response

    @app.post("/api/user/logout")
    async def user_logout(request: Request) -> JSONResponse:
        accounts.revoke_session(request.cookies.get(USER_SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(USER_SESSION_COOKIE, path="/")
        return response

    @app.get("/api/user/me")
    async def user_me(request: Request) -> dict[str, Any]:
        user = current_user(request)
        return {"ok": True, "user": user, "balance_cents": billing.balance_cents(str(user["user_id"]))}

    @app.patch("/api/user/profile")
    async def user_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
        user = current_user(request)
        try:
            updated = accounts.update_profile(str(user["user_id"]), display_name=body.display_name)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "user": updated}

    @app.get("/api/user/keys")
    async def user_keys(request: Request) -> dict[str, Any]:
        user = current_user(request)
        rows = []
        for key_id in accounts.key_ids(str(user["user_id"])):
            item = api_keys.get_public(key_id)
            if item:
                rows.append(public_key(item))
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return {"data": rows}

    @app.post("/api/user/keys")
    async def user_create_key(body: UserKeyBody, request: Request) -> dict[str, Any]:
        user = current_user(request)
        expires_at = None
        if body.expires_in_days:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
        item, raw = await api_keys.create(body.name, expires_at)
        accounts.attach_key(str(user["user_id"]), str(item["key_id"]))
        return {"key": public_key(item), "token": raw}

    @app.get("/api/user/keys/{key_id}/secret")
    async def user_key_secret(key_id: str, request: Request) -> dict[str, str]:
        user = current_user(request)
        if not accounts.owns_key(str(user["user_id"]), key_id):
            raise HTTPException(status_code=404, detail="API Key 不存在")
        try:
            return {"key_id": key_id, "token": api_keys.reveal(key_id)}
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/api/user/keys/{key_id}")
    async def user_revoke_key(key_id: str, request: Request) -> dict[str, Any]:
        user = current_user(request)
        if not accounts.owns_key(str(user["user_id"]), key_id):
            raise HTTPException(status_code=404, detail="API Key 不存在")
        try:
            item = await api_keys.revoke(key_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="API Key 不存在") from error
        return {"key": public_key(item)}

    @app.get("/api/user/requests")
    async def user_requests(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status_filter: str | None = Query(default=None, alias="status"),
        model: str | None = Query(default=None),
    ) -> dict[str, Any]:
        user = current_user(request)
        user_id = str(user["user_id"])
        sync_user_charges(user_id)
        rows = user_rows(user_id)
        if status_filter:
            rows = [row for row in rows if str(row.get("status") or "") == status_filter]
        if model:
            wanted = model.lower()
            rows = [row for row in rows if wanted in str((row.get("diagnostics") or {}).get("actual_model") or row.get("requested_model") or "").lower()]
        total = len(rows)
        page = rows[offset : offset + limit]
        return {"data": [public_request(row) for row in page], "total": total, "limit": limit, "offset": offset}

    @app.get("/api/user/models")
    async def user_models(request: Request) -> dict[str, Any]:
        current_user(request)
        payload = pricing.public()
        models = [dict(row) for row in payload.get("models") or [] if row.get("enabled")]
        return {
            "data": models,
            "billing_enabled": bool(payload.get("billing_enabled")),
            "usd_cny_rate": float(payload.get("usd_cny_rate") or 0),
            "price_reference": str(payload.get("price_reference") or ""),
            "pricing_unit": "USD / 1M tokens",
        }

    @app.get("/api/user/billing")
    async def user_billing(request: Request) -> dict[str, Any]:
        user = current_user(request)
        user_id = str(user["user_id"])
        sync_user_charges(user_id)
        charge_rows = billing.charges_for_user(user_id)
        order_rows = billing.orders_for_user(user_id)
        payload = pricing.public()
        return {
            "balance_cents": billing.balance_cents(user_id),
            "billing_enabled": bool(payload.get("billing_enabled")),
            "usd_cny_rate": float(payload.get("usd_cny_rate") or 0),
            "estimated_spend_cents": sum(int(row.get("cost_cny_cents") or 0) for row in charge_rows),
            "debited_spend_cents": sum(int(row.get("cost_cny_cents") or 0) for row in charge_rows if row.get("debited")),
            "charges": charge_rows[:100],
            "orders": order_rows[:50],
            "payment": {
                "enabled": bool(payments.public().get("enabled") and payments.public().get("configured")),
                "alipay": bool(payments.public().get("alipay_enabled")),
                "wechat": bool(payments.public().get("wechat_enabled")),
            },
            "usage_is_estimated": True,
        }

    @app.get("/api/user/dashboard")
    async def user_dashboard(request: Request) -> dict[str, Any]:
        user = current_user(request)
        user_id = str(user["user_id"])
        sync_user_charges(user_id)
        rows = user_rows(user_id)
        by_model: Counter[str] = Counter()
        by_status: Counter[str] = Counter()
        by_day: defaultdict[str, int] = defaultdict(int)
        total_tokens = 0
        latencies: list[float] = []
        for row in rows:
            diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
            usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
            timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
            model_id = str(diagnostics.get("actual_model") or row.get("requested_model") or "unknown")
            by_model[model_id] += 1
            by_status[str(row.get("status") or "unknown")] += 1
            day = str(row.get("recorded_at") or "")[:10] or "unknown"
            by_day[day] += 1
            total_tokens += int(usage.get("total_tokens") or 0)
            if timings.get("total_ms") is not None:
                try:
                    latencies.append(float(timings["total_ms"]))
                except (TypeError, ValueError):
                    pass
        charges = billing.charges_for_user(user_id)
        completed = by_status.get("completed", 0)
        terminal = completed + sum(by_status.get(value, 0) for value in ("error", "cancelled", "stalled"))
        return {
            "requests": len(rows),
            "completed": completed,
            "success_rate": round(completed / terminal * 100, 2) if terminal else 100.0,
            "estimated_tokens": total_tokens,
            "estimated_spend_cents": sum(int(row.get("cost_cny_cents") or 0) for row in charges),
            "balance_cents": billing.balance_cents(user_id),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "by_model": [{"model": key, "requests": value} for key, value in by_model.most_common()],
            "by_status": [{"status": key, "requests": value} for key, value in by_status.items()],
            "by_day": [{"day": key, "requests": by_day[key]} for key in sorted(by_day)],
            "usage_is_estimated": True,
        }

    @app.post("/api/user/playground")
    async def user_playground(body: PlaygroundBody, request: Request) -> dict[str, Any]:
        user = current_user(request)
        if not accounts.owns_key(str(user["user_id"]), body.key_id):
            raise HTTPException(status_code=404, detail="请选择自己的 API Key")
        if not pricing.model(body.model):
            raise HTTPException(status_code=400, detail="该模型不在当前模型广场中")
        try:
            secret = api_keys.reveal(body.key_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail="API Key 当前不可用于测试") from error
        base = str(request.base_url).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{base}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {secret}"},
                    json={"model": body.model, "messages": [{"role": "user", "content": body.prompt}], "stream": False},
                )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="测试请求暂时无法完成") from error
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": "API 返回了无法解析的响应"}}
        if response.status_code >= 400:
            public_message = str((payload.get("error") or {}).get("message") or "测试请求失败") if isinstance(payload, dict) else "测试请求失败"
            raise HTTPException(status_code=response.status_code, detail=public_message[:500])
        return {"ok": True, "response": payload}

    async def create_zpay_checkout(order: dict[str, Any], request: Request) -> dict[str, Any]:
        config = payments.public()
        key = payments.secret_key()
        if not config.get("enabled") or not config.get("configured") or not key:
            raise HTTPException(status_code=409, detail="在线支付当前未配置完成")
        channel = str(order.get("channel") or "")
        if channel == "alipay" and not config.get("alipay_enabled"):
            raise HTTPException(status_code=409, detail="支付宝支付当前未启用")
        if channel == "wechat" and not config.get("wechat_enabled"):
            raise HTTPException(status_code=409, detail="微信支付当前未启用")
        origin = str(config.get("public_origin") or "").rstrip("/") or str(request.base_url).rstrip("/")
        provider_channel = "alipay" if channel == "alipay" else "wxpay"
        params: dict[str, Any] = {
            "pid": str(config.get("pid") or ""),
            "type": provider_channel,
            "out_trade_no": str(order.get("merchant_trade_no") or ""),
            "notify_url": f"{origin}/api/user/payments/zpay/notify",
            "return_url": f"{origin}/console#billing",
            "name": "chat2api 账户充值",
            "money": f"{int(order.get('amount_cents') or 0) / 100:.2f}",
            "param": str(order.get("order_id") or ""),
        }
        cid = str(config.get("alipay_cid") if channel == "alipay" else config.get("wechat_cid") or "")
        if cid:
            params["cid"] = cid
        params["sign"] = _zpay_sign(params, key)
        params["sign_type"] = "MD5"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.post("https://zpayz.cn/mapi.php", data=params, headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="支付接口网络连接失败") from error
        payload = _parse_json_maybe_wrapped(response.text)
        if response.status_code >= 400 or not payload or int(payload.get("code") or 0) != 1:
            detail = str((payload or {}).get("msg") or "支付接口返回失败")[:500]
            raise HTTPException(status_code=502, detail=detail)
        pay_url = _safe_url(payload.get("payurl")) or _safe_url(payload.get("payurl2"))
        qr_image_url = _safe_url(payload.get("img"))
        qr_payload = str(payload.get("qrcode") or "")[:2048]
        updated = billing.update_order_checkout(str(order["order_id"]), pay_url=pay_url, qr_image_url=qr_image_url, qr_payload=qr_payload)
        return {"order": updated, "checkout": {"pay_url": pay_url, "qr_image_url": qr_image_url, "qr_payload": qr_payload}}

    @app.post("/api/user/billing/recharge")
    async def user_recharge(body: RechargeBody, request: Request) -> dict[str, Any]:
        user = current_user(request)
        order = billing.create_order(str(user["user_id"]), body.amount_cents, body.channel)
        return await create_zpay_checkout(order, request)

    @app.api_route("/api/user/payments/zpay/notify", methods=["GET", "POST"], include_in_schema=False)
    async def zpay_notify(request: Request) -> Response:
        data: dict[str, Any] = dict(request.query_params)
        if request.method == "POST":
            form = await request.form()
            data.update(dict(form))
        key = payments.secret_key()
        config = payments.public()
        if not key or not config.get("configured"):
            return Response("fail", status_code=503, media_type="text/plain")
        if not _verify_zpay(data, key):
            return Response("fail", status_code=400, media_type="text/plain")
        if str(data.get("pid") or "") != str(config.get("pid") or "") or str(data.get("trade_status") or "") != "TRADE_SUCCESS":
            return Response("fail", status_code=400, media_type="text/plain")
        order = billing.order_by_trade_no(str(data.get("out_trade_no") or ""))
        if not order:
            return Response("fail", status_code=404, media_type="text/plain")
        expected_money = f"{int(order.get('amount_cents') or 0) / 100:.2f}"
        try:
            amount_ok = abs(float(str(data.get("money") or "-1")) - float(expected_money)) < 0.000001
        except ValueError:
            amount_ok = False
        expected_type = "alipay" if order.get("channel") == "alipay" else "wxpay"
        if not amount_ok or str(data.get("type") or "") != expected_type:
            return Response("fail", status_code=400, media_type="text/plain")
        billing.settle_order(str(order["order_id"]), str(data.get("trade_no") or ""))
        return Response("success", media_type="text/plain")

    @app.get("/api/user/payments/zpay/return", include_in_schema=False)
    async def zpay_return() -> RedirectResponse:
        return RedirectResponse("/console#billing", status_code=303)

    @app.get("/api/admin/user-pricing")
    async def admin_user_pricing(request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        return pricing.public()

    @app.put("/api/admin/user-pricing")
    async def admin_update_user_pricing(body: PricingBody, request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        try:
            return pricing.update(billing_enabled=body.billing_enabled, usd_cny_rate=body.usd_cny_rate, models=body.models)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/admin/user-payments")
    async def admin_user_payments(request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        return payments.public()

    @app.put("/api/admin/user-payments")
    async def admin_update_user_payments(body: PaymentBody, request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        try:
            return payments.update(body.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/admin/user-payments/test")
    async def admin_test_user_payments(request: Request) -> dict[str, Any]:
        if not admin_ok(request):
            raise HTTPException(status_code=401, detail="Administrator login required")
        config = payments.public()
        key = payments.secret_key()
        if not config.get("configured") or not key:
            raise HTTPException(status_code=409, detail="请先保存 ZPAY 商户 ID 和商户密钥")
        probe_trade_no = ("99" + str(int(datetime.now(timezone.utc).timestamp() * 1000)) + f"{secrets.randbelow(10**6):06d}").ljust(32, "0")[:32]
        params = {"act": "order", "pid": str(config.get("pid") or ""), "key": key, "out_trade_no": probe_trade_no}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get("https://zpayz.cn/api.php?" + urlencode(params), headers={"Accept": "application/json"})
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail="ZPAY API 网络连接失败") from error
        payload = _parse_json_maybe_wrapped(response.text)
        if response.status_code >= 400 or not payload:
            raise HTTPException(status_code=502, detail="ZPAY API 未返回有效 JSON")
        message = str(payload.get("msg") or payload.get("message") or "")[:500]
        lowered = message.lower()
        auth_failure = any(token in lowered for token in ("invalid pid", "invalid key", "unauthorized", "authentication failed")) or any(token in message for token in ("商户不存在", "商户ID错误", "商户号错误", "密钥错误", "鉴权失败", "认证失败"))
        if auth_failure:
            raise HTTPException(status_code=409, detail=f"ZPAY 商户凭据校验失败：{message}")
        return {"ok": True, "reachable": True, "credential_status": "accepted" if int(payload.get("code") or 0) == 1 else "api_reachable", "message": message, "upstream_code": payload.get("code")}

    @app.middleware("http")
    async def user_console_commerce_boundary(request: Request, call_next):
        # When billing is explicitly enabled by the administrator, owned API keys
        # need a positive wallet balance. Legacy/admin-managed keys are untouched.
        if request.url.path.startswith("/v1/") and bool(pricing.public().get("billing_enabled")):
            supplied = str(request.headers.get("x-api-key") or "").strip()
            authorization = str(request.headers.get("authorization") or "").strip()
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
            if supplied:
                digest = token_hash(supplied)
                matched = next((item for item in api_keys.keys.values() if secrets.compare_digest(str(item.token_hash), digest)), None)
                owner = accounts.owner_for_key(str(matched.key_id)) if matched else None
                if owner and billing.balance_cents(owner) <= 0:
                    return JSONResponse(status_code=402, content={"error": {"message": "账户余额不足，请先充值", "type": "billing_error", "code": "insufficient_balance"}})
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ADMIN_JS_ASSET}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
