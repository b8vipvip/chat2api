"""Bounded runtime adapter for the C-06 in-flight token reservation ledger."""
from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import HTTPException

from .quota_reservations import QuotaExceeded, QuotaReservationError
from .token_usage import usage_for
from .user_commerce import InsufficientWalletBalance, WalletReservationError, _pricing_model_key

logger = logging.getLogger(__name__)


def reserve_wallet_charge(
    app: Any, request_id: str, principal: Any, model_id: str,
    prompt_tokens: int, output_tokens: int,
) -> dict[str, Any] | None:
    """Reserve estimated wallet cents for a user-owned, priced model request.

    This is an admission hold, not a second charge ledger. BillingStore remains
    the sole owner of the final request_id charge and wallet debit.
    """
    if str(getattr(principal, "kind", "")) == "master":
        return None
    accounts = getattr(app.state, "user_accounts", None)
    if accounts is None:
        return None
    key_id = str(getattr(principal, "key_id", "") or "")
    owner = accounts.owner_for_key(key_id) if key_id else None
    if not owner:
        return None
    billing = getattr(app.state, "user_billing", None)
    pricing = getattr(app.state, "user_pricing", None)
    if billing is None or pricing is None:
        raise HTTPException(status_code=503, detail="Wallet reservation is unavailable")
    try:
        config = pricing.public()
        if not bool(config.get("billing_enabled")):
            return None
        price = pricing.model(model_id)
        if not price or price.get("enabled", True) is not True:
            # Disabled and unpriced rows are both closed to new paid requests.
            # An intentional free model must have an explicit enabled zero-rate
            # row; merely disabling its price must not bypass wallet admission.
            raise HTTPException(
                status_code=503,
                detail="Wallet price is not configured or enabled for this model",
            )
        # Image and realtime Voice costs are not represented by text-token
        # usage in the current Worker protocol. A positive per-token price
        # would therefore undercharge these endpoints; only an explicit
        # all-zero row may opt them into intentionally free use.
        if str(model_id or "").strip().lower() in {"gpt-image", "gpt-live", "gpt-live-mini"}:
            rate_fields = (
                "input_usd_per_million",
                "cached_input_usd_per_million",
                "output_usd_per_million",
            )
            if any(float(price.get(field) or 0) > 0 for field in rate_fields):
                raise HTTPException(
                    status_code=503,
                    detail="Provider usage for this model is not metered for wallet billing",
                )
        fx = float(config.get("usd_cny_rate") or 0)
        if fx <= 0:
            raise ValueError("Invalid USD/CNY billing rate")
        usd = (
            max(0, int(prompt_tokens)) * float(price.get("input_usd_per_million") or 0)
            + max(0, int(output_tokens)) * float(price.get("output_usd_per_million") or 0)
        ) / 1_000_000
        rate_fields = (
            "input_usd_per_million",
            "cached_input_usd_per_million",
            "output_usd_per_million",
        )
        has_positive_rate = any(float(price.get(field) or 0) > 0 for field in rate_fields)
        # A positive-rate request always gets a durable admission snapshot and
        # at least a one-cent hold, even when nearest-cent rounding would be 0.
        estimated_cny_cents = max(1, int(math.ceil(usd * fx * 100))) if has_positive_rate else 0
        if estimated_cny_cents <= 0:
            return None
        catalog_snapshot: dict[str, dict[str, Any]] = {}
        for row in config.get("models") or []:
            if not isinstance(row, dict):
                continue
            key = _pricing_model_key(row.get("model_id"))
            if key:
                catalog_snapshot.setdefault(key, dict(row))
        return billing.reserve_for_request(
            str(owner), str(request_id),
            model_id=str(model_id or ""),
            estimated_cny_cents=estimated_cny_cents,
            input_tokens=max(0, int(prompt_tokens)),
            output_tokens=max(0, int(output_tokens)),
            price_snapshot=price,
            price_catalog_snapshot=catalog_snapshot,
            usd_cny_rate=fx,
        )
    except HTTPException:
        raise
    except InsufficientWalletBalance as error:
        raise HTTPException(status_code=402, detail="账户余额不足，无法预留本次请求预计费用") from error
    except WalletReservationError as error:
        raise HTTPException(status_code=503, detail="Wallet reservation failed: " + str(error)) from error
    except OSError as error:
        # Storage write failure is an admission failure, never a best-effort
        # bypass to Worker dispatch.
        raise HTTPException(status_code=503, detail="Wallet reservation storage is unavailable") from error
    except (TypeError, ValueError, OverflowError) as error:
        raise HTTPException(status_code=503, detail="Wallet reservation estimate is invalid") from error


