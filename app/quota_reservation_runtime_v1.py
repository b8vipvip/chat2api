"""Bounded runtime adapter for the C-06 in-flight token reservation ledger."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .quota_reservations import QuotaExceeded, QuotaReservationError
from .token_usage import usage_for


def reserve_request_quota(
    app: Any, request_id: str, principal: Any, prompt: str, output_limit: int,
) -> dict[str, Any] | None:
    """Reserve a conservative estimate before a Chat Completions Worker dispatch."""
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
    try:
        return store.reserve_for_key(api_keys, str(request_id), key_id, estimated_tokens)
    except QuotaExceeded as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except QuotaReservationError as error:
        raise HTTPException(status_code=503, detail="Token quota reservation failed: " + str(error)) from error


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
        await store.commit_completed_usage(record, api_keys)

    telemetry.usage_accounting = account_usage_with_reservations
    app.state.quota_reservation_runtime_enabled = True
    app.state.quota_reservation_runtime_coverage = ["/v1/chat/completions", "/v1/responses"]
    app.state.quota_reservation_runtime_v1_installed = True
    return app
