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


@pytest.mark.parametrize("bad_rate", [float("nan"), float("inf"), True, -1.0])
def test_pricing_rejects_non_finite_or_non_numeric_exchange_rates(
    tmp_path: Path, bad_rate: float,
) -> None:
    pricing = PricingStore(tmp_path)
    with pytest.raises(ValueError):
        pricing.update(
            billing_enabled=True,
            usd_cny_rate=bad_rate,
            models=pricing.public()["models"],
        )
    assert pricing.public()["billing_enabled"] is False


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), True, False, -1.0])
def test_pricing_rejects_non_finite_or_invalid_model_rates(
    tmp_path: Path, bad_price: float,
) -> None:
    pricing = PricingStore(tmp_path)
    models = pricing.public()["models"]
    models[0]["input_usd_per_million"] = bad_price
    with pytest.raises(ValueError):
        pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=models)
    assert pricing.public()["billing_enabled"] is False


def test_pricing_config_load_fails_closed_on_non_finite_saved_rates(tmp_path: Path) -> None:
    path = tmp_path / "user_pricing.json"
    source = b'{"version":1,"billing_enabled":true,"usd_cny_rate":NaN,"models":[]}'
    path.write_bytes(source)
    with pytest.raises(RuntimeError, match="Pricing configuration is unreadable"):
        PricingStore(tmp_path)
    assert path.read_bytes() == source


def test_pricing_update_rolls_back_memory_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = PricingStore(tmp_path)
    previous = pricing.public()
    models = [dict(row) for row in previous["models"]]
    models[0]["input_usd_per_million"] = 9.0

    def fail_save() -> None:
        raise OSError("simulated pricing config write failure")

    with monkeypatch.context() as patch:
        patch.setattr(pricing, "_save", fail_save)
        with pytest.raises(OSError, match="write failure"):
            pricing.update(billing_enabled=True, usd_cny_rate=8.0, models=models)
    assert pricing.public() == previous


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



def test_wallet_reservation_storage_failures_preserve_available_balance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = PricingStore(tmp_path)
    billing = BillingStore(tmp_path)
    billing.credit("usr_durable", 1000)
    kwargs = {
        "model_id": "gpt-5.6-sol",
        "estimated_cny_cents": 300,
        "input_tokens": 100,
        "output_tokens": 4096,
        "price_snapshot": pricing.model("gpt-5.6-sol"),
        "usd_cny_rate": 7.0,
    }

    def fail_save() -> None:
        raise OSError("simulated full data volume")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="full data volume"):
            billing.reserve_for_request("usr_durable", "req_failed_save", **kwargs)
    assert "req_failed_save" not in billing.reservations
    assert billing.available_balance_cents("usr_durable") == 1000
    assert "req_failed_save" not in BillingStore(tmp_path).reservations

    assert billing.reserve_for_request(
        "usr_durable", "req_release_failure", **kwargs,
    )["state"] == "reserved"
    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="full data volume"):
            billing.release_reservation("req_release_failure", "usr_durable")
    assert billing.reservations["req_release_failure"]["state"] == "reserved"
    assert billing.available_balance_cents("usr_durable") == 700
    assert BillingStore(tmp_path).reservations["req_release_failure"]["state"] == "reserved"