def reserve_request_quota(
    app: Any, request_id: str, principal: Any, prompt: str, output_limit: int,
    model_id: str | None = None,
) -> dict[str, Any] | None:
    """Reserve a conservative estimate before a usage-bearing Worker dispatch."""
    if not bool(getattr(app.state, "quota_reservation_runtime_enabled", False)):
        return None
    # The server master key is deliberately not a ManagedApiKey row and has
    # no per-key token quota. Keep it on the existing unmetered path instead
    # of treating its synthetic key_id="master" as a missing managed key.
    if str(getattr(principal, "kind", "")) == "master":
        return None
    store = getattr(app.state, "quota_reservations", None)
    api_keys = getattr(app.state, "api_keys", None)
    if store is None or api_keys is None:
        raise HTTPException(status_code=503, detail="Token quota reservation is unavailable")
    key_id = str(getattr(principal, "key_id", "") or "")
    if not key_id:
        raise HTTPException(status_code=403, detail="An API key is required for token quota reservation")
    try:
        prompt_tokens = max(0, int(usage_for(str(prompt or ""), "").prompt_tokens))
        requested_output = int(output_limit)
    except (TypeError, ValueError, AttributeError) as error:
        raise HTTPException(status_code=400, detail="Invalid token quota estimate") from error
    if requested_output < 1 or requested_output > 1_000_000:
        raise HTTPException(status_code=400, detail="Requested output budget is outside the quota reservation limit")
    estimated_tokens = prompt_tokens + requested_output
    if estimated_tokens < 1 or estimated_tokens > 1_000_000:
        raise HTTPException(status_code=413, detail="Estimated request usage is too large for quota reservation")
    token_reservation = None
    try:
        token_reservation = store.reserve_for_key(api_keys, str(request_id), key_id, estimated_tokens)
        reserve_wallet_charge(
            app, str(request_id), principal, str(model_id or ""),
            prompt_tokens, requested_output,
        )
        return token_reservation
    except HTTPException:
        if token_reservation:
            try:
                store.release(str(request_id), key_id)
            except Exception:
                pass
        raise
    except QuotaExceeded as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except QuotaReservationError as error:
        raise HTTPException(status_code=503, detail="Token quota reservation failed: " + str(error)) from error
    except Exception as error:
        # Unexpected wallet/journal persistence failure must not leave a
        # token hold that blocks unrelated, valid requests.
        if token_reservation:
            try:
                store.release(str(request_id), key_id)
            except Exception:
                logger.exception("Token hold cleanup failed after quota admission exception request_id=%s", request_id)
        raise HTTPException(status_code=503, detail="Quota admission is unavailable") from error


# Images and Voice do not yet expose provider-verified usage in the Worker
# protocol. These fixed amounts are admission holds only; terminal settlement
# continues to use the existing estimated text usage emitted by each executor.
IMAGE_GENERATION_RESERVATION_FLOOR_TOKENS = 4096
AUDIO_REQUEST_RESERVATION_FLOOR_TOKENS = 4096


def reserve_image_generation_quota(
    app: Any, request_id: str, principal: Any, prompt: str, image_count: int = 1,
    model_id: str = "gpt-image",
) -> dict[str, Any] | None:
    if type(image_count) is not int or not 1 <= image_count <= 4:
        raise HTTPException(status_code=400, detail="Image count is outside the quota reservation limit")
    return reserve_request_quota(
        app, request_id, principal, prompt,
        IMAGE_GENERATION_RESERVATION_FLOOR_TOKENS * image_count,
        model_id=model_id,
    )


def reserve_audio_request_quota(
    app: Any, request_id: str, principal: Any, prompt: str, model_id: str = "gpt-live",
) -> dict[str, Any] | None:
    """Hold an admission allowance for speech or uploaded-audio requests.

    Text input (speech prompt or optional conversation instruction) is
    estimated before dispatch. Uploaded audio bytes are not converted into
    fabricated tokens; the fixed floor is admission capacity only. The Voice
    Worker does not report provider-verified usage, so terminal settlement
    continues to use the existing estimated prompt/transcript row.
    """
    return reserve_request_quota(
        app, request_id, principal, prompt,
        AUDIO_REQUEST_RESERVATION_FLOOR_TOKENS,
        model_id=model_id,
    )


def install_quota_reservation_runtime_v1(app: Any) -> Any:
    if getattr(app.state, "quota_reservation_runtime_v1_installed", False):
        return app
    store = getattr(app.state, "quota_reservations", None)
    api_keys = getattr(app.state, "api_keys", None)
    telemetry = getattr(app.state, "telemetry", None)
    if store is None or api_keys is None or telemetry is None:
        raise RuntimeError("C-06 runtime requires quota_reservations, api_keys and telemetry app state")

    async def account_usage_with_reservations(record: dict[str, Any]) -> None:
        # TelemetryStore invokes this for queued/running snapshots too. The
        # reservation store treats those as pending and settles/releases only
        # the Broker-owned terminal projection.
        status = str(record.get("status") or "").lower()
        try:
            await store.commit_completed_usage(record, api_keys)
        finally:
            if status in {"error", "cancelled", "stalled", "failed", "timeout"}:
                request_id = str(record.get("request_id") or "")
                key_id = str(record.get("api_key_id") or "")
                accounts = getattr(app.state, "user_accounts", None)
                billing = getattr(app.state, "user_billing", None)
                owner = accounts.owner_for_key(key_id) if accounts is not None and key_id else None
                if owner and billing is not None and request_id:
                    try:
                        billing.release_reservation(request_id, str(owner), reason="request_" + status)
                    except Exception as error:
                        logger.warning(
                            "Wallet reservation release deferred request_id=%s error_type=%s",
                            request_id, type(error).__name__,
                        )

    telemetry.usage_accounting = account_usage_with_reservations
    app.state.quota_reservation_runtime_enabled = True
    app.state.quota_reservation_runtime_coverage = [
        "/v1/chat/completions",
        "/v1/responses",
        "/v1/images/generations",
        "/v1/responses#image_generation (delegated to Images executor)",
        "/v1/audio/speech",
        "/v1/audio/conversations",
    ]
    app.state.quota_reservation_runtime_v1_installed = True
    return app
