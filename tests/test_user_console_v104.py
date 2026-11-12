from __future__ import annotations

import hashlib
from pathlib import Path

from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload
from app.user_commerce import BillingStore, PaymentConfigStore, PricingStore, UserAccountStore
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
