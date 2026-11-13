from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload
from app.user_commerce import (
    BillingStore, InsufficientWalletBalance, PaymentConfigStore, PricingStore,
    UserAccountStore, WalletReservationError,
)
from app.user_console_v104_patch import _verify_zpay, _zpay_sign


ROOT = Path(__file__).resolve().parents[1]


def test_user_account_ownership_and_session_are_isolated(tmp_path: Path) -> None:
    store = UserAccountStore(tmp_path)
    alice = store.register("alice@example.com", "correct-horse-battery", "Alice")
    bob = store.register("bob@example.com", "different-secret-123", "Bob")

    store.attach_key(alice["user_id"], "key_alice")
    store.attach_key(bob["user_id"], "key_bob")
    assert store.owns_key(alice["user_id"], "key_alice") is True
    assert store.owns_key(alice["user_id"], "key_bob") is False
    assert store.owner_for_key("key_alice") == alice["user_id"]

    assert store.verify("alice@example.com", "wrong-password") is None
    assert store.verify("alice@example.com", "correct-horse-battery")["user_id"] == alice["user_id"]
    token = store.create_session(alice["user_id"])
    assert store.authenticate(token)["user_id"] == alice["user_id"]
    store.revoke_session(token)
    assert store.authenticate(token) is None


def test_admin_user_projection_and_disable_revoke_all_sessions(tmp_path: Path) -> None:
    store = UserAccountStore(tmp_path)
    user = store.register("admin-view@example.com", "long-enough-password", "Admin View")
    user_id = user["user_id"]
    store.attach_key(user_id, "key_owned_by_user")
    first = store.create_session(user_id)
    second = store.create_session(user_id)

    rows = store.admin_list()
    assert len(rows) == 1
    assert rows[0]["user_id"] == user_id
    assert rows[0]["key_count"] == 1
    assert rows[0]["active_sessions"] == 2
    assert "password_hash" not in rows[0]
    assert "password_salt" not in rows[0]
    assert first not in str(rows)
    assert second not in str(rows)

    disabled, revoked = store.set_enabled(user_id, False)
    assert disabled["enabled"] is False
    assert revoked == 2
    assert store.is_enabled(user_id) is False
    assert store.authenticate(first) is None
    assert store.authenticate(second) is None
    assert store.admin_list()[0]["active_sessions"] == 0

    enabled, revoked_on_enable = store.set_enabled(user_id, True)
    assert enabled["enabled"] is True
    assert revoked_on_enable == 0
    assert store.authenticate(first) is None
    assert store.authenticate(second) is None


def test_admin_user_session_revoke_is_idempotent_and_scoped(tmp_path: Path) -> None:
    store = UserAccountStore(tmp_path)
    alice = store.register("alice-sessions@example.com", "long-enough-password")
    bob = store.register("bob-sessions@example.com", "different-password")
    alice_session = store.create_session(alice["user_id"])
    bob_session = store.create_session(bob["user_id"])

    assert store.revoke_user_sessions(alice["user_id"]) == 1
    assert store.revoke_user_sessions(alice["user_id"]) == 0
    assert store.authenticate(alice_session) is None
    assert store.authenticate(bob_session)["user_id"] == bob["user_id"]


