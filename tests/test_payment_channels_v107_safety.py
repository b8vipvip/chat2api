from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_payment_safety_keeps_v106_cipher_stable_until_data_keyring_migration() -> None:
    source = (ROOT / "app" / "payment_channels_v107_safety_patch.py").read_text(encoding="utf-8")
    legacy = (ROOT / "app" / "payment_channels_v106_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "load_or_create_data_secret" not in source
    assert "channels.cipher =" not in source
    assert "_payment_channels_secret_candidate" in legacy
    assert "_validate_payment_channel_ciphertexts(channels)" in legacy
    assert "_persist_payment_channels_secret(data_dir, data_secret)" in legacy
    assert "禁止重复入账" in source
    assert 'item.get("status") == "paid"' in source
    assert "/console?payment=success#billing" in source
    assert "install_payment_channels_v107_safety_patch(app)" in entry
    assert entry.index("install_payment_channels_v106_patch(app)") < entry.index("install_payment_channels_v107_safety_patch(app)")
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")



def test_usdt_duplicate_txid_confirmation_is_atomic_under_concurrency(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch
    from app.payment_channels_v107_safety_patch import install_payment_channels_v107_safety_patch

    master_key = "v107-usdt-concurrent-master"
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY=master_key,
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    install_payment_channels_v107_safety_patch(app)
    billing = app.state.user_billing
    txid = "a" * 64
    first = billing.create_order("usr_usdt_race", 125, "usdt")
    second = billing.create_order("usr_usdt_race", 125, "usdt")
    with billing.lock:
        for order in (first, second):
            row = billing.orders[order["order_id"]]
            row["status"] = "reviewing"
            row["txid"] = txid
        billing._save()

    def confirm(order_id: str) -> int:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/admin/payment-channels-v106/usdt-orders/{order_id}/confirm",
                headers={"x-api-key": master_key},
            )
            return response.status_code

    # Exercise the authoritative store boundary with both contenders released
    # from a barrier at the same time; this deterministically covers the race
    # that a middleware-only pre-check cannot serialize.
    from threading import Barrier
    from app.user_commerce import WalletReservationError

    barrier = Barrier(2)

    def settle(order_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            billing.settle_usdt_order(order_id, txid)
            return "paid"
        except WalletReservationError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(settle, [first["order_id"], second["order_id"]]))

    assert sorted(outcomes) == ["duplicate", "paid"]
    paid = [
        row for row in billing.orders.values()
        if row.get("channel") == "usdt" and row.get("status") == "paid"
    ]
    assert len(paid) == 1
    assert paid[0]["txid"] == txid
    assert billing.balance_cents("usr_usdt_race") == 125

    # The HTTP path uses the same atomic owner and a paid-order retry is idempotent.
    assert confirm(paid[0]["order_id"]) == 200
    assert billing.balance_cents("usr_usdt_race") == 125
    paid = [
        row for row in billing.orders.values()
        if row.get("channel") == "usdt" and row.get("status") == "paid"
    ]
    assert len(paid) == 1
    assert paid[0]["txid"] == txid
    assert billing.balance_cents("usr_usdt_race") == 125

    # A retry for the winning order remains idempotent and cannot credit again.
    assert confirm(paid[0]["order_id"]) == 200
    assert billing.balance_cents("usr_usdt_race") == 125


def test_generic_provider_trade_number_is_unique_and_same_order_retry_is_idempotent(tmp_path: Path) -> None:
    import pytest

    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch
    from app.user_commerce import WalletReservationError

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v107-provider-namespace-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    billing = app.state.user_billing
    alipay = billing.create_order("usr_zpay_first", 125, "alipay")
    wechat = billing.create_order("usr_zpay_second", 250, "wechat")
    paypal = billing.create_order("usr_paypal", 300, "paypal")

    paid = billing.settle_order(alipay["order_id"], "zpay-trade-shared")
    assert billing.settle_order(alipay["order_id"], "zpay-trade-shared") == paid
    assert billing.balance_cents("usr_zpay_first") == 125

    # Alipay and WeChat share one ZPAY merchant transaction namespace.
    with pytest.raises(WalletReservationError, match="already credited"):
        billing.settle_order(wechat["order_id"], "zpay-trade-shared")
    assert billing.orders[wechat["order_id"]]["status"] == "pending"
    assert billing.balance_cents("usr_zpay_second") == 0

    # The same opaque string is allowed under a different provider namespace.
    paypal_paid = billing.settle_order(paypal["order_id"], "zpay-trade-shared")
    assert paypal_paid["status"] == "paid"
    assert billing.balance_cents("usr_paypal") == 300

    with pytest.raises(WalletReservationError, match="another provider transaction"):
        billing.settle_order(alipay["order_id"], "different-trade-number")
    assert billing.balance_cents("usr_zpay_first") == 125


def test_generic_provider_trade_number_duplicate_race_is_atomic(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import pytest

    from app.user_commerce import BillingStore, WalletReservationError

    billing = BillingStore(tmp_path)
    first = billing.create_order("usr_zpay_race_a", 125, "alipay")
    second = billing.create_order("usr_zpay_race_b", 250, "wechat")
    barrier = Barrier(2)

    def settle(order_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            billing.settle_order(order_id, "zpay-trade-race")
            return "paid"
        except WalletReservationError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(settle, [first["order_id"], second["order_id"]]))

    assert sorted(outcomes) == ["duplicate", "paid"]
    paid = [row for row in billing.orders.values() if row.get("status") == "paid"]
    assert len(paid) == 1
    assert sum(billing.balance_cents(user_id) for user_id in (
        "usr_zpay_race_a", "usr_zpay_race_b",
    )) == int(paid[0]["amount_cents"])


@pytest.mark.parametrize("terminal_status", ["rejected", "failed", "cancelled", "expired"])
def test_terminal_unpaid_order_cannot_be_credited_by_a_late_provider_callback(
    tmp_path: Path, terminal_status: str,
) -> None:
    import pytest

    from app.user_commerce import BillingStore, WalletReservationError

    billing = BillingStore(tmp_path)
    order = billing.create_order("usr_late_callback", 125, "alipay")
    with billing.lock:
        billing.orders[order["order_id"]]["status"] = terminal_status
        billing._save()

    with pytest.raises(WalletReservationError, match="not eligible"):
        billing.settle_order(order["order_id"], "late-provider-trade")
    assert billing.orders[order["order_id"]]["status"] == terminal_status
    assert billing.balance_cents("usr_late_callback") == 0


def test_billing_ledger_rejects_duplicate_paid_provider_transactions(tmp_path: Path) -> None:
    import pytest

    from app.user_commerce import BillingStore

    billing = BillingStore(tmp_path)
    first = billing.create_order("usr_duplicate_ledger_a", 125, "alipay")
    second = billing.create_order("usr_duplicate_ledger_b", 250, "wechat")
    billing.settle_order(first["order_id"], "zpay-trade-unique-a")
    billing.settle_order(second["order_id"], "zpay-trade-unique-b")
    with billing.lock:
        billing.orders[second["order_id"]]["provider_trade_no"] = "zpay-trade-unique-a"
        billing._save()
    source = billing.path.read_bytes()

    with pytest.raises(RuntimeError, match="Billing ledger is unreadable"):
        BillingStore(tmp_path)
    assert billing.path.read_bytes() == source


def test_usdt_confirmation_rejects_pending_order_even_with_txid(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch
    from app.payment_channels_v107_safety_patch import install_payment_channels_v107_safety_patch

    master_key = "v107-usdt-pending-master"
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY=master_key,
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    install_payment_channels_v107_safety_patch(app)
    billing = app.state.user_billing
    order = billing.create_order("usr_usdt_pending", 125, "usdt")
    with billing.lock:
        billing.orders[order["order_id"]]["txid"] = "b" * 64
        billing._save()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/admin/payment-channels-v106/usdt-orders/{order['order_id']}/confirm",
            headers={"x-api-key": master_key},
        )
    assert response.status_code == 409
    assert billing.orders[order["order_id"]]["status"] == "pending"
    assert billing.balance_cents("usr_usdt_pending") == 0



def test_usdt_paid_order_cannot_be_rejected_or_make_txid_reusable(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch
    from app.payment_channels_v107_safety_patch import install_payment_channels_v107_safety_patch

    master_key = "v107-usdt-paid-reject-master"
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY=master_key,
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    install_payment_channels_v107_safety_patch(app)
    billing = app.state.user_billing
    txid = "c" * 64
    paid_order = billing.create_order("usr_usdt_paid_reject", 125, "usdt")
    duplicate_order = billing.create_order("usr_usdt_paid_reject", 125, "usdt")
    with billing.lock:
        for order in (paid_order, duplicate_order):
            row = billing.orders[order["order_id"]]
            row["status"] = "reviewing"
            row["txid"] = txid
        billing._save()

    with TestClient(app, raise_server_exceptions=False) as client:
        confirmed = client.post(
            f"/api/admin/payment-channels-v106/usdt-orders/{paid_order['order_id']}/confirm",
            headers={"x-api-key": master_key},
        )
        assert confirmed.status_code == 200
        rejected = client.post(
            f"/api/admin/payment-channels-v106/usdt-orders/{paid_order['order_id']}/reject",
            headers={"x-api-key": master_key},
        )
        assert rejected.status_code == 409
        duplicate = client.post(
            f"/api/admin/payment-channels-v106/usdt-orders/{duplicate_order['order_id']}/confirm",
            headers={"x-api-key": master_key},
        )
        assert duplicate.status_code == 409

    assert billing.orders[paid_order["order_id"]]["status"] == "paid"
    assert billing.balance_cents("usr_usdt_paid_reject") == 125
    assert billing.orders[duplicate_order["order_id"]]["status"] == "reviewing"



def test_payment_credentials_remain_decryptable_after_v107_and_admin_key_rotation(tmp_path: Path) -> None:
    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch
    from app.payment_channels_v107_safety_patch import install_payment_channels_v107_safety_patch

    def install(api_key: str):
        app = create_app(Settings(
            CHAT2API_DATA_DIR=str(tmp_path),
            CHAT2API_API_KEY=api_key,
        ))
        install_user_console_v104_patch(app)
        install_payment_channels_v106_patch(app)
        install_payment_channels_v107_safety_patch(app)
        return app

    first = install("initial-admin-key")
    channels = first.state.user_payment_channels_v106
    channels.update({
        "public_origin": "https://payments.example",
        "aggregate": {
            "zpay": {
                "enabled": True,
                "pid": "zpay-pid",
                "alipay_enabled": True,
                "wechat_enabled": True,
                "key": "zpay-private-secret",
            },
        },
        "direct": {
            "paypal": {
                "enabled": True,
                "sandbox": False,
                "client_id": "paypal-client",
                "client_secret": "paypal-private-secret",
            },
            "usdt": {},
        },
    })
    assert channels.zpay_key() == "zpay-private-secret"
    assert channels.paypal_secret() == "paypal-private-secret"
    secret_path = tmp_path / ".chat2api_data_secret"
    assert secret_path.is_file()
    assert secret_path.stat().st_mode & 0o077 == 0

    # A second app install simulates process restart after administrator API
    # key rotation. The payment-specific key remains stable and both secrets
    # still decrypt; V107 must not silently swap the cipher underneath V106.
    second = install("rotated-admin-key")
    restarted = second.state.user_payment_channels_v106
    assert restarted.zpay_key() == "zpay-private-secret"
    assert restarted.paypal_secret() == "paypal-private-secret"


def test_payment_cipher_mismatch_fails_closed_without_rewriting_existing_config(tmp_path: Path) -> None:
    import json
    import pytest

    from app.config import Settings
    from app.main import create_app
    from app.payment_channels_v106_patch import PaymentChannelsStore, install_payment_channels_v106_patch
    from app.user_console_v104_patch import install_user_console_v104_patch

    config = PaymentChannelsStore(tmp_path, "original-secret")
    config.update({
        "aggregate": {"zpay": {"enabled": True, "pid": "pid", "key": "zpay-secret"}},
        "direct": {"paypal": {"enabled": True, "client_id": "client", "client_secret": "paypal-secret"}},
    })
    original = (tmp_path / "user_payment_channels_v106.json").read_bytes()
    # No pinned secret file exists; the current administrator key is not the
    # key that encrypted this legacy config. Startup must fail, not report the
    # credentials as empty and permit an accidental overwrite.
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="different-secret",
    ))
    install_user_console_v104_patch(app)
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        install_payment_channels_v106_patch(app)
    assert (tmp_path / "user_payment_channels_v106.json").read_bytes() == original
    assert not (tmp_path / ".chat2api_data_secret").exists()

def test_merchant_trade_numbers_are_unique_across_all_payment_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.user_commerce as commerce
    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v107-merchant-trade-unique-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    billing = app.state.user_billing

    # Force the same millisecond and a random collision across the two
    # legacy (ZPAY) and direct-provider creation paths. Allocation must retry
    # while holding the BillingStore lock instead of persisting an ambiguous
    # callback key.
    candidates = iter([1, 1, 2, 2, 3, 3, 4])
    monkeypatch.setattr(commerce.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(commerce.secrets, "randbelow", lambda _upper: next(candidates))

    orders = [
        billing.create_order("usr_trade_alipay", 125, "alipay"),
        billing.create_order("usr_trade_wechat", 125, "wechat"),
        billing.create_order("usr_trade_paypal", 125, "paypal"),
        billing.create_order("usr_trade_usdt", 125, "usdt"),
    ]
    trade_numbers = [row["merchant_trade_no"] for row in orders]
    assert len(set(trade_numbers)) == len(orders)
    for order in orders:
        matched = billing.order_by_trade_no(order["merchant_trade_no"])
        assert matched is not None
        assert matched["order_id"] == order["order_id"]


def test_billing_ledger_rejects_duplicate_merchant_trade_numbers_without_overwrite(
    tmp_path: Path,
) -> None:
    from app.user_commerce import BillingStore, WalletReservationError

    billing = BillingStore(tmp_path)
    first = billing.create_order("usr_trade_duplicate_a", 125, "alipay")
    second = billing.create_order("usr_trade_duplicate_b", 250, "wechat")
    with billing.lock:
        billing.orders[second["order_id"]]["merchant_trade_no"] = first["merchant_trade_no"]
        billing._save()
    source = billing.path.read_bytes()

    with pytest.raises(WalletReservationError, match="ambiguous"):
        billing.order_by_trade_no(first["merchant_trade_no"])
    with pytest.raises(RuntimeError, match="Billing ledger is unreadable"):
        BillingStore(tmp_path)
    assert billing.path.read_bytes() == source

@pytest.mark.parametrize("terminal_status", ["rejected", "failed", "cancelled", "expired"])
def test_usdt_txid_submission_cannot_reopen_terminal_orders(
    tmp_path: Path, terminal_status: str,
) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_commerce import USER_SESSION_COOKIE
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v107-usdt-terminal-submit-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    accounts = app.state.user_accounts
    billing = app.state.user_billing
    user = accounts.register(
        f"usdt-terminal-{terminal_status}@example.com",
        "valid-password-123",
    )
    user_id = str(user["user_id"])
    order = billing.create_order(user_id, 125, "usdt")
    with billing.lock:
        billing.orders[order["order_id"]]["status"] = terminal_status
        billing._save()

    session = accounts.create_session(user_id)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(USER_SESSION_COOKIE, session)
        response = client.post(
            f"/api/user/payments/v106/usdt/{order['order_id']}/txid",
            json={"txid": "d" * 64},
        )

    assert response.status_code == 409, response.text
    assert billing.orders[order["order_id"]]["status"] == terminal_status
    assert billing.orders[order["order_id"]]["txid"] == ""
    assert billing.balance_cents(user_id) == 0


@pytest.mark.parametrize("initial_status", ["pending", "reviewing"])
def test_usdt_txid_submission_keeps_open_orders_reviewable(
    tmp_path: Path, initial_status: str,
) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_commerce import USER_SESSION_COOKIE
    from app.user_console_v104_patch import install_user_console_v104_patch
    from app.payment_channels_v106_patch import install_payment_channels_v106_patch

    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_API_KEY="v107-usdt-open-submit-master",
    ))
    install_user_console_v104_patch(app)
    install_payment_channels_v106_patch(app)
    accounts = app.state.user_accounts
    billing = app.state.user_billing
    user = accounts.register(
        f"usdt-open-{initial_status}@example.com",
        "valid-password-123",
    )
    user_id = str(user["user_id"])
    order = billing.create_order(user_id, 125, "usdt")
    with billing.lock:
        row = billing.orders[order["order_id"]]
        row["status"] = initial_status
        if initial_status == "reviewing":
            row["txid"] = "e" * 64
        billing._save()

    session = accounts.create_session(user_id)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(USER_SESSION_COOKIE, session)
        response = client.post(
            f"/api/user/payments/v106/usdt/{order['order_id']}/txid",
            json={"txid": "d" * 64},
        )

    assert response.status_code == 200, response.text
    assert response.json()["order"]["status"] == "reviewing"
    assert response.json()["order"]["txid"] == "d" * 64

