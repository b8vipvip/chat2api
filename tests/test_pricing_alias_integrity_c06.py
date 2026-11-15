"""Pricing alias canonicalization must not permit ambiguous billing rates."""
from __future__ import annotations

import json

import pytest

from app.user_commerce import PricingStore


def test_canonical_model_aliases_must_share_enabled_state_and_rates(tmp_path):
    pricing = PricingStore(tmp_path)
    original = pricing.public()
    models = [dict(row) for row in original["models"]]
    alias = next(row for row in models if row["model_id"] == "gpt-5.6")
    alias["input_usd_per_million"] += 1.0

    with pytest.raises(ValueError, match="canonical model.*identical"):
        pricing.update(
            billing_enabled=True,
            usd_cny_rate=7.2,
            models=models,
        )

    assert pricing.public() == original
    assert pricing.model("gpt-5.6")["input_usd_per_million"] == pricing.model("gpt-5.6-sol")["input_usd_per_million"]


def test_persisted_conflicting_alias_prices_fail_closed_without_rewrite(tmp_path):
    pricing = PricingStore(tmp_path)
    path = tmp_path / "user_pricing.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    alias = next(row for row in payload["models"] if row["model_id"] == "gpt-5.6")
    alias["output_usd_per_million"] += 0.5
    original = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(original)

    with pytest.raises(RuntimeError, match="Pricing configuration is unreadable"):
        PricingStore(tmp_path)

    assert path.read_bytes() == original