def test_default_model_prices_follow_current_reference_rate_card(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    assert pricing.model("gpt-5.6-sol") == {
        "model_id": "gpt-5.6-sol",
        "name": "GPT-5.6 Sol",
        "enabled": True,
        "input_usd_per_million": 4.0,
        "cached_input_usd_per_million": 0.4,
        "output_usd_per_million": 20.0,
    }
    assert pricing.model("gpt-5.6-terra")["output_usd_per_million"] == 12.0
    assert pricing.model("gpt-5.6-luna")["input_usd_per_million"] == 0.2
    assert pricing.model("gpt-5.6")["output_usd_per_million"] == 20.0


def test_billing_records_request_once_and_uses_price_snapshot(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    payload = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=payload["models"])
    billing = BillingStore(tmp_path)
    billing.credit("usr_test", 1000)
    request = {
        "request_id": "req_1",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        "diagnostics": {},
    }
    row = billing.record_charge("usr_test", request, pricing)
    assert row is not None
    assert row["cost_usd_micros"] == 14_000
    assert row["cost_cny_cents"] == 10
    assert row["debited"] is True
    assert billing.balance_cents("usr_test") == 990

    again = billing.record_charge("usr_test", request, pricing)
    assert again == row
    assert billing.balance_cents("usr_test") == 990


def test_payment_secret_is_encrypted_and_never_returned_publicly(tmp_path: Path) -> None:
    store = PaymentConfigStore(tmp_path, "server-local-data-secret")
    public = store.update({
        "enabled": True,
        "pid": "1234567890",
        "key": "merchant-super-secret",
        "alipay_enabled": True,
        "wechat_enabled": True,
        "alipay_cid": "1,2",
        "wechat_cid": "3",
        "public_origin": "https://api.example.com",
    })
    assert public["configured"] is True
    assert public["key_configured"] is True
    assert "merchant-super-secret" not in str(public)
    assert "merchant-super-secret" not in (tmp_path / "user_payment_config.json").read_text(encoding="utf-8")
    assert store.secret_key() == "merchant-super-secret"


def test_zpay_signature_matches_provider_canonical_contract() -> None:
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": "202609070001",
        "notify_url": "https://example.com/notify",
        "name": "chat2api account recharge",
        "money": "0.01",
        "param": "pay_demo",
        "sign_type": "MD5",
    }
    key = "merchant-key"
    canonical = "&".join(f"{name}={params[name]}" for name in sorted(params) if name not in {"sign", "sign_type"})
    expected = hashlib.md5((canonical + key).encode("utf-8")).hexdigest()  # noqa: S324 - provider contract
    signature = _zpay_sign(params, key)
    assert signature == expected
    assert _verify_zpay({**params, "sign": signature}, key) is True
    assert _verify_zpay({**params, "money": "1.00", "sign": signature}, key) is False


