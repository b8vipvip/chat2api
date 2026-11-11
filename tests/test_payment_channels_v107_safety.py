from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_payment_safety_uses_canonical_data_secret_and_blocks_txid_reuse() -> None:
    source = (ROOT / "app" / "payment_channels_v107_safety_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "channels.keyring is not getattr(app.state, \"data_keyring\", None)" in source
    assert "must never replace its active writer" in source
    assert "load_or_create_data_secret" not in source
    assert "禁止重复入账" in source
    assert 'item.get("status") == "paid"' in source
    assert "/console?payment=success#billing" in source
    assert "install_payment_channels_v107_safety_patch(app)" in entry
    assert entry.index("install_payment_channels_v106_patch(app)") < entry.index("install_payment_channels_v107_safety_patch(app)")
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")
