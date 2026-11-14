from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_payment_safety_uses_canonical_data_secret_and_blocks_txid_reuse() -> None:
    source = (ROOT / "app" / "payment_channels_v107_safety_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "load_or_create_data_secret" in source
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

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(confirm, [first["order_id"], second["order_id"]]))

    assert sorted(statuses) == [200, 409]
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