def test_user_console_contains_requested_modules_without_sensitive_runtime_disclosure() -> None:
    html = (ROOT / "app" / "user_console_v104.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "user_console_v104.js").read_text(encoding="utf-8")
    for label in ("API 密钥", "请求记录", "账户资料", "费用中心", "模型库", "测试场", "数据看板", "开发文档"):
        assert label in html
    public_surface = html + "\n" + js
    # The model-library page may explain that availability comes from Workers,
    # but transport/host identity details must remain private.
    for forbidden in ("Chrome", "extension", "Linux", "client_id", "pairing", "Window Management"):
        assert forbidden not in public_surface


def test_user_api_privacy_boundary_does_not_relay_runtime_failure_text() -> None:
    source = (ROOT / "app" / "user_console_privacy_v105_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'path == "/api/user/playground"' in source
    assert "测试请求失败，请稍后重试或查看请求记录" in source
    assert "response.status_code >= 500" in source
    assert "install_user_console_privacy_v105_patch(app)" in entry
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")


def test_admin_console_extension_adds_user_price_payment_and_audit_navigation() -> None:
    js = (ROOT / "app" / "admin_user_commerce_v104.js").read_text(encoding="utf-8")
    assert "用户管理" in js
    assert "价格配置" in js
    assert "支付配置" in js
    assert "/api/admin/users" in js
    assert "/api/admin/audit" in js
    assert "/api/admin/user-pricing" in js
    assert "/api/admin/user-payments" in js
    assert "测试 API 连接" in js
    assert "不显示或恢复完整密钥" in js
    node = shutil.which("node")
    if node:
        checked = subprocess.run(
            [node, "--check", str(ROOT / "app" / "admin_user_commerce_v104.js")],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_user_commerce_runtime_and_production_dependencies_are_published() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.291"
    runtime_source = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"user_console_v104": True' in runtime_source
    assert '"user_pricing_billing_v104": True' in runtime_source
    assert '"user_payment_zpay_v104": True' in runtime_source
    assert '"user_payment_channels_v106": True' in runtime_source
    assert '"user_payment_paypal_v106": True' in runtime_source
    assert '"user_payment_usdt_trc20_v106": True' in runtime_source
    assert '"user_payment_settlement_safety_v107": True' in runtime_source
    assert "python-multipart" in requirements
    assert "python-multipart" in project



def test_wallet_reservations_prevent_overcommit_and_release_idempotently(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    price = pricing.model("gpt-5.6-sol")
    billing = BillingStore(tmp_path)
    billing.credit("usr_wallet", 1000)

    reserved = billing.reserve_for_request(
        "usr_wallet", "req_wallet_hold",
        model_id="gpt-5.6-sol", estimated_cny_cents=600,
        input_tokens=100, output_tokens=4096,
        price_snapshot=price, usd_cny_rate=7.0,
    )
    assert reserved is not None and reserved["state"] == "reserved"
    assert billing.balance_cents("usr_wallet") == 1000
    assert billing.reserved_cents("usr_wallet") == 600
    assert billing.available_balance_cents("usr_wallet") == 400
    with pytest.raises(WalletReservationError):
        billing.reserve_for_request(
            "usr_wallet", "req_wallet_hold",
            model_id="gpt-5.6-sol", estimated_cny_cents=599,
            input_tokens=100, output_tokens=4096,
            price_snapshot=price, usd_cny_rate=7.0,
        )

    with pytest.raises(InsufficientWalletBalance):
        billing.reserve_for_request(
            "usr_wallet", "req_wallet_overcommit",
            model_id="gpt-5.6-sol", estimated_cny_cents=500,
            input_tokens=100, output_tokens=4096,
            price_snapshot=price, usd_cny_rate=7.0,
        )

    assert billing.release_reservation("req_wallet_hold", "usr_wallet", "worker_failed") is True
    assert billing.release_reservation("req_wallet_hold", "usr_wallet", "worker_failed") is False
    assert billing.reserved_cents("usr_wallet") == 0
    assert billing.available_balance_cents("usr_wallet") == 1000
    # A terminal release must not be a reusable dispatch admission.
    with pytest.raises(WalletReservationError, match="terminal wallet reservation"):
        billing.reserve_for_request(
            "usr_wallet", "req_wallet_hold",
            model_id="gpt-5.6-sol", estimated_cny_cents=600,
            input_tokens=100, output_tokens=4096,
            price_snapshot=price, usd_cny_rate=7.0,
        )


def test_wallet_reservation_settles_actual_charge_once_and_persists(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    payload = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=payload["models"])
    price = pricing.model("gpt-5.6-sol")
    billing = BillingStore(tmp_path)
    billing.credit("usr_wallet", 1000)
    billing.reserve_for_request(
        "usr_wallet", "req_wallet_settle",
        model_id="gpt-5.6-sol", estimated_cny_cents=600,
        input_tokens=1000, output_tokens=4096,
        price_snapshot=price, usd_cny_rate=7.0,
    )
    changed_models = [
        {**row, "output_usd_per_million": 999.0}
        if row["model_id"] == "gpt-5.6-sol" else row
        for row in payload["models"]
    ]
    pricing.update(billing_enabled=True, usd_cny_rate=9.0, models=changed_models)
    request = {
        "request_id": "req_wallet_settle",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "estimated": True,
        },
        "diagnostics": {},
    }
    charge = billing.record_charge("usr_wallet", request, pricing)
    assert charge is not None
    assert charge["cost_cny_cents"] == 10
    assert charge["usage_is_estimated"] is True
    reservation = billing.reservations["req_wallet_settle"]
    assert reservation["state"] == "settled"
    assert reservation["actual_cny_cents"] == 10
    assert billing.balance_cents("usr_wallet") == 990
    assert billing.reserved_cents("usr_wallet") == 0

    again = billing.record_charge("usr_wallet", request, pricing)
    assert again == charge
    assert billing.balance_cents("usr_wallet") == 990
    reopened = BillingStore(tmp_path)
    assert reopened.reservations["req_wallet_settle"]["state"] == "settled"
    assert reopened.charge_for_request("req_wallet_settle") == charge
    # A previously charged ID may be read idempotently, but never used to
    # dispatch a second billable job without a fresh reservation.
    with pytest.raises(WalletReservationError, match="already charged"):
        reopened.reserve_for_request(
            "usr_wallet", "req_wallet_settle",
            model_id="gpt-5.6-sol", estimated_cny_cents=600,
            input_tokens=1000, output_tokens=4096,
            price_snapshot=price, usd_cny_rate=7.0,
        )


def test_wallet_reservations_are_serialized_under_concurrent_admission(tmp_path: Path) -> None:
    billing = BillingStore(tmp_path)
    billing.credit("usr_wallet", 1000)
    barrier = Barrier(2)

    def reserve(request_id: str) -> str:
        barrier.wait(timeout=3)
        try:
            billing.reserve_for_request(
                "usr_wallet", request_id,
                model_id="test-model", estimated_cny_cents=600,
                input_tokens=1, output_tokens=1,
                price_snapshot={"model_id": "test-model"}, usd_cny_rate=7.0,
            )
            return "reserved"
        except InsufficientWalletBalance:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ("req_wallet_a", "req_wallet_b")))

    assert sorted(outcomes) == ["rejected", "reserved"]
    assert billing.reserved_cents("usr_wallet") == 600
    assert billing.available_balance_cents("usr_wallet") == 400


def test_wallet_reservation_and_charge_are_user_scoped(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    billing = BillingStore(tmp_path)
    billing.credit("usr_alice", 1000)
    billing.reserve_for_request(
        "usr_alice", "req_wallet_owner",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=1, output_tokens=1,
        price_snapshot=pricing.model("gpt-5.6-sol"), usd_cny_rate=7.0,
    )

    with pytest.raises(WalletReservationError):
        billing.record_charge(
            "usr_bob",
            {
                "request_id": "req_wallet_owner",
                "status": "completed",
                "requested_model": "gpt-5.6-sol",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            pricing,
        )
    assert billing.balance_cents("usr_alice") == 1000
    assert billing.balance_cents("usr_bob") == 0
    assert billing.reserved_cents("usr_alice") == 100


def test_wallet_reservation_rejects_user_request_before_worker_dispatch(tmp_path: Path) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-user@example.com", "long-enough-password")
    public, token = asyncio.run(app.state.api_keys.create("wallet-user"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    app.state.user_billing.credit(user["user_id"], 1)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)
    app.state.registry.resolve_client = lambda requested: "ext_wallet_test"
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Wallet admission test"}],
                "max_tokens": 64,
            },
        )

    assert response.status_code == 402, response.text
    assert sent == []
    assert app.state.user_billing.balance_cents(user["user_id"]) == 1
    assert app.state.user_billing.reserved_cents(user["user_id"]) == 0
    rows = app.state.quota_reservations.list_reservations(key_id=public["key_id"])
    assert rows and all(row["state"] == "released" for row in rows)
    app.state.quota_reservations.close()



def test_wallet_admission_is_reserved_before_chat_worker_and_settled_after_success(tmp_path: Path) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-route-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-route@example.com", "long-enough-password")
    public, token = asyncio.run(app.state.api_keys.create("wallet-route"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    app.state.user_billing.credit(user["user_id"], 1000)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)
    app.state.registry.resolve_client = lambda requested: "ext_wallet_route"

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "chat.request":
            hold = app.state.user_billing.reservations.get(payload["request_id"])
            assert hold is not None and hold["state"] == "reserved"
            assert hold["reserved_cny_cents"] > 0
            await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed",
                "text": "Wallet reservation completed successfully",
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Wallet success path"}],
                "max_tokens": 64,
            },
        )

    assert response.status_code == 200, response.text
    record = next(
        row for row in app.state.telemetry.recent(20)
        if row.get("api_key_id") == public["key_id"]
        and row.get("request_type") == "text"
    )
    charge = app.state.user_billing.charge_for_request(record["request_id"])
    reservation = app.state.user_billing.reservations[record["request_id"]]
    assert charge is not None
    assert charge["usage_is_estimated"] is True
    assert reservation["state"] == "settled"
    assert reservation["actual_cny_cents"] == charge["cost_cny_cents"]
    assert app.state.user_billing.reserved_cents(user["user_id"]) == 0
    assert app.state.user_billing.balance_cents(user["user_id"]) == 1000 - charge["cost_cny_cents"]
    app.state.quota_reservations.close()


def test_wallet_telemetry_settles_success_and_releases_failed_request(tmp_path: Path) -> None:
    import asyncio

    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-hook-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-hook@example.com", "long-enough-password")
    public, _token = asyncio.run(app.state.api_keys.create("wallet-hook"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = app.state.user_billing
    billing.credit(user["user_id"], 1000)
    price = pricing.model("gpt-5.6-sol")
    billing.reserve_for_request(
        user["user_id"], "req_wallet_hook_success",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=100, output_tokens=4096,
        price_snapshot=price, usd_cny_rate=7.0,
    )
    billing.reserve_for_request(
        user["user_id"], "req_wallet_hook_failure",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=100, output_tokens=4096,
        price_snapshot=price, usd_cny_rate=7.0,
    )
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    async def append_rows() -> None:
        await app.state.telemetry.append({
            "request_id": "req_wallet_hook_success",
            "api_key_id": public["key_id"],
            "requested_model": "gpt-5.6-sol",
            "status": "completed",
            "usage": {
                "prompt_tokens": 1000, "completion_tokens": 500,
                "total_tokens": 1500, "estimated": True,
            },
            "diagnostics": {},
        })
        await app.state.telemetry.append({
            "request_id": "req_wallet_hook_failure",
            "api_key_id": public["key_id"],
            "requested_model": "gpt-5.6-sol",
            "status": "error",
            "usage": {},
            "diagnostics": {},
        })

    asyncio.run(append_rows())
    success_charge = billing.charge_for_request("req_wallet_hook_success")
    assert success_charge is not None and success_charge["debited"] is True
    assert billing.reservations["req_wallet_hook_success"]["state"] == "settled"
    assert billing.reservations["req_wallet_hook_success"]["actual_cny_cents"] == success_charge["cost_cny_cents"]
    assert billing.reservations["req_wallet_hook_failure"]["state"] == "released"
    assert billing.reserved_cents(user["user_id"]) == 0
    assert billing.balance_cents(user["user_id"]) == 1000 - success_charge["cost_cny_cents"]
    app.state.quota_reservations.close()



def test_wallet_actual_overrun_posts_debt_and_blocks_new_reservations(tmp_path: Path) -> None:
    pricing = PricingStore(tmp_path)
    payload = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=payload["models"])
    price = pricing.model("gpt-5.6-sol")
    billing = BillingStore(tmp_path)
    billing.credit("usr_wallet", 100)
    billing.reserve_for_request(
        "usr_wallet", "req_wallet_overrun",
        model_id="gpt-5.6-sol", estimated_cny_cents=50,
        input_tokens=100, output_tokens=4096,
        price_snapshot=price, usd_cny_rate=7.0,
    )
    charge = billing.record_charge(
        "usr_wallet",
        {
            "request_id": "req_wallet_overrun",
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "usage": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "estimated": True,
            },
            "diagnostics": {},
        },
        pricing,
    )
    assert charge is not None and charge["cost_cny_cents"] > 100
    assert billing.balance_cents("usr_wallet") < 0
    assert billing.available_balance_cents("usr_wallet") < 0
    with pytest.raises(InsufficientWalletBalance):
        billing.reserve_for_request(
            "usr_wallet", "req_wallet_after_debt",
            model_id="gpt-5.6-sol", estimated_cny_cents=1,
            input_tokens=1, output_tokens=1,
            price_snapshot=price, usd_cny_rate=7.0,
        )
