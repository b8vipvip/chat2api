from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_REVISION = 107


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


def install_payment_channels_v107_safety_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "payment_channels_v107_safety_installed", False):
        return app
    app.state.payment_channels_v107_safety_installed = True

    # Keep the V106 payment cipher stable until the explicitly deferred
    # DataKeyring migration has a backup/readback/rollback plan. V106 pins its
    # legacy key in a 0600 local file and validates existing ciphertext before
    # the app is allowed to serve payment configuration.
    billing = app.state.user_billing

    @app.middleware("http")
    async def payment_settlement_safety_v107(request: Request, call_next):
        path = request.url.path

        # A TRON TXID is a one-time settlement proof. Never allow one already-paid
        # transaction to credit a second recharge order, even if an administrator
        # accidentally confirms the same hash twice under different order IDs.
        match = re.fullmatch(r"/api/admin/payment-channels-v106/usdt-orders/([^/]+)/confirm", path)
        if request.method == "POST" and match:
            order_id = match.group(1)
            with billing.lock:
                row = billing.orders.get(order_id)
                txid = str((row or {}).get("txid") or "").lower()
                duplicate = next(
                    (
                        item for other_id, item in billing.orders.items()
                        if other_id != order_id
                        and item.get("channel") == "usdt"
                        and item.get("status") == "paid"
                        and txid
                        and secrets.compare_digest(str(item.get("txid") or "").lower(), txid)
                    ),
                    None,
                )
            if duplicate:
                return JSONResponse(status_code=409, content={"detail": "该 TRON 交易哈希已经用于其他已入账订单，禁止重复入账"})

        response = await call_next(request)

        # Keep the hash fragment exactly '#billing' so the existing user console
        # router opens the billing view after PayPal returns. Payment result is a
        # normal query parameter placed before the fragment.
        if path == "/api/user/payments/v106/paypal/return" and 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            if location == "/console#billing&payment=success":
                response.headers["location"] = "/console?payment=success#billing"
            elif location == "/console#billing&payment=failed":
                response.headers["location"] = "/console?payment=failed#billing"
        return response

    return app
