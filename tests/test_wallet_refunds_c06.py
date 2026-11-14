"""C-06 internal wallet chargeback: idempotency, limits, durability and RBAC."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane import ControlPlaneStore, permission_for_admin_route
from app.main import create_app
from app.quota_reservation_admin_v1_patch import install_quota_reservation_admin_v1
from app.user_commerce import BillingStore, PricingStore, WalletReservationError
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def _charged_wallet(tmp_path: Path) -> tuple[BillingStore, PricingStore]:
    pricing = PricingStore(tmp_path)
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = BillingStore(tmp_path)
    billing.credit("usr_refund", 1000)
    charge = billing.record_charge("usr_refund", {
        "request_id": "req_refundable",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500, "estimated": True},
    }, pricing)
    assert charge is not None and charge["cost_cny_cents"] == 10 and charge["debited"]
    assert billing.balance_cents("usr_refund") == 990
    return billing, pricing


def test_refund_is_idempotent_partial_and_never_over_refunds(tmp_path: Path) -> None:
    billing, _pricing = _charged_wallet(tmp_path)
    first = billing.refund_charge("req_refundable", "rf_partial0001", 3, "billing correction")
    assert first["amount_cents"] == 3
    assert first["user_id"] == "usr_refund"
    assert billing.refund_charge("req_refundable", "rf_partial0001", 3, "billing correction") == first
    assert billing.balance_cents("usr_refund") == 993

    with pytest.raises(WalletReservationError, match="already used"):
        billing.refund_charge("req_refundable", "rf_partial0001", 4, "billing correction")
    with pytest.raises(WalletReservationError, match="exceeds"):
        billing.refund_charge("req_refundable", "rf_more00001", 8, "billing correction")

    second = billing.refund_charge("req_refundable", "rf_remainder01", 7, "billing correction")
    assert second["amount_cents"] == 7
    assert billing.balance_cents("usr_refund") == 1000
    assert billing.charge_for_request("req_refundable")["cost_cny_cents"] == 10
    assert sum(row["amount_cents"] for row in billing.refunds_for_user("usr_refund")) == 10
    assert len(BillingStore(tmp_path).refunds_for_user("usr_refund")) == 2

    with pytest.raises(WalletReservationError, match="exceeds"):
        billing.refund_charge("req_refundable", "rf_extra00001", 1, "billing correction")


def test_refund_requires_existing_debited_charge_and_bounded_reason(tmp_path: Path) -> None:
    billing, pricing = _charged_wallet(tmp_path)
    with pytest.raises(WalletReservationError, match="no completed debited"):
        billing.refund_charge("req_unknown", "rf_unknown0001", 1, "billing correction")
    with pytest.raises(WalletReservationError, match="5-500"):
        billing.refund_charge("req_refundable", "rf_reason00001", 1, "no")
    with pytest.raises(WalletReservationError, match="positive"):
        billing.refund_charge("req_refundable", "rf_negative0001", -1, "billing correction")

    billing.record_charge("usr_refund", {
        "request_id": "req_unpriced",
        "status": "completed", "requested_model": "unknown-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    }, pricing)
    with pytest.raises(WalletReservationError, match="no completed debited"):
        billing.refund_charge("req_unpriced", "rf_unpriced01", 1, "billing correction")
    assert billing.balance_cents("usr_refund") == 990


def test_refund_disk_error_reverts_credit_and_stays_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing, _pricing = _charged_wallet(tmp_path)

    def fail_save() -> None:
        raise OSError("simulated wallet refund fsync outage")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="fsync outage"):
            billing.refund_charge("req_refundable", "rf_diskoutage", 5, "billing correction")
    assert billing.balance_cents("usr_refund") == 990
    assert billing.refunds_for_user("usr_refund") == []
    assert BillingStore(tmp_path).refunds_for_user("usr_refund") == []
    assert billing.refund_charge("req_refundable", "rf_diskoutage", 5, "billing correction")["amount_cents"] == 5
    assert billing.balance_cents("usr_refund") == 995



def test_wallet_refunds_require_billing_write_not_support_or_read_only(
    tmp_path: Path,
) -> None:
    control = ControlPlaneStore(tmp_path, root_username="owner")
    control.create_admin("billing_operator", "strong-billing-password", "billing_admin")
    control.create_admin("helpdesk_operator", "strong-support-password", "support")
    control.create_admin("system_operator", "strong-system-password", "system_admin")
    endpoint = "/api/admin/billing/charges/req_refundable/refund"
    assert control.permitted("owner", endpoint, "POST")
    assert control.permitted("billing_operator", endpoint, "POST")
    assert not control.permitted("helpdesk_operator", endpoint, "POST")
    assert not control.permitted("system_operator", endpoint, "POST")
    assert not control.permitted("billing_operator", endpoint, "GET")

def test_wallet_refund_route_is_csrf_gated_session_scoped_and_audited(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    ))
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = app.state.user_billing
    billing.credit("usr_refund", 1000)
    charge = billing.record_charge("usr_refund", {
        "request_id": "req_refundable",
        "status": "completed", "requested_model": "gpt-5.6-sol",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500, "estimated": True},
    }, pricing)
    assert charge is not None and charge["cost_cny_cents"] == 10
    install_quota_reservation_admin_v1(app)
    assert permission_for_admin_route(
        "/api/admin/billing/charges/req_refundable/refund", "POST",
    ) == "billing:write"
    assert permission_for_admin_route(
        "/api/admin/billing/charges/req_refundable/refund", "GET",
    ) is None
    endpoint = "/api/admin/billing/charges/req_refundable/refund"
    data = {"refund_id": "rf_admin00001", "amount_cents": 5, "reason": "verified correction"}
    with TestClient(app) as client:
        assert client.post(endpoint, json=data, headers=ORIGIN).status_code == 401
        login = client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-root-password",
        }, headers=ORIGIN)
        assert login.status_code == 200, login.text
        assert client.post(endpoint, json=data).status_code == 403
        refunded = client.post(endpoint, json=data, headers=ORIGIN)
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["refund"]["amount_cents"] == 5
        repeated = client.post(endpoint, json=data, headers=ORIGIN)
        assert repeated.status_code == 200, repeated.text
        assert billing.balance_cents("usr_refund") == 995
        changed = client.post(endpoint, json={**data, "amount_cents": 6}, headers=ORIGIN)
        assert changed.status_code == 409
        assert client.post(endpoint, json={**data, "refund_id": "not-valid"}, headers=ORIGIN).status_code == 422
        assert client.post(endpoint, json={**data, "amount_cents": 5.5}, headers=ORIGIN).status_code == 422
        assert client.post(endpoint, json={**data, "amount_cents": True}, headers=ORIGIN).status_code == 422
    events = app.state.admin_audit.recent(30)
    assert any(e["action"] == "billing.wallet_refund_requested" for e in events)
    assert any(e["action"] == "billing.wallet_refund_completed" for e in events)


def test_wallet_refund_history_is_visible_only_to_own_account(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-refund-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    accounts = app.state.user_accounts
    alice = accounts.register("wallet-alice@example.com", "long-enough-password")
    bob = accounts.register("wallet-bob@example.com", "different-long-password")
    alice_id = alice["user_id"]
    bob_id = bob["user_id"]
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = app.state.user_billing
    for name, user_id in (("alice", alice_id), ("bob", bob_id)):
        billing.credit(user_id, 1000)
        row = billing.record_charge(user_id, {
            "request_id": "req_refund_" + name,
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        }, pricing)
        assert row is not None and row["cost_cny_cents"] == 10
    billing.refund_charge("req_refund_alice", "rf_alice00001", 5, "verified correction")

    with TestClient(app) as client:
        client.cookies.set("chat2api_user_session", accounts.create_session(alice_id))
        alice_view = client.get("/api/user/billing")
        assert alice_view.status_code == 200, alice_view.text
        assert alice_view.json()["refunded_cents"] == 5
        assert alice_view.json()["net_debited_spend_cents"] == 5
        assert len(alice_view.json()["refunds"]) == 1
        assert alice_view.json()["refunds"][0]["request_id"] == "req_refund_alice"
        client.cookies.set("chat2api_user_session", accounts.create_session(bob_id))
        bob_view = client.get("/api/user/billing")
        assert bob_view.status_code == 200, bob_view.text
        assert bob_view.json()["refunded_cents"] == 0
        assert bob_view.json()["net_debited_spend_cents"] == 10
        assert bob_view.json()["refunds"] == []
        assert "rf_alice00001" not in bob_view.text


def test_wallet_credit_fsync_failure_never_leaves_spendable_phantom_balance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing = BillingStore(tmp_path)

    def fail_save() -> None:
        raise OSError("simulated wallet credit fsync outage")

    # First credit for an account: no in-memory balance must survive the failed write.
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="fsync outage"):
            billing.credit("usr_credit_new", 500)
    assert billing.balance_cents("usr_credit_new") == 0
    assert billing.available_balance_cents("usr_credit_new") == 0
    assert BillingStore(tmp_path).balance_cents("usr_credit_new") == 0

    assert billing.credit("usr_credit_existing", 100) == 100
    # Existing durable balance: a failed top-up must restore the exact prior value.
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="fsync outage"):
            billing.credit("usr_credit_existing", 50)
    assert billing.balance_cents("usr_credit_existing") == 100
    assert billing.available_balance_cents("usr_credit_existing") == 100
    assert BillingStore(tmp_path).balance_cents("usr_credit_existing") == 100

    # The exact same credit may be safely retried after storage recovers.
    assert billing.credit("usr_credit_existing", 50) == 150
    assert BillingStore(tmp_path).balance_cents("usr_credit_existing") == 150


def test_wallet_accounting_caps_inconsistent_cached_input_to_total_input(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = BillingStore(tmp_path)
    billing.credit("usr_cache_guard", 10_000)

    row = billing.record_charge("usr_cache_guard", {
        "request_id": "req_cache_exceeds_input",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {
            "prompt_tokens": 1_000_000,
            "cached_input_tokens": 2_000_000,
            "completion_tokens": 0,
            "estimated": True,
        },
    }, pricing)
    assert row is not None
    assert row["input_tokens"] == 1_000_000
    assert row["cached_input_tokens"] == 1_000_000
    assert row["cost_cny_cents"] == 280  # 0.40 USD per 1M * 7 CNY/USD
    assert billing.balance_cents("usr_cache_guard") == 9_720

    # Negative cache telemetry must not offset ordinary input accounting.
    other = billing.record_charge("usr_cache_guard", {
        "request_id": "req_negative_cached_input",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {
            "prompt_tokens": 1_000_000,
            "cached_input_tokens": -100,
            "completion_tokens": 0,
        },
    }, pricing)
    assert other is not None
    assert other["cached_input_tokens"] == 0
    assert other["cost_cny_cents"] == 2_800  # 4.00 USD per 1M * 7 CNY/USD
    assert billing.balance_cents("usr_cache_guard") == 6_920


@pytest.mark.parametrize("invalid_amount", [True, 1.5, "10", 0, -1, None])
def test_wallet_credit_requires_exact_positive_integer_cents(
    tmp_path: Path, invalid_amount: object,
) -> None:
    billing = BillingStore(tmp_path)
    with pytest.raises(ValueError, match="正整数分"):
        billing.credit("usr_strict_credit", invalid_amount)  # type: ignore[arg-type]
    assert billing.balance_cents("usr_strict_credit") == 0
    assert BillingStore(tmp_path).balance_cents("usr_strict_credit") == 0


def test_wallet_credit_rolls_back_on_non_os_serialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing = BillingStore(tmp_path)
    assert billing.credit("usr_serialization_credit", 125) == 125

    def fail_save() -> None:
        raise TypeError("simulated serialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.credit("usr_serialization_credit", 25)
    assert billing.balance_cents("usr_serialization_credit") == 125
    assert billing.available_balance_cents("usr_serialization_credit") == 125
    assert BillingStore(tmp_path).balance_cents("usr_serialization_credit") == 125


def test_wallet_ledger_mutations_roll_back_non_os_save_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = PricingStore(tmp_path)
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = BillingStore(tmp_path)
    billing.credit("usr_non_os", 1000)
    kwargs = {
        "model_id": "gpt-5.6-sol",
        "estimated_cny_cents": 100,
        "input_tokens": 1000,
        "output_tokens": 500,
        "price_snapshot": pricing.model("gpt-5.6-sol"),
        "usd_cny_rate": 7.0,
    }

    def fail_save() -> None:
        raise TypeError("simulated ledger serialization failure")

    # A failed hold write cannot remain as an in-memory capacity reservation.
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.reserve_for_request("usr_non_os", "req_non_os_hold", **kwargs)
    assert "req_non_os_hold" not in billing.reservations
    assert billing.available_balance_cents("usr_non_os") == 1000

    billing.reserve_for_request("usr_non_os", "req_non_os_charge", **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.release_reservation("req_non_os_charge", "usr_non_os")
    assert billing.reservations["req_non_os_charge"]["state"] == "reserved"

    request = {
        "request_id": "req_non_os_charge",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "estimated": True},
    }
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.record_charge("usr_non_os", request, pricing)
    assert billing.balance_cents("usr_non_os") == 1000
    assert billing.charge_for_request("req_non_os_charge") is None
    assert billing.reservations["req_non_os_charge"]["state"] == "reserved"

    charge = billing.record_charge("usr_non_os", request, pricing)
    assert charge is not None and charge["debited"] is True
    assert billing.balance_cents("usr_non_os") == 990
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.refund_charge(
                "req_non_os_charge", "rf_nonos000001", 5, "verified correction",
            )
    assert billing.balance_cents("usr_non_os") == 990
    assert billing.refunds_for_user("usr_non_os") == []
    reopened = BillingStore(tmp_path)
    assert reopened.balance_cents("usr_non_os") == 990
    assert reopened.charge_for_request("req_non_os_charge") == charge
    assert reopened.refunds_for_user("usr_non_os") == []


def test_wallet_recharge_api_and_order_store_require_strict_integer_cents(tmp_path: Path) -> None:
    billing = BillingStore(tmp_path)
    with pytest.raises(ValueError, match="整数分"):
        billing.create_order("usr_strict_cents", 1.5, "alipay")
    with pytest.raises(ValueError, match="整数分"):
        billing.create_order("usr_strict_cents", True, "alipay")
    order = billing.create_order("usr_strict_cents", 125, "alipay")
    assert order["amount_cents"] == 125

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path / "api"),
        CHAT2API_API_KEY="strict-recharge-master-key",
    ))
    install_user_console_v104_patch(app)
    account = app.state.user_accounts.register(
        "strict-recharge@example.com", "long-enough-password",
    )
    with TestClient(app) as client:
        client.cookies.set(
            "chat2api_user_session",
            app.state.user_accounts.create_session(account["user_id"]),
        )
        for amount in (1.5, True, "125"):
            response = client.post(
                "/api/user/billing/recharge",
                json={"amount_cents": amount, "channel": "alipay"},
                headers=ORIGIN,
            )
            assert response.status_code == 422, response.text


def test_payment_order_create_checkout_and_settlement_rollback_on_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing = BillingStore(tmp_path)

    def fail_save() -> None:
        raise TypeError("simulated payment ledger serialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.create_order("usr_order_rollback", 125, "alipay")
    assert billing.orders_for_user("usr_order_rollback") == []
    assert BillingStore(tmp_path).orders_for_user("usr_order_rollback") == []

    order = billing.create_order("usr_order_rollback", 125, "alipay")
    original = dict(order)
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.update_order_checkout(
                order["order_id"], pay_url="https://pay.invalid/phantom",
                qr_payload="phantom-payload",
            )
    assert billing.order_by_trade_no(order["merchant_trade_no"]) == original
    assert BillingStore(tmp_path).order_by_trade_no(order["merchant_trade_no"]) == original

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(TypeError, match="serialization failure"):
            billing.settle_order(order["order_id"], "provider-trade-rollback")
    assert billing.balance_cents("usr_order_rollback") == 0
    assert billing.order_by_trade_no(order["merchant_trade_no"]) == original
    reloaded = BillingStore(tmp_path)
    assert reloaded.balance_cents("usr_order_rollback") == 0
    assert reloaded.order_by_trade_no(order["merchant_trade_no"]) == original

    settled = billing.settle_order(order["order_id"], "provider-trade-rollback")
    assert settled["status"] == "paid"
    assert billing.balance_cents("usr_order_rollback") == 125
    durable = BillingStore(tmp_path)
    assert durable.balance_cents("usr_order_rollback") == 125
    assert durable.order_by_trade_no(order["merchant_trade_no"])["status"] == "paid"



@pytest.mark.parametrize("invalid_payload", [
    b'{"wallets":',
    b'{"version":1,"wallets":{"usr_existing":"not-integer"},"charges":{}}',
])
def test_wallet_ledger_corruption_fails_closed_without_overwriting_source(
    tmp_path: Path, invalid_payload: bytes,
) -> None:
    # Billing JSON is authoritative user money, not a recoverable cache.
    # A startup parser failure must never silently reset balances and allow
    # a subsequent recharge to erase an unreadable production ledger.
    ledger_path = tmp_path / "user_billing.json"
    ledger_path.write_bytes(invalid_payload)
    with pytest.raises(RuntimeError, match="Billing ledger is unreadable"):
        BillingStore(tmp_path)
    assert ledger_path.read_bytes() == invalid_payload
