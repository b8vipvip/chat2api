"""Pricing model aliases must resolve deterministically to their exact rows."""
from __future__ import annotations

from app.user_commerce import BillingStore, PricingStore


def test_pricing_alias_lookup_prefers_exact_model_id_and_ignores_row_order(tmp_path):
    pricing = PricingStore(tmp_path)
    original = pricing.public()
    models = [dict(row) for row in original["models"]]
    canonical = next(row for row in models if row["model_id"] == "gpt-5.6-sol")
    alias = next(row for row in models if row["model_id"] == "gpt-5.6")
    canonical["input_usd_per_million"] = 11.0
    alias["input_usd_per_million"] = 22.0

    pricing.update(billing_enabled=True, usd_cny_rate=7.2, models=models)
    assert pricing.model("gpt-5.6-sol")["input_usd_per_million"] == 11.0
    assert pricing.model("gpt-5.6")["input_usd_per_million"] == 22.0
    assert pricing.model("GPT-5.6")["input_usd_per_million"] == 22.0

    pricing.update(billing_enabled=True, usd_cny_rate=7.2, models=list(reversed(models)))
    assert pricing.model("gpt-5.6-sol")["input_usd_per_million"] == 11.0
    assert pricing.model("gpt-5.6")["input_usd_per_million"] == 22.0

    restarted = PricingStore(tmp_path)
    assert restarted.model("gpt-5.6-sol")["input_usd_per_million"] == 11.0
    assert restarted.model("gpt-5.6")["input_usd_per_million"] == 22.0



def test_pricing_rejects_case_insensitive_duplicate_model_ids(tmp_path):
    import pytest

    pricing = PricingStore(tmp_path)
    original = pricing.public()
    models = [dict(row) for row in original["models"]]
    duplicate = dict(models[0])
    duplicate["model_id"] = models[0]["model_id"].upper()
    models.append(duplicate)

    with pytest.raises(ValueError, match="identity is invalid or duplicated"):
        pricing.update(billing_enabled=True, usd_cny_rate=7.2, models=models)

    assert pricing.public() == original



def test_wallet_reservation_snapshot_preserves_exact_alias_prices(tmp_path):
    pricing = PricingStore(tmp_path)
    config = pricing.public()
    models = [dict(row) for row in config["models"]]
    canonical = next(row for row in models if row["model_id"] == "gpt-5.6-sol")
    alias = next(row for row in models if row["model_id"] == "gpt-5.6")
    canonical["input_usd_per_million"] = 11.0
    alias["input_usd_per_million"] = 22.0
    pricing.update(billing_enabled=True, usd_cny_rate=7.2, models=models)

    admission_catalog = {
        str(row["model_id"]).strip().casefold(): dict(row)
        for row in pricing.public()["models"]
    }
    requested_price = pricing.model("gpt-5.6")
    billing = BillingStore(tmp_path)
    user_id = "usr_alias_snapshot"
    request_id = "req_alias_snapshot_123"
    billing.credit(user_id, 100_000)
    billing.reserve_for_request(
        user_id,
        request_id,
        model_id="gpt-5.6",
        estimated_cny_cents=20_000,
        input_tokens=1_000_000,
        output_tokens=0,
        price_snapshot=requested_price,
        price_catalog_snapshot=admission_catalog,
        usd_cny_rate=7.2,
    )
    # Restart between admission and terminal settlement to verify that both
    # independently priced aliases survive the durable reservation snapshot.
    billing = BillingStore(tmp_path)
    persisted_catalog = billing.reservations[request_id]["price_catalog_snapshot"]
    assert persisted_catalog["gpt-5.6"]["input_usd_per_million"] == 22.0
    assert persisted_catalog["gpt-5.6-sol"]["input_usd_per_million"] == 11.0

    charge = billing.record_charge(
        user_id,
        {
            "request_id": request_id,
            "status": "completed",
            "requested_model": "gpt-5.6",
            "diagnostics": {"actual_model": "gpt-5.6-sol"},
            "usage": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 0,
                "total_tokens": 1_000_000,
                "cached_input_tokens": 0,
            },
        },
        pricing,
    )

    assert charge is not None
    assert charge["model_id"] == "gpt-5.6-sol"
    assert charge["priced_model_id"] == "gpt-5.6-sol"
    assert charge["price_snapshot"]["input_usd_per_million"] == 11.0
    assert charge["cost_cny_cents"] == 7_920
