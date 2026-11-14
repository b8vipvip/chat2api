from __future__ import annotations

import contextvars
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.admin_auth import AdminSessionStore
from app.api_keys import ApiPrincipal
from app.live_voice_patch import install_live_voice_patch
from app.voice_patch import install_voice_patch
from app.model_entitlement_v1_patch import install_model_entitlements
from app.model_capability_routing_patch import install_model_capability_routing_patch
from app.model_entitlements import ModelEntitlementError, ModelEntitlementStore
from app import model_capability_routing_patch as model_routing
from app import v13_patch


class FakeRegistry:
    def __init__(self):
        self.routing_key_context = contextvars.ContextVar("test_route_key", default=None)
        self.resolve_calls = []
        self.busy_clients = set()
        self.catalog = [
            {"id": "default", "label": "Default"},
            {"id": "chatgpt-web", "label": "Web alias"},
            {"id": "gpt-5.5", "label": "GPT 5.5"},
            {"id": "gpt-5.6-sol", "label": "GPT 5.6 Sol"},
        ]

    def model_catalog(self, online_only=True):
        return list(self.catalog)

    def resolve_client(self, requested):
        selected = requested or "worker_1"
        self.resolve_calls.append(selected)
        return selected

    def set_routing_key(self, key_id):
        self.routing_key_context.set(key_id)

    def online_client_ids(self):
        return ["worker_1"]

    def route_for_key(self, key_id):
        return None


class FakeAccounts:
    def owner_for_key(self, key_id):
        return {"key_1": "usr_abcdef123"}.get(key_id)

    def authenticate(self, token):
        return {"user_id": "usr_abcdef123"} if token == "valid-user-session" else None

    def admin_get(self, user_id):
        return {"user_id": user_id} if user_id == "usr_abcdef123" else None


class FakeTenantMemberships:
    def exists(self, tenant_id):
        return tenant_id == "ten_abc123"

    def tenant_for_key(self, key_id):
        return "ten_abc123" if key_id == "key_1" else None

    def user_for_key(self, key_id):
        return "usr_abcdef123" if key_id == "key_1" else None

    def role_for(self, tenant_id, user_id):
        return "owner" if tenant_id == "ten_abc123" and user_id == "usr_abcdef123" else None


class FakeApiKeys:
    async def authenticate(self, token):
        if token == "voice-token":
            return ApiPrincipal(
                key_id="key_1", name="voice-test", kind="managed", scopes=("audio",)
            )
        return None

    def get_public(self, key_id):
        return {"key_id": key_id} if key_id in {"key_1", "key_2"} else None


def build_app(tmp_path: Path):
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path, admin_username="owner")
    app.state.registry = FakeRegistry()
    app.state.user_accounts = FakeAccounts()
    app.state.tenant_memberships = FakeTenantMemberships()
    app.state.api_keys = FakeApiKeys()
    app.state.admin_sessions = AdminSessionStore(
        "owner", "strong-admin-password", storage_path=tmp_path / "admin_sessions.json"
    )
    token = app.state.admin_sessions.create()

    @app.get("/api/user/models")
    async def user_models():
        return {"data": [
            {"id": "gpt-5.5", "enabled": True},
            {"id": "gpt-5.6-sol", "enabled": True},
            {"id": "default", "enabled": True},
        ]}

    install_model_entitlements(app)
    return app, token



class StopBeforeWorkerDispatchBroker:
    def can_accept(self, client_id, weight=1):
        return True

    async def create(self, request_id, client_id):
        # Stop after the entitlement gate but before any Worker dispatch.
        raise RuntimeError("fixture stopped after entitlement check")


def install_voice_test_runtime(app):
    app.state.settings.api_key = "master-test-secret"
    app.state.settings.request_timeout_seconds = 30
    app.state.broker = StopBeforeWorkerDispatchBroker()
    app.state.telemetry = SimpleNamespace()
    app.state.file_store = SimpleNamespace(get=lambda _file_id: SimpleNamespace(
        file_id="file_audio", filename="audio.wav", mime_type="audio/wav",
        size=10, owner_key_id="key_1",
    ))


