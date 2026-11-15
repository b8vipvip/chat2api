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