def test_wallet_failed_terminal_ledger_write_never_retains_phantom_debit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = PricingStore(tmp_path)
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = BillingStore(tmp_path)
    billing.credit("usr_durable", 1000)
    billing.reserve_for_request(
        "usr_durable", "req_durable_charge",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=100, output_tokens=4096,
        price_snapshot=pricing.model("gpt-5.6-sol"), usd_cny_rate=7.0,
    )
    request = {
        "request_id": "req_durable_charge",
        "status": "completed",
        "requested_model": "gpt-5.6-sol",
        "usage": {
            "prompt_tokens": 1000, "completion_tokens": 500,
            "total_tokens": 1500, "estimated": True,
        },
    }

    def fail_save() -> None:
        raise OSError("simulated write interruption")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "_save", fail_save)
        with pytest.raises(OSError, match="write interruption"):
            billing.record_charge("usr_durable", request, pricing)
    assert billing.balance_cents("usr_durable") == 1000
    assert billing.reservations["req_durable_charge"]["state"] == "reserved"
    assert billing.charge_for_request("req_durable_charge") is None
    reopened = BillingStore(tmp_path)
    assert reopened.balance_cents("usr_durable") == 1000
    assert reopened.reservations["req_durable_charge"]["state"] == "reserved"
    assert reopened.charge_for_request("req_durable_charge") is None

    charge = billing.record_charge("usr_durable", request, pricing)
    assert charge is not None and charge["cost_cny_cents"] == 10
    assert billing.balance_cents("usr_durable") == 990
    assert billing.reservations["req_durable_charge"]["state"] == "settled"

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


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 10, "completion_tokens": 5},
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 0},
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": True},
        {"prompt_tokens": 10.5, "completion_tokens": 5, "total_tokens": 15},
        {"prompt_tokens": 10, "completion_tokens": "5", "total_tokens": 15},
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 16},
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cached_input_tokens": 11},
    ],
)
def test_wallet_completed_request_without_valid_usage_keeps_reservation(
    tmp_path: Path, usage: dict[str, object],
) -> None:
    pricing = PricingStore(tmp_path)
    pricing_payload = pricing.public()
    models = [dict(row) for row in pricing_payload["models"]]
    for model_price in models:
        if model_price["model_id"] == "gpt-5.6-sol":
            model_price["input_usd_per_million"] = 1000.0
            model_price["cached_input_usd_per_million"] = 100.0
            model_price["output_usd_per_million"] = 1000.0
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=models)
    billing = BillingStore(tmp_path)
    billing.credit("usr_usage_pending", 1000)
    billing.reserve_for_request(
        "usr_usage_pending", "req_wallet_usage_pending",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=100, output_tokens=4096,
        price_snapshot=pricing.model("gpt-5.6-sol"), usd_cny_rate=7.0,
    )

    result = billing.record_charge(
        "usr_usage_pending",
        {
            "request_id": "req_wallet_usage_pending",
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "usage": usage,
        },
        pricing,
    )

    assert result is None
    assert billing.charge_for_request("req_wallet_usage_pending") is None
    assert billing.reservations["req_wallet_usage_pending"]["state"] == "reserved"
    assert billing.reserved_cents("usr_usage_pending") == 100
    assert billing.balance_cents("usr_usage_pending") == 1000

    # A replay with valid, internally consistent usage settles exactly once.
    result = billing.record_charge(
        "usr_usage_pending",
        {
            "request_id": "req_wallet_usage_pending",
            "status": "completed",
            "requested_model": "gpt-5.6-sol",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        pricing,
    )
    assert result is not None and result["cost_cny_cents"] > 0
    assert billing.reservations["req_wallet_usage_pending"]["state"] == "settled"
    assert billing.reserved_cents("usr_usage_pending") == 0
    assert billing.balance_cents("usr_usage_pending") == 1000 - result["cost_cny_cents"]


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




def test_wallet_enabled_rejects_unpriced_model_and_releases_token_hold(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1, reserve_request_quota
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-unpriced-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-unpriced@example.com", "long-enough-password")
    public, _token = asyncio.run(app.state.api_keys.create("wallet-unpriced"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(
        billing_enabled=True,
        usd_cny_rate=7.0,
        models=[row for row in config["models"] if row["model_id"] not in {"gpt-5.6-sol", "gpt-5.6"}],
    )
    billing = app.state.user_billing
    billing.credit(user["user_id"], 1000)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    with pytest.raises(HTTPException) as error:
        reserve_request_quota(
            app, "req_wallet_unpriced_0001",
            SimpleNamespace(kind="api_key", key_id=public["key_id"]),
            "unpriced model request", 4096, model_id="gpt-5.6-sol",
        )

    assert error.value.status_code == 503
    assert "not configured" in str(error.value.detail)
    assert billing.balance_cents(user["user_id"]) == 1000
    assert billing.reserved_cents(user["user_id"]) == 0
    assert billing.charge_for_request("req_wallet_unpriced_0001") is None
    token_rows = app.state.quota_reservations.list_reservations(key_id=public["key_id"])
    assert len(token_rows) == 1
    assert token_rows[0]["state"] == "released"
    app.state.quota_reservations.close()


@pytest.mark.parametrize("model_id", ["gpt-image", "gpt-live", "gpt-live-mini"])
def test_wallet_billing_rejects_positive_text_rates_for_unmetered_media_models(
    tmp_path: Path, model_id: str,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1, reserve_request_quota
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-media-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register(f"{model_id}@example.com", "long-enough-password")
    public, _token = asyncio.run(app.state.api_keys.create("wallet-media"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    media_price = {
        "model_id": model_id,
        "name": model_id,
        "enabled": True,
        "input_usd_per_million": 1.0,
        "cached_input_usd_per_million": 0.1,
        "output_usd_per_million": 5.0,
    }
    pricing.update(
        billing_enabled=True,
        usd_cny_rate=7.0,
        models=[*config["models"], media_price],
    )
    billing = app.state.user_billing
    billing.credit(user["user_id"], 1000)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    with pytest.raises(HTTPException) as error:
        reserve_request_quota(
            app, f"req_wallet_{model_id.replace('-', '_')}_0001",
            SimpleNamespace(kind="api_key", key_id=public["key_id"]),
            "media request", 4096, model_id=model_id,
        )

    assert error.value.status_code == 503
    assert "not metered" in str(error.value.detail)
    assert billing.balance_cents(user["user_id"]) == 1000
    assert billing.reserved_cents(user["user_id"]) == 0
    token_rows = app.state.quota_reservations.list_reservations(key_id=public["key_id"])
    assert len(token_rows) == 1 and token_rows[0]["state"] == "released"
    app.state.quota_reservations.close()


def test_wallet_storage_outage_rejects_dispatch_and_releases_token_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-disk-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-io@example.com", "long-enough-password")
    public, token = asyncio.run(app.state.api_keys.create("wallet-io"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    app.state.user_billing.credit(user["user_id"], 1000)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)
    app.state.registry.resolve_client = lambda requested: "ext_wallet_io_test"
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send

    def fail_save() -> None:
        raise OSError("simulated billing file unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(app.state.user_billing, "_save", fail_save)
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "gpt-5.6-sol",
                    "messages": [{"role": "user", "content": "Wallet disk failure"}],
                    "max_tokens": 64,
                },
            )

    assert response.status_code == 503, response.text
    assert sent == []
    assert app.state.user_billing.available_balance_cents(user["user_id"]) == 1000
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



def test_wallet_recovery_uses_persisted_request_id_beyond_telemetry_memory_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-recovery-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register(
        "wallet-recovery@example.com", "long-enough-password",
    )
    public, _token = asyncio.run(app.state.api_keys.create("wallet-recovery"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=config["models"])
    billing = app.state.user_billing
    billing.credit(user["user_id"], 1000)
    billing.reserve_for_request(
        user["user_id"], "req_wallet_recovery_old",
        model_id="gpt-5.6-sol", estimated_cny_cents=100,
        input_tokens=100, output_tokens=4096,
        price_snapshot=pricing.model("gpt-5.6-sol"), usd_cny_rate=7.0,
    )

    # Simulate a process interruption after the terminal telemetry row was
    # durably appended but before BillingStore could project its charge.
    def fail_charge(*_args, **_kwargs):
        raise OSError("simulated crash between telemetry and billing projection")

    with monkeypatch.context() as patch:
        patch.setattr(billing, "record_charge", fail_charge)
        with pytest.raises(OSError, match="simulated crash"):
            asyncio.run(app.state.telemetry.append({
                "request_id": "req_wallet_recovery_old",
                "api_key_id": public["key_id"],
                "requested_model": "gpt-5.6-sol",
                "status": "completed",
                "usage": {
                    "prompt_tokens": 1000, "completion_tokens": 500,
                    "total_tokens": 1500, "estimated": True,
                },
                "diagnostics": {},
            }))

    # Evict the terminal row from TelemetryStore's maxlen=500 memory cache.
    async def append_noise() -> None:
        for index in range(501):
            await app.state.telemetry.append({
                "request_id": f"req_wallet_recovery_noise_{index}",
                "status": "running",
                "usage": {},
            })

    asyncio.run(append_noise())
    assert len(app.state.telemetry.recent(1000)) == 500
    assert all(
        row.get("request_id") != "req_wallet_recovery_old"
        for row in app.state.telemetry.recent(1000)
    )
    assert billing.reserved_cents(user["user_id"]) == 100
    assert billing.charge_for_request("req_wallet_recovery_old") is None

    session = app.state.user_accounts.create_session(user["user_id"])
    with TestClient(app) as client:
        # Lifespan startup loads persisted telemetry and runs wallet recovery
        # before serving the first request, despite the row being outside the
        # 500-item in-memory history window.
        assert billing.charge_for_request("req_wallet_recovery_old") is not None
        client.cookies.set("chat2api_user_session", session)
        response = client.get("/api/user/billing")

    assert response.status_code == 200, response.text
    charge = billing.charge_for_request("req_wallet_recovery_old")
    assert charge is not None and charge["debited"] is True
    assert billing.reservations["req_wallet_recovery_old"]["state"] == "settled"
    assert billing.reserved_cents(user["user_id"]) == 0
    assert billing.balance_cents(user["user_id"]) == 1000 - charge["cost_cny_cents"]



def test_wallet_enabled_rejects_disabled_model_price_and_releases_token_hold(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app.config import Settings
    from app.main import create_app
    from app.quota_reservations import QuotaReservationStore
    from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1, reserve_request_quota
    from app.user_console_v104_patch import install_user_console_v104_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="wallet-disabled-price-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_user_console_v104_patch(app)
    user = app.state.user_accounts.register("wallet-disabled-price@example.com", "long-enough-password")
    public, _token = asyncio.run(app.state.api_keys.create("wallet-disabled-price"))
    app.state.user_accounts.attach_key(user["user_id"], public["key_id"])
    pricing = app.state.user_pricing
    config = pricing.public()
    models = [dict(row) for row in config["models"]]
    for row in models:
        if row["model_id"] in {"gpt-5.6-sol", "gpt-5.6"}:
            row["enabled"] = False
    pricing.update(billing_enabled=True, usd_cny_rate=7.0, models=models)
    billing = app.state.user_billing
    billing.credit(user["user_id"], 1000)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    with pytest.raises(HTTPException) as error:
        reserve_request_quota(
            app, "req_wallet_disabled_price_0001",
            SimpleNamespace(kind="api_key", key_id=public["key_id"]),
            "disabled model price request", 4096, model_id="gpt-5.6-sol",
        )

    assert error.value.status_code == 503
    assert "not configured or enabled" in str(error.value.detail)
    assert billing.balance_cents(user["user_id"]) == 1000
    assert billing.reserved_cents(user["user_id"]) == 0
    assert billing.charge_for_request("req_wallet_disabled_price_0001") is None
    token_rows = app.state.quota_reservations.list_reservations(key_id=public["key_id"])
    assert len(token_rows) == 1 and token_rows[0]["state"] == "released"
    app.state.quota_reservations.close()

def test_legacy_payment_config_corruption_fails_closed_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "user_payment_config.json"
    original = b'{"version":'
    path.write_bytes(original)

    with pytest.raises(RuntimeError, match="existing file was preserved"):
        PaymentConfigStore(tmp_path, "server-local-data-secret")
    assert path.read_bytes() == original


def test_legacy_payment_config_cipher_mismatch_fails_closed_without_overwrite(tmp_path: Path) -> None:
    store = PaymentConfigStore(tmp_path, "original-server-data-secret")
    store.update({
        "enabled": True,
        "pid": "1234567890",
        "key": "legacy-zpay-secret",
        "alipay_enabled": True,
        "wechat_enabled": True,
        "alipay_cid": "1",
        "wechat_cid": "2",
        "public_origin": "https://api.example.com",
    })
    original = (tmp_path / "user_payment_config.json").read_bytes()

    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        PaymentConfigStore(tmp_path, "rotated-or-missing-server-data-secret")
    assert (tmp_path / "user_payment_config.json").read_bytes() == original


def test_legacy_payment_config_update_rolls_back_memory_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PaymentConfigStore(tmp_path, "server-local-data-secret")
    store.update({
        "enabled": True,
        "pid": "1234567890",
        "key": "existing-zpay-secret",
        "alipay_enabled": True,
        "wechat_enabled": True,
        "alipay_cid": "1",
        "wechat_cid": "2",
        "public_origin": "https://api.example.com",
    })
    previous_payload = dict(store.payload)
    previous_public = store.public()
    original_file = store.path.read_bytes()

    def fail_save() -> None:
        raise OSError("simulated payment config disk failure")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(OSError, match="simulated payment config disk failure"):
        store.update({
            "enabled": False,
            "pid": "different-pid",
            "key": "replacement-secret",
            "alipay_enabled": False,
            "wechat_enabled": False,
            "alipay_cid": "3",
            "wechat_cid": "4",
            "public_origin": "https://other.example.com",
        })

    assert store.payload == previous_payload
    assert store.public() == previous_public
    assert store.path.read_bytes() == original_file

@pytest.mark.parametrize(
    "corrupt_payload",
    [
        b'{"version":',
        b'{"version":1,"users":{},"sessions":{}}',
        b'{"version":1,"users":[],"sessions":{"not-a-sha256-digest":{"user_id":"usr_missing","expires_at":9999999999}}}',
    ],
)
def test_user_account_store_corruption_fails_closed_without_overwrite(
    tmp_path: Path, corrupt_payload: bytes,
) -> None:
    path = tmp_path / "user_accounts.json"
    path.write_bytes(corrupt_payload)

    with pytest.raises(RuntimeError, match="existing file was preserved"):
        UserAccountStore(tmp_path)
    assert path.read_bytes() == corrupt_payload


def test_user_account_store_rejects_duplicate_persisted_key_owners_without_overwrite(
    tmp_path: Path,
) -> None:
    import json

    store = UserAccountStore(tmp_path)
    alice = store.register("account-owner-a@example.com", "valid-password-123")
    bob = store.register("account-owner-b@example.com", "valid-password-456")
    store.attach_key(alice["user_id"], "key_shared_account")
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    next(row for row in payload["users"] if row["user_id"] == bob["user_id"])["key_ids"] = [
        "key_shared_account"
    ]
    corrupt_payload = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    store.path.write_bytes(corrupt_payload)

    with pytest.raises(RuntimeError, match="existing file was preserved"):
        UserAccountStore(tmp_path)
    assert store.path.read_bytes() == corrupt_payload


def test_user_account_attach_key_prevents_cross_user_reassignment(tmp_path: Path) -> None:
    store = UserAccountStore(tmp_path)
    alice = store.register("account-attach-a@example.com", "valid-password-123")
    bob = store.register("account-attach-b@example.com", "valid-password-456")
    store.attach_key(alice["user_id"], "key_owned_by_alice")

    with pytest.raises(ValueError, match="already assigned to another user"):
        store.attach_key(bob["user_id"], "key_owned_by_alice")

    assert store.owner_for_key("key_owned_by_alice") == alice["user_id"]
    assert store.owns_key(alice["user_id"], "key_owned_by_alice") is True
    assert store.owns_key(bob["user_id"], "key_owned_by_alice") is False


@pytest.mark.parametrize(
    "mutation",
    [
        "register",
        "create_session",
        "authenticate_expired",
        "revoke_session",
        "update_profile",
        "attach_key",
        "set_enabled",
        "revoke_user_sessions",
        "cleanup_sessions",
    ],
)
def test_user_account_store_mutations_roll_back_after_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    import copy
    import time

    store = UserAccountStore(tmp_path)
    user = store.register("account-rollback@example.com", "valid-password-123")
    user_id = user["user_id"]
    store.attach_key(user_id, "key_account_rollback")
    token = store.create_session(user_id)
    if mutation in {"authenticate_expired", "cleanup_sessions"}:
        store.sessions[next(iter(store.sessions))]["expires_at"] = time.time() - 1

    previous_users = copy.deepcopy(store.users)
    previous_sessions = copy.deepcopy(store.sessions)

    def fail_save() -> None:
        raise OSError("simulated account store persistence failure")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(OSError, match="simulated account store persistence failure"):
        if mutation == "register":
            store.register("account-rollback-new@example.com", "another-valid-password")
        elif mutation == "create_session":
            store.create_session(user_id)
        elif mutation == "authenticate_expired":
            store.authenticate(token)
        elif mutation == "revoke_session":
            store.revoke_session(token)
        elif mutation == "update_profile":
            store.update_profile(user_id, display_name="Changed")
        elif mutation == "attach_key":
            store.attach_key(user_id, "key_account_rollback_new")
        elif mutation == "set_enabled":
            store.set_enabled(user_id, False)
        elif mutation == "revoke_user_sessions":
            store.revoke_user_sessions(user_id)
        elif mutation == "cleanup_sessions":
            store.admin_list()

    assert store.users == previous_users
    assert store.sessions == previous_sessions