@pytest.mark.parametrize("model_id", ["gpt-live", "gpt-live-mini"])
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/v1/audio/speech", {"input": "hello"}),
        ("/v1/audio/conversations", {"audio_file_id": "file_audio", "instruction": "hello"}),
    ],
)
def test_voice_endpoints_entitlement_uses_the_requested_model(
    tmp_path: Path, model_id: str, path: str, payload: dict,
):
    app, _ = build_app(tmp_path)
    install_voice_test_runtime(app)
    install_voice_patch(app)
    app.state.model_entitlements.upsert("key", "key_1", "allow", [model_id])

    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"Authorization": "Bearer voice-token"},
            json={"model": model_id, **payload},
        )

    # 409 is the deliberate broker fixture stop; 403 would mean the model
    # context was lost and the entitlement resolver saw an empty model.
    assert response.status_code == 409, response.text
    assert app.state.registry.resolve_calls == ["worker_1"]


def test_live_realtime_entitlement_uses_session_start_model(tmp_path: Path):
    app, _ = build_app(tmp_path)
    install_voice_test_runtime(app)
    install_live_voice_patch(app)
    app.state.model_entitlements.upsert("key", "key_1", "allow", ["gpt-live-mini"])

    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/realtime",
            headers={"Authorization": "Bearer voice-token"},
        ) as websocket:
            websocket.send_text(json.dumps({
                "type": "session.start", "model": "gpt-live-mini",
            }))
            error = websocket.receive_json()

    # The allow rule is evaluated against the requested alias (mini), not an
    # empty ContextVar or the transport's effective gpt-live alias.
    assert error["code"] == "GPT_LIVE_UNAVAILABLE"
    assert app.state.registry.resolve_calls == ["worker_1"]


def test_live_realtime_denied_model_returns_authorization_error(tmp_path: Path):
    app, _ = build_app(tmp_path)
    install_voice_test_runtime(app)
    install_live_voice_patch(app)
    app.state.model_entitlements.upsert("key", "key_1", "deny", ["gpt-live-mini"])

    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/realtime",
            headers={"Authorization": "Bearer voice-token"},
        ) as websocket:
            websocket.send_text(json.dumps({
                "type": "session.start", "model": "gpt-live-mini",
            }))
            error = websocket.receive_json()

    assert error["code"] == "model_not_entitled"
    assert error["retryable"] is False
    assert app.state.registry.resolve_calls == []


def set_model_context(model_id: str):
    local = model_routing._MODEL_CONTEXT.set({"model": model_id})
    historical = v13_patch._target_context.set({"model": model_id})
    return local, historical


def reset_model_context(tokens):
    model_routing._MODEL_CONTEXT.reset(tokens[0])
    v13_patch._target_context.reset(tokens[1])


def test_entitlement_rules_are_persistent_and_validate_concrete_models(tmp_path: Path):
    store = ModelEntitlementStore(tmp_path)
    global_rule = store.upsert("global", "default", "allow", ["gpt-5-6", "gpt-5.5"])
    assert global_rule["models"] == ["gpt-5.6-sol", "gpt-5.5"]
    store.upsert("key", "key_1", "allow", ["gpt-5.5"])
    store.upsert("user", "usr_abcdef123", "deny", ["gpt-5.5"])
    with pytest.raises(ModelEntitlementError):
        store.upsert("key", "key_1", "allow", ["default"])
    with pytest.raises(ModelEntitlementError):
        store.upsert("global", "default", "allow", [""])
    restored = ModelEntitlementStore(tmp_path)
    assert len(restored.list_rules()) == 3
    assert restored.evaluate("gpt-5.5", key_id="key_1", user_id="usr_abcdef123")["reason"] == "explicit_deny"
    assert not restored.evaluate("gpt-5.6-sol", key_id="key_1", user_id="usr_abcdef123")["allowed"]
    assert restored.path.stat().st_mode & 0o777 == 0o600
    store.close()
    restored.close()


