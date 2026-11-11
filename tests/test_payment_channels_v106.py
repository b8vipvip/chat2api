from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data_keyring import DataKeyring, DataKeyringError
from app.payment_channels_v106_patch import PaymentChannelsStore, _valid_tron_address


ROOT = Path(__file__).resolve().parents[1]


def test_aggregate_zpay_only_exposes_checked_payment_rails(tmp_path: Path) -> None:
    store = PaymentChannelsStore(tmp_path, "local-secret")
    store.update({
        "public_origin": "https://api.example.com",
        "aggregate": {"zpay": {
            "enabled": True,
            "pid": "10001",
            "key": "merchant-key",
            "alipay_enabled": True,
            "wechat_enabled": False,
            "alipay_cid": "1,2",
            "wechat_cid": "3",
        }},
        "direct": {"paypal": {}, "usdt": {}},
    })
    public = store.user_public()
    assert [row["id"] for row in public["methods"]] == ["alipay"]
    assert "merchant-key" not in str(store.admin_public())
    assert "merchant-key" not in (tmp_path / "user_payment_channels_v106.json").read_text(encoding="utf-8")


def test_direct_paypal_uses_one_provider_enable_switch_and_hides_secret(tmp_path: Path) -> None:
    store = PaymentChannelsStore(tmp_path, "local-secret")
    store.update({
        "aggregate": {"zpay": {}},
        "direct": {
            "paypal": {"enabled": True, "client_id": "paypal-client", "client_secret": "paypal-secret", "sandbox": True},
            "usdt": {},
        },
    })
    methods = store.user_public()["methods"]
    assert [row["id"] for row in methods] == ["paypal"]
    admin = store.admin_public()["direct"]["paypal"]
    assert admin["configured"] is True
    assert admin["secret_configured"] is True
    assert "paypal-secret" not in str(store.admin_public())
    assert "paypal-secret" not in (tmp_path / "user_payment_channels_v106.json").read_text(encoding="utf-8")


def test_usdt_is_tron_trc20_only_and_requires_valid_address(tmp_path: Path) -> None:
    store = PaymentChannelsStore(tmp_path, "local-secret")
    valid = "TWd4WrZ9wn84f5x1hZhL4DHvk738ns5jwb"
    assert _valid_tron_address(valid) is True
    with pytest.raises(ValueError):
        store.update({"aggregate": {"zpay": {}}, "direct": {"paypal": {}, "usdt": {"enabled": True, "address": "0xdeadbeef"}}})
    store.update({
        "aggregate": {"zpay": {}},
        "direct": {"paypal": {}, "usdt": {"enabled": True, "address": valid, "qr_image_url": "https://example.com/usdt.png", "address_link": "https://tronscan.org/#/address/example"}},
    })
    method = store.user_public()["methods"][0]
    assert method["id"] == "usdt"
    assert method["network"] == "TRON (TRC20)"
    assert method["address"] == valid


def test_v106_admin_and_user_assets_express_requested_payment_structure() -> None:
    admin = (ROOT / "app" / "admin_payments_v106.js").read_text(encoding="utf-8")
    user = (ROOT / "app" / "user_payments_v106.js").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "payment_channels_v106_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    for label in ("官方支付 · PayPal", "聚合支付 · ZPAY", "启用支付宝", "启用微信支付", "USDT 支付 · 欧意收款", "TRON (TRC20)"):
        assert label in admin
    assert "payment.alipay" not in user
    assert "/api/user/payment-channels-v106" in user
    assert "/api/user/billing/recharge-v106" in user
    assert "manual_confirmation" in patch
    assert "v2/checkout/orders" in patch
    assert "install_payment_channels_v106_patch(app)" in entry
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")


def test_usdt_manual_confirmation_is_fail_closed() -> None:
    patch = (ROOT / "app" / "payment_channels_v106_patch.py").read_text(encoding="utf-8")
    assert 'order["status"] = "reviewing"' in patch
    assert "用户尚未提交有效交易哈希" in patch
    assert "billing.settle_order(order_id, txid)" in patch
    assert "confirm(" not in patch


def test_payment_channels_legacy_ciphertext_is_read_then_new_writes_use_keyring(tmp_path: Path) -> None:
    legacy = PaymentChannelsStore(tmp_path, "legacy-channel-secret")
    legacy.update({
        "aggregate": {"zpay": {"enabled": True, "pid": "10001", "key": "old-zpay-secret"}},
        "direct": {
            "paypal": {"enabled": True, "client_id": "old-client", "client_secret": "old-paypal-secret"},
            "usdt": {},
        },
    })

    keyring = DataKeyring(
        tmp_path,
        legacy_secrets={"payment_channels": ["legacy-channel-secret"]},
    )
    store = PaymentChannelsStore(
        tmp_path,
        keyring,
        legacy_secrets=["legacy-channel-secret"],
    )
    assert store.zpay_key() == "old-zpay-secret"
    assert store.paypal_secret() == "old-paypal-secret"

    assert store.migrate_legacy_ciphertexts() == 2
    assert store.migrate_legacy_ciphertexts() == 0
    raw = json.loads((tmp_path / "user_payment_channels_v106.json").read_text(encoding="utf-8"))
    zpay_envelope = json.loads(raw["aggregate"]["zpay"]["key_ciphertext"])
    paypal_envelope = json.loads(raw["direct"]["paypal"]["client_secret_ciphertext"])
    assert zpay_envelope["format"] == DataKeyring.FORMAT
    assert paypal_envelope["format"] == DataKeyring.FORMAT
    assert zpay_envelope["purpose"] == "payment_channels"
    assert paypal_envelope["purpose"] == "payment_channels"
    assert "old-zpay-secret" not in str(raw)
    assert "old-paypal-secret" not in str(raw)
    assert store.zpay_key() == "old-zpay-secret"
    assert store.paypal_secret() == "old-paypal-secret"


def test_payment_channels_unknown_legacy_key_fails_closed(tmp_path: Path) -> None:
    legacy = PaymentChannelsStore(tmp_path, "actual-old-key")
    legacy.update({
        "aggregate": {"zpay": {"enabled": True, "pid": "10001", "key": "old-secret"}},
        "direct": {"paypal": {}, "usdt": {}},
    })

    with pytest.raises(DataKeyringError, match="payment channel credentials cannot be proven recoverable"):
        DataKeyring(
            tmp_path,
            legacy_secrets={"payment_channels": ["wrong-old-key"]},
        )
    assert not (tmp_path / "data_secret.key").exists()
