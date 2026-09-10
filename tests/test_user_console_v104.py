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


def test_user_console_contains_requested_modules_without_runtime_disclosure() -> None:
    html = (ROOT / "app" / "user_console_v104.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "user_console_v104.js").read_text(encoding="utf-8")
    for label in ("API 密钥", "请求记录", "账户资料", "费用中心", "模型广场", "测试场", "数据看板", "开发文档"):
        assert label in html
    public_surface = html + "\n" + js
    for forbidden in ("ChatGPT", "Chrome", "Worker", "extension", "Linux", "client_id", "pairing", "Window Management"):
        assert forbidden not in public_surface


def test_user_api_privacy_boundary_does_not_relay_runtime_failure_text() -> None:
    source = (ROOT / "app" / "user_console_privacy_v105_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'path == "/api/user/playground"' in source
    assert "测试请求失败，请稍后重试或查看请求记录" in source
    assert "response.status_code >= 500" in source
    assert "install_user_console_privacy_v105_patch(app)" in entry
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")


def test_admin_console_extension_adds_price_and_payment_navigation() -> None:
    js = (ROOT / "app" / "admin_user_commerce_v104.js").read_text(encoding="utf-8")
    assert "价格配置" in js
    assert "支付配置" in js
    assert "/api/admin/user-pricing" in js
    assert "/api/admin/user-payments" in js
    assert "测试 API 连接" in js


def test_user_commerce_runtime_and_production_dependencies_are_published() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.74"
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