def test_allowlists_intersect_denies_override_and_unresolved_aliases_fail_closed(tmp_path: Path):
    store = ModelEntitlementStore(tmp_path)
    store.upsert("global", "default", "allow", ["gpt-5.5", "gpt-5.6-sol"])
    store.upsert("key", "key_1", "allow", ["gpt-5.5"])
    assert store.evaluate("gpt-5-5-thinking", key_id="key_1", user_id=None)["allowed"]
    assert not store.evaluate("gpt-5.6-sol", key_id="key_1", user_id=None)["allowed"]
    assert not store.evaluate("default", key_id="key_1", user_id=None)["allowed"]
    assert store.evaluate("gpt-5.6-sol", key_id="key_2", user_id=None)["allowed"] is True
    # A policy scoped to another key does not restrict this key.
    assert store.evaluate("gpt-5.5", key_id="key_2", user_id=None)["allowed"]
    store.close()


def test_api_key_and_owner_policies_filter_catalog_and_gate_dispatch(tmp_path: Path):
    app, _ = build_app(tmp_path)
    store = app.state.model_entitlements
    registry = app.state.registry

    store.upsert("global", "default", "allow", ["gpt-5.5", "gpt-5.6-sol"])
    store.upsert("key", "key_1", "allow", ["gpt-5.5"])
    store.upsert("user", "usr_abcdef123", "deny", ["gpt-5.5"])

    registry.routing_key_context.set("key_1")
    assert registry.model_catalog() == []

    # Removing the user-level deny leaves the global/key allowlist intersection.
    store.delete("user", "usr_abcdef123")
    assert [row["id"] for row in registry.model_catalog()] == ["gpt-5.5"]

    tokens = set_model_context("gpt-5.6-sol")
    try:
        with pytest.raises(HTTPException) as denied:
            registry.resolve_client(None)
        assert denied.value.status_code == 403
        assert denied.value.detail["code"] == "model_not_entitled"
    finally:
        reset_model_context(tokens)

    tokens = set_model_context("gpt-5-5-thinking")
    try:
        assert registry.resolve_client(None) == "worker_1"
    finally:
        reset_model_context(tokens)

    # Non-user-managed keys retain their own key rule only; master bypass is explicit.
    registry.routing_key_context.set("key_2")
    assert [row["id"] for row in registry.model_catalog()] == ["gpt-5.5", "gpt-5.6-sol"]
    registry.routing_key_context.set("master")
    assert len(registry.model_catalog()) == 4
    tokens = set_model_context("gpt-5.6-sol")
    try:
        assert registry.resolve_client(None) == "worker_1"
    finally:
        reset_model_context(tokens)


