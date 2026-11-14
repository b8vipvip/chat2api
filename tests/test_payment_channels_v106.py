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
    assert "billing.settle_usdt_order(order_id, txid)" in patch
    assert "confirm(" not in patch



def test_v106_extended_order_amounts_are_strict_integer_cents_and_create_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v106-order-rollback-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    billing = app.state.user_billing

    for amount in (1.5, True):
        with pytest.raises(ValueError, match="整数分"):
            billing.create_order("usr_v106_strict", amount, "paypal")
        with pytest.raises(ValueError, match="整数分"):
            billing.create_order("usr_v106_strict", amount, "usdt")
    assert billing.orders == {}

    def fail_save() -> None:
        raise OSError("simulated v106 payment ledger outage")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="ledger outage"):
            billing.create_order("usr_v106_strict", 125, "paypal")
    assert billing.orders == {}
    assert billing.orders_for_user("usr_v106_strict") == []


def test_v106_usdt_order_mutations_restore_memory_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v106-order-mutation-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    user = app.state.user_accounts.register("v106-usdt@example.com", "long-enough-password")
    session = app.state.user_accounts.create_session(user["user_id"])
    billing = app.state.user_billing
    valid_address = "TWd4WrZ9wn84f5x1hZhL4DHvk738ns5jwb"
    app.state.user_payment_channels_v106.update({
        "aggregate": {"zpay": {}},
        "direct": {"paypal": {}, "usdt": {"enabled": True, "address": valid_address}},
    })

    original_save = billing._save
    save_calls = 0

    def fail_second_save() -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("simulated checkout metadata persistence failure")
        original_save()

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_second_save)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("chat2api_user_session", session)
            response = client.post(
                "/api/user/billing/recharge-v106",
                json={"amount_cents": 125, "channel": "usdt"},
                headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            )
    assert response.status_code == 500
    orders = billing.orders_for_user(user["user_id"])
    assert len(orders) == 1
    order = orders[0]
    assert order["status"] == "pending"
    assert order["provider_amount"] == ""
    assert order["provider_currency"] == ""
    assert order["qr_payload"] == ""

    # Failed submission must not leave a txid/status mutation in memory.
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", lambda: (_ for _ in ()).throw(OSError("txid save failed")))
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("chat2api_user_session", session)
            response = client.post(
                f"/api/user/payments/v106/usdt/{order['order_id']}/txid",
                json={"txid": "a" * 64},
                headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            )
    assert response.status_code == 500
    unchanged = billing.orders[order["order_id"]]
    assert unchanged["status"] == "pending"
    assert unchanged["txid"] == ""

    # A failed administrator rejection must leave the submitted review intact.
    unchanged["status"] = "reviewing"
    unchanged["txid"] = "a" * 64
    original_save()
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", lambda: (_ for _ in ()).throw(OSError("reject save failed")))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/admin/payment-channels-v106/usdt-orders/{order['order_id']}/reject",
                headers={"x-api-key": "v106-order-mutation-master"},
            )
    assert response.status_code == 500
    unchanged = billing.orders[order["order_id"]]
    assert unchanged["status"] == "reviewing"
    assert unchanged["txid"] == "a" * 64


def test_v106_recharge_api_rejects_non_integer_cent_payloads(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v106-strict-recharge-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    user = app.state.user_accounts.register("v106-strict@example.com", "long-enough-password")
    session = app.state.user_accounts.create_session(user["user_id"])
    app.state.user_payment_channels_v106.update({
        "aggregate": {"zpay": {}},
        "direct": {"paypal": {"enabled": True, "client_id": "paypal-client", "client_secret": "paypal-secret"}, "usdt": {}},
    })
    with TestClient(app) as client:
        client.cookies.set("chat2api_user_session", session)
        for amount in (1.5, True, "125"):
            response = client.post(
                "/api/user/billing/recharge-v106",
                json={"amount_cents": amount, "channel": "paypal"},
                headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            )
            assert response.status_code == 422, response.text
    assert app.state.user_billing.orders == {}



def test_payment_channel_config_load_fails_closed_without_overwriting_source(tmp_path: Path) -> None:
    source = b'{"aggregate":'
    path = tmp_path / "user_payment_channels_v106.json"
    path.write_bytes(source)
    with pytest.raises(RuntimeError, match="Payment channel configuration is unreadable"):
        PaymentChannelsStore(tmp_path, "local-secret")
    assert path.read_bytes() == source


def test_payment_channel_update_rolls_back_memory_on_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PaymentChannelsStore(tmp_path, "local-secret")
    store.update({
        "aggregate": {"zpay": {"enabled": True, "pid": "pid-1", "key": "old-zpay-secret"}},
        "direct": {"paypal": {"enabled": True, "client_id": "old-client", "client_secret": "old-paypal-secret"}},
    })
    previous = copy.deepcopy(store.payload)
    persisted = (tmp_path / "user_payment_channels_v106.json").read_bytes()

    def fail_save() -> None:
        raise OSError("simulated payment config fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_save", fail_save)
        with pytest.raises(OSError, match="fsync failure"):
            store.update({
                "public_origin": "https://changed.example",
                "aggregate": {"zpay": {"enabled": False, "pid": "new-pid", "key": "new-zpay-secret"}},
                "direct": {"paypal": {"enabled": False, "client_id": "new-client", "client_secret": "new-paypal-secret"}},
            })
    assert store.payload == previous
    assert (tmp_path / "user_payment_channels_v106.json").read_bytes() == persisted
    assert store.zpay_key() == "old-zpay-secret"
    assert store.paypal_secret() == "old-paypal-secret"


def test_invalid_payment_channel_update_does_not_partially_mutate_memory(tmp_path: Path) -> None:
    store = PaymentChannelsStore(tmp_path, "local-secret")
    store.update({
        "aggregate": {"zpay": {"enabled": True, "pid": "old-pid", "key": "old-zpay-secret"}},
        "direct": {"paypal": {"enabled": True, "client_id": "old-client", "client_secret": "old-paypal-secret"}},
    })
    previous = copy.deepcopy(store.payload)
    with pytest.raises(ValueError, match="ZPAY 渠道 ID"):
        store.update({
            "aggregate": {"zpay": {"enabled": False, "pid": "new-pid", "alipay_cid": "not-a-number"}},
            "direct": {"paypal": {"enabled": False, "client_id": "new-client"}},
        })
    assert store.payload == previous
    assert store.zpay_key() == "old-zpay-secret"
    assert store.paypal_secret() == "old-paypal-secret"
