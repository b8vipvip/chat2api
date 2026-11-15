"""Pricing model aliases must resolve deterministically to their exact rows."""
from __future__ import annotations

from app.user_commerce import PricingStore


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