def test_identity_and_tenant_lookup_failures_fail_closed(tmp_path: Path):
    app, _ = build_app(tmp_path)
    registry = app.state.registry
    registry.routing_key_context.set("key_1")
    tokens = set_model_context("gpt-5.5")

    original_owner_lookup = app.state.user_accounts.owner_for_key
    original_tenant_lookup = app.state.tenant_memberships.tenant_for_key
    original_user_lookup = app.state.tenant_memberships.user_for_key

    def unavailable(_key_id):
        raise RuntimeError("simulated identity store outage")

    try:
        app.state.user_accounts.owner_for_key = unavailable
        with pytest.raises(HTTPException) as identity_error:
            registry.resolve_client(None)
        assert identity_error.value.status_code == 503
        assert identity_error.value.detail["code"] == "model_entitlement_identity_unavailable"

        app.state.user_accounts.owner_for_key = original_owner_lookup
        app.state.user_accounts.owner_for_key = lambda _key_id: ""
        with pytest.raises(HTTPException) as empty_owner_error:
            registry.resolve_client(None)
        assert empty_owner_error.value.status_code == 503
        assert empty_owner_error.value.detail["code"] == "model_entitlement_identity_unavailable"

        app.state.user_accounts.owner_for_key = original_owner_lookup
        app.state.tenant_memberships.tenant_for_key = unavailable
        with pytest.raises(HTTPException) as tenant_error:
            registry.resolve_client(None)
        assert tenant_error.value.status_code == 503
        assert tenant_error.value.detail["code"] == "model_entitlement_tenant_context_unavailable"

        app.state.tenant_memberships.tenant_for_key = original_tenant_lookup
        app.state.tenant_memberships.tenant_for_key = lambda _key_id: ""
        with pytest.raises(HTTPException) as empty_tenant_error:
            registry.resolve_client(None)
        assert empty_tenant_error.value.status_code == 503
        assert empty_tenant_error.value.detail["code"] == "model_entitlement_tenant_context_unavailable"

        app.state.tenant_memberships.tenant_for_key = original_tenant_lookup
        app.state.tenant_memberships.user_for_key = lambda _key_id: None
        with pytest.raises(HTTPException) as orphan_error:
            registry.resolve_client(None)
        assert orphan_error.value.status_code == 503
        assert orphan_error.value.detail["code"] == "model_entitlement_tenant_context_unavailable"
    finally:
        app.state.user_accounts.owner_for_key = original_owner_lookup
        app.state.tenant_memberships.tenant_for_key = original_tenant_lookup
        app.state.tenant_memberships.user_for_key = original_user_lookup
        reset_model_context(tokens)


def test_tenant_key_binding_must_match_current_authoritative_key_owner(tmp_path: Path):
    app, _ = build_app(tmp_path)
    registry = app.state.registry
    registry.routing_key_context.set("key_1")
    app.state.model_entitlements.upsert("tenant", "ten_abc123", "allow", ["gpt-5.5"])
    tokens = set_model_context("gpt-5.5")
    original_owner_lookup = app.state.user_accounts.owner_for_key
    try:
        # A stale binding must not lend the former owner's workspace role to
        # a key that is now owned by a different account.
        app.state.user_accounts.owner_for_key = lambda _key_id: "usr_otherowner"
        with pytest.raises(HTTPException) as reassigned:
            registry.resolve_client(None)
        assert reassigned.value.status_code == 503
        assert reassigned.value.detail["code"] == "model_entitlement_tenant_context_unavailable"

        # An orphaned tenant-bound key is not a valid unowned standalone key.
        app.state.user_accounts.owner_for_key = lambda _key_id: None
        with pytest.raises(HTTPException) as orphaned:
            registry.resolve_client(None)
        assert orphaned.value.status_code == 503
        assert orphaned.value.detail["code"] == "model_entitlement_tenant_context_unavailable"

        # The unchanged authoritative owner retains the active workspace role.
        app.state.user_accounts.owner_for_key = original_owner_lookup
        assert registry.resolve_client(None) == "worker_1"
    finally:
        app.state.user_accounts.owner_for_key = original_owner_lookup
        reset_model_context(tokens)


