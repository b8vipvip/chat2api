from __future__ import annotations

from pathlib import Path

import pytest

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