def test_admin_entitlement_api_requires_session_audits_and_validates_subjects(tmp_path: Path):
    app, token = build_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/admin/model-entitlements").status_code == 401
        headers = {"Cookie": f"chat2api_admin_session={token}"}
        created = client.put(
            "/api/admin/model-entitlements/key/key_1",
            headers=headers,
            json={"effect": "allow", "models": ["gpt-5.5"]},
        )
        assert created.status_code == 200, created.text
        assert created.json()["rule"]["models"] == ["gpt-5.5"]
        listed = client.get("/api/admin/model-entitlements", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["data"][0]["subject_type"] == "key"
        missing = client.put(
            "/api/admin/model-entitlements/user/usr_not_real",
            headers=headers,
            json={"effect": "deny", "models": ["gpt-5.5"]},
        )
        assert missing.status_code == 404
        invalid = client.put(
            "/api/admin/model-entitlements/global/default",
            headers=headers,
            json={"effect": "allow", "models": ["default"]},
        )
        assert invalid.status_code == 400
        tenant_rule = client.put(
            "/api/admin/model-entitlements/tenant/ten_abc123",
            headers=headers,
            json={"effect": "allow", "models": ["gpt-5.5"]},
        )
        assert tenant_rule.status_code == 200
        missing_tenant = client.put(
            "/api/admin/model-entitlements/tenant/ten_missing",
            headers=headers,
            json={"effect": "deny", "models": ["gpt-5.5"]},
        )
        assert missing_tenant.status_code == 404
        tenant_role = client.put(
            "/api/admin/model-entitlements/tenant_role/owner",
            headers=headers,
            json={"effect": "allow", "models": ["gpt-5.5"]},
        )
        assert tenant_role.status_code == 200
        invalid_tenant_role = client.put(
            "/api/admin/model-entitlements/tenant_role/root",
            headers=headers,
            json={"effect": "deny", "models": ["gpt-5.5"]},
        )
        assert invalid_tenant_role.status_code == 400
        deleted = client.delete("/api/admin/model-entitlements/key/key_1", headers=headers)
        assert deleted.status_code == 200 and deleted.json()["deleted"] is True
        events = app.state.admin_audit.recent(20)
        assert any(row["action"] == "model.entitlement_completed" for row in events)


def test_images_generation_entitlement_uses_server_owned_gpt_image_target(tmp_path: Path):
    app, _ = build_app(tmp_path)
    install_model_capability_routing_patch(app)

    @app.post("/v1/images/generations")
    async def image_route():
        token = app.state.registry.routing_key_context.set("key_1")
        try:
            return {"client_id": app.state.registry.resolve_client("worker_1")}
        finally:
            app.state.registry.routing_key_context.reset(token)

    store = app.state.model_entitlements
    store.upsert("key", "key_1", "deny", ["gpt-image"])
    with TestClient(app) as client:
        denied = client.post("/v1/images/generations", json={"model": "gpt-5.5"})
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "model_not_entitled"

        store.delete("key", "key_1")
        store.upsert("key", "key_1", "allow", ["gpt-image"])
        allowed = client.post("/v1/images/generations", json={"model": "gpt-5.5"})
        assert allowed.status_code == 200
        assert allowed.json()["client_id"] == "worker_1"


def test_user_console_model_catalog_obeys_global_and_user_entitlements(tmp_path: Path):
    app, _ = build_app(tmp_path)
    store = app.state.model_entitlements
    store.upsert("global", "default", "allow", ["gpt-5.5", "gpt-5.6-sol"])
    store.upsert("user", "usr_abcdef123", "deny", ["gpt-5.5"])
    with TestClient(app) as client:
        response = client.get(
            "/api/user/models",
            cookies={"chat2api_user_session": "valid-user-session"},
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["data"]] == ["gpt-5.6-sol"]


def test_tenant_allowlist_applies_only_through_explicit_api_key_binding(tmp_path: Path):
    app, _ = build_app(tmp_path)
    store = app.state.model_entitlements
    registry = app.state.registry
    store.upsert("global", "default", "allow", ["gpt-5.5", "gpt-5.6-sol"])
    store.upsert("tenant", "ten_abc123", "allow", ["gpt-5.5"])
    store.upsert("tenant_role", "owner", "allow", ["gpt-5.5"])

    registry.routing_key_context.set("key_1")
    assert [row["id"] for row in registry.model_catalog()] == ["gpt-5.5"]
    tokens = set_model_context("gpt-5.6-sol")
    try:
        with pytest.raises(HTTPException) as denied:
            registry.resolve_client(None)
        assert denied.value.status_code == 403
    finally:
        reset_model_context(tokens)

    # An unbound key does not inherit another tenant's policy.
    registry.routing_key_context.set("key_2")
    assert [row["id"] for row in registry.model_catalog()] == ["gpt-5.5", "gpt-5.6-sol"]
