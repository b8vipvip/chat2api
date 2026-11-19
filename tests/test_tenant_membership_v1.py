from __future__ import annotations

from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from app.admin_audit import AdminAuditError, AdminAuditStore
from app.config import Settings
from app.main import create_app
from app.tenant_membership import TenantMembershipError, TenantMembershipStore
from app.tenant_membership_v1_patch import install_tenant_memberships
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def app_with_tenants(path: Path):
    app = create_app(Settings(
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password",
        CHAT2API_DATA_DIR=str(path),
    ))
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_tenant_memberships(app)
    return app


def test_workspace_store_membership_and_last_owner_guard(tmp_path: Path):
    store = TenantMembershipStore(tmp_path)
    a = store.create("alpha-team", "Alpha Team")
    b = store.create("beta-team", "Beta Team")
    assert len(store.list_tenants()) == 2
    with pytest.raises(TenantMembershipError, match="already exists"):
        store.create("alpha-team", "Dup")
    with pytest.raises(TenantMembershipError, match="Invalid tenant slug"):
        store.create("BAD", "Wrong")
    user1, user2 = "usr_ABCDEFGH123", "usr_ABCDEFGH999"
    store.upsert_member(a["tenant_id"], user1, "owner")
    store.upsert_member(b["tenant_id"], user2, "owner")
    assert store.role_for(a["tenant_id"], user1) == "owner"
    assert store.role_for(b["tenant_id"], user1) is None
    assert [x["tenant_id"] for x in store.list_for_user(user1)] == [a["tenant_id"]]
    assert [x["tenant_id"] for x in store.list_for_user(user2)] == [b["tenant_id"]]
    with pytest.raises(TenantMembershipError, match="last workspace owner"):
        store.remove_member(a["tenant_id"], user1)
    with pytest.raises(TenantMembershipError, match="last workspace owner"):
        store.upsert_member(a["tenant_id"], user1, "member")
    store.upsert_member(a["tenant_id"], user2, "owner")
    store.bind_key(a["tenant_id"], user1, "key_123456789")
    assert store.tenant_for_key("key_123456789") == a["tenant_id"]
    assert store.user_for_key("key_123456789") == user1
    assert store.keys_for_tenant(a["tenant_id"])[0]["user_id"] == user1
    with pytest.raises(TenantMembershipError, match="already bound"):
        store.bind_key(b["tenant_id"], user2, "key_123456789")
    with pytest.raises(TenantMembershipError, match="Unbind"):
        store.remove_member(a["tenant_id"], user1)
    store.unbind_key(a["tenant_id"], "key_123456789")
    assert store.tenant_for_key("key_123456789") is None
    store.remove_member(a["tenant_id"], user1)
    assert store.role_for(a["tenant_id"], user1) is None
    assert store.role_for(a["tenant_id"], user2) == "owner"
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.close()

    restored = TenantMembershipStore(tmp_path)
    assert restored.role_for(a["tenant_id"], user2) == "owner"
    restored.close()


def test_admin_and_user_routes_enforce_session_csrf_and_scoped_member_discovery(tmp_path: Path):
    app = app_with_tenants(tmp_path)
    with TestClient(app) as root:
        assert root.get("/api/admin/tenants").status_code == 401
        assert root.get("/api/user/tenants").status_code == 401
        login = root.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password",
        }, headers=ORIGIN)
        assert login.status_code == 200
        assert root.post("/api/admin/tenants", json={"slug": "org-one", "display_name": "One"}).status_code == 403
        tenant = root.post("/api/admin/tenants", headers=ORIGIN,
                           json={"slug": "org-one", "display_name": "One"})
        assert tenant.status_code == 201, tenant.text
        tid = tenant.json()["tenant"]["tenant_id"]
        other = root.post("/api/admin/tenants", headers=ORIGIN,
                          json={"slug": "org-two", "display_name": "Two"})
        assert other.status_code == 201, other.text
        tid2 = other.json()["tenant"]["tenant_id"]

        with TestClient(app) as alice, TestClient(app) as bob:
            a = alice.post("/api/user/register", json={
                "email": "alice@example.com", "password": "alice-password-123",
            })
            b = bob.post("/api/user/register", json={
                "email": "bob@example.com", "password": "bob-password-123",
            })
            assert a.status_code == b.status_code == 200
            uid1 = a.json()["user"]["user_id"]
            uid2 = b.json()["user"]["user_id"]

            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": uid1, "role": "owner"
            }).status_code == 200
            assert root.put(f"/api/admin/tenants/{tid2}/members", headers=ORIGIN, json={
                "user_id": uid2, "role": "owner"
            }).status_code == 200
            created_key = alice.post("/api/user/keys", json={"name": "Alice workspace key"})
            assert created_key.status_code == 200, created_key.text
            key_id = created_key.json()["key"]["key_id"]
            bound = root.put(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert bound.status_code == 200, bound.text
            assert app.state.tenant_memberships.tenant_for_key(key_id) == tid
            assert root.get(f"/api/admin/tenants/{tid}/keys").json()["data"][0]["key_id"] == key_id
            cross_tenant = root.put(f"/api/admin/tenants/{tid2}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert cross_tenant.status_code == 409
            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": "usr_NOT_REAL123", "role": "owner"
            }).status_code == 404
            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": uid2, "role": "root"
            }).status_code == 400

            a_rows = alice.get("/api/user/tenants")
            b_rows = bob.get("/api/user/tenants")
            assert [x["tenant_id"] for x in a_rows.json()["data"]] == [tid]
            assert [x["tenant_id"] for x in b_rows.json()["data"]] == [tid2]
            assert alice.get("/api/admin/tenants").status_code == 401
            assert root.delete(f"/api/admin/tenants/{tid}/members/{uid1}", headers=ORIGIN).status_code == 409
            assert root.delete(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN).status_code == 200
            assert app.state.tenant_memberships.tenant_for_key(key_id) is None
            app.state.api_keys.keys[key_id].enabled = False
            disabled_binding = root.put(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert disabled_binding.status_code == 409
            assert root.get(f"/api/admin/tenants/{tid}/members").json()["data"][0]["user_id"] == uid1

        # No change to the existing Mode 2 business API's auth contract.
        assert root.get("/v1/models").status_code == 401
        assert any(x["action"] == "tenant.member_change_completed" for x in app.state.admin_audit.recent(20))


def test_workspace_invitation_is_email_bound_single_use_and_revocable(tmp_path: Path, monkeypatch):
    import json
    import time

    store = TenantMembershipStore(tmp_path)
    tenant = store.create("invite-team", "Invitation Team")
    store.upsert_member(tenant["tenant_id"], "usr_owner12345", "owner")

    with pytest.raises(TenantMembershipError, match="owner assignment requires an explicit admin action"):
        store.create_invitation(tenant["tenant_id"], "owner-target@example.com", "owner", "root")

    invitation, code = store.create_invitation(
        tenant["tenant_id"], "Member@Example.com", "member", "root",
        expires_in_seconds=600,
    )
    assert invitation["email"] == "member@example.com"
    assert invitation["status"] == "issued"
    assert len(code) >= 32
    listed = store.list_invitations(tenant["tenant_id"])
    assert listed[0]["invitation_id"] == invitation["invitation_id"]
    assert "code" not in listed[0] and "token_hash" not in listed[0]
    stored_hash = store.db.execute(
        "SELECT token_hash FROM tenant_invitations WHERE invitation_id=?",
        (invitation["invitation_id"],),
    ).fetchone()[0]
    assert stored_hash != code
    assert stored_hash == __import__("hashlib").sha256(code.encode("utf-8")).hexdigest()

    with pytest.raises(TenantMembershipError, match="email does not match"):
        store.accept_invitation(code, "usr_member12345", "attacker@example.com")
    accepted = store.accept_invitation(code, "usr_member12345", "MEMBER@example.com")
    assert accepted == {
        "tenant_id": tenant["tenant_id"], "user_id": "usr_member12345", "role": "member",
    }
    with pytest.raises(TenantMembershipError, match="no longer available"):
        store.accept_invitation(code, "usr_member12345", "member@example.com")
    assert store.role_for(tenant["tenant_id"], "usr_member12345") == "member"

    revoked, revoked_code = store.create_invitation(
        tenant["tenant_id"], "revoked@example.com", "viewer", "root",
    )
    assert store.revoke_invitation(tenant["tenant_id"], revoked["invitation_id"]) is True
    revoked_row = next(
        row for row in store.list_invitations(tenant["tenant_id"])
        if row["invitation_id"] == revoked["invitation_id"]
    )
    assert revoked_row["status"] == "revoked"
    with pytest.raises(TenantMembershipError, match="no longer available"):
        store.accept_invitation(revoked_code, "usr_revoked12345", "revoked@example.com")

    expiring, expiring_code = store.create_invitation(
        tenant["tenant_id"], "expired@example.com", "viewer", "root",
        expires_in_seconds=300,
    )
    original_time = time.time
    monkeypatch.setattr("app.tenant_membership.time.time", lambda: original_time() + 301)
    with pytest.raises(TenantMembershipError, match="expired"):
        store.accept_invitation(expiring_code, "usr_expired12345", "expired@example.com")
    assert next(
        row for row in store.list_invitations(tenant["tenant_id"])
        if row["invitation_id"] == expiring["invitation_id"]
    )["status"] == "expired"
    store.close()


def test_tenant_invitation_api_issues_revokes_and_accepts_with_authenticated_email(tmp_path: Path):
    from app.user_commerce import USER_SESSION_COOKIE

    app = app_with_tenants(tmp_path)
    with TestClient(app) as admin, TestClient(app) as member:
        assert admin.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password",
        }, headers=ORIGIN).status_code == 200
        created = admin.post("/api/admin/tenants", json={
            "slug": "invite-api-team", "display_name": "Invite API Team",
        }, headers=ORIGIN)
        assert created.status_code == 201, created.text
        tenant_id = created.json()["tenant"]["tenant_id"]

        registered = member.post("/api/user/register", json={
            "email": "api-member@example.com", "password": "member-password-123",
        })
        assert registered.status_code == 200, registered.text
        user_id = registered.json()["user"]["user_id"]
        assert member.cookies.get(USER_SESSION_COOKIE)

        issued = admin.post(
            f"/api/admin/tenants/{tenant_id}/invitations",
            headers=ORIGIN,
            json={"email": "API-MEMBER@example.com", "role": "member"},
        )
        assert issued.status_code == 201, issued.text
        code = issued.json()["code"]
        invitation_id = issued.json()["invitation"]["invitation_id"]
        listing = admin.get(f"/api/admin/tenants/{tenant_id}/invitations")
        assert listing.status_code == 200
        assert "code" not in listing.text and "token_hash" not in listing.text

        missing_origin = member.post(
            "/api/user/tenants/invitations/accept", json={"code": code},
        )
        assert missing_origin.status_code == 403
        wrong_origin = member.post(
            "/api/user/tenants/invitations/accept", json={"code": code},
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert wrong_origin.status_code == 403

        accepted = member.post(
            "/api/user/tenants/invitations/accept", json={"code": code}, headers=ORIGIN,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["membership"]["user_id"] == user_id
        assert [row["tenant_id"] for row in member.get("/api/user/tenants").json()["data"]] == [tenant_id]
        replay = member.post(
            "/api/user/tenants/invitations/accept", json={"code": code}, headers=ORIGIN,
        )
        assert replay.status_code == 409

        revoked = admin.post(
            f"/api/admin/tenants/{tenant_id}/invitations",
            headers=ORIGIN,
            json={"email": "revoked@example.com", "role": "viewer"},
        )
        assert revoked.status_code == 201
        revoke_id = revoked.json()["invitation"]["invitation_id"]
        revoked_response = admin.delete(
            f"/api/admin/tenants/{tenant_id}/invitations/{revoke_id}", headers=ORIGIN,
        )
        assert revoked_response.status_code == 200
        assert revoked_response.json()["revoked"] is True

        audit_rows = app.state.admin_audit.recent(50)
        assert any(row["action"] == "tenant.invitation_issue_completed" for row in audit_rows)
        assert any(row["action"] == "tenant.invitation_accept_completed" for row in audit_rows)
        assert any(row["action"] == "tenant.invitation_revoke_completed" for row in audit_rows)
        assert all(code not in json.dumps(row) for row in audit_rows)
        assert issued.headers["Cache-Control"] == "no-store"
        assert accepted.headers["Cache-Control"] == "no-store"


def test_tenant_mutation_audit_failure_does_not_report_a_committed_change_as_failed(tmp_path: Path, monkeypatch):
    app = app_with_tenants(tmp_path)
    app.state.admin_audit = AdminAuditStore(tmp_path)
    audit = app.state.admin_audit
    original_append = audit.append

    def fail_requested(**kwargs):
        if kwargs["action"] == "tenant.create_requested":
            raise AdminAuditError("simulated pre-mutation audit failure")
        return original_append(**kwargs)

    monkeypatch.setattr(audit, "append", fail_requested)
    with TestClient(app) as client:
        login = client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password",
        }, headers=ORIGIN)
        assert login.status_code == 200

        denied = client.post("/api/admin/tenants", headers=ORIGIN, json={
            "slug": "org-denied", "display_name": "Denied",
        })
        assert denied.status_code == 503
        assert app.state.tenant_memberships.list_tenants() == []

        def fail_completion(**kwargs):
            if kwargs["action"] == "tenant.create_completed":
                raise AdminAuditError("simulated post-mutation audit failure")
            return original_append(**kwargs)

        monkeypatch.setattr(audit, "append", fail_completion)
        committed = client.post("/api/admin/tenants", headers={**ORIGIN, "X-Request-ID": "audit-commit-123"}, json={
            "slug": "org-committed", "display_name": "Committed",
        })
        assert committed.status_code == 201, committed.text
        assert committed.headers["X-Admin-Audit-Status"] == "completion-pending"
        assert len(app.state.tenant_memberships.list_tenants()) == 1
        assert app.state.admin_audit_completion_pending is True
        assert any(row["action"] == "tenant.create_requested" and row["request_id"] == "audit-commit-123" for row in audit.recent(20))
        assert not any(row["action"] == "tenant.create_completed" for row in audit.recent(20))


def test_tenant_api_rejects_cross_site_state_change(tmp_path: Path):
    app = app_with_tenants(tmp_path)
    with TestClient(app) as client:
        client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password"
        }, headers=ORIGIN)
        cross_site = client.post("/api/admin/tenants", json={
            "slug": "ab-team", "display_name": "A"
        }, headers={"Origin": "https://malicious.example", "Sec-Fetch-Site": "cross-site"})
        assert cross_site.status_code == 403
        assert app.state.tenant_memberships.list_tenants() == []


def test_tenant_membership_does_not_grant_cross_user_resource_access(tmp_path: Path):
    import asyncio
    import base64
    import io
    from PIL import Image
    from app.user_console_v104_patch import USER_SESSION_COOKIE

    app = app_with_tenants(tmp_path)
    accounts = app.state.user_accounts
    alice = accounts.register("alice-resource@example.com", "alice-password-123")
    bob = accounts.register("bob-resource@example.com", "bob-password-123")
    charlie = accounts.register("charlie-resource@example.com", "charlie-password-123")
    alice_id, bob_id, charlie_id = alice["user_id"], bob["user_id"], charlie["user_id"]

    async def seed():
        alice_key, alice_token = await app.state.api_keys.create("Alice resource key")
        bob_key, bob_token = await app.state.api_keys.create("Bob resource key")
        charlie_key, charlie_token = await app.state.api_keys.create("Charlie resource key")
        file_a = await app.state.file_store.create(
            filename="alice-private.txt",
            data_base64=base64.b64encode(b"alice-private-bytes").decode("ascii"),
            mime_type="text/plain",
            owner_key_id=alice_key["key_id"],
            purpose="assistants",
        )
        await app.state.telemetry.upsert({
            "request_id": "req_alice_history_0001",
            "api_key_id": alice_key["key_id"],
            "status": "completed",
            "requested_model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })
        await app.state.telemetry.upsert({
            "request_id": "req_bob_history_0001",
            "api_key_id": bob_key["key_id"],
            "status": "completed",
            "requested_model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })
        await app.state.telemetry.upsert({
            "request_id": "req_alice_native_0001",
            "api_key_id": alice_key["key_id"],
            "status": "completed",
            "response_media": [],
        })
        await app.state.telemetry.merge_diagnostics("req_alice_native_0001", {
            "native_snapshot_terminal_complete": True,
            "native_capture_revision": 1,
            "native_final_capture_committed": True,
            "native_chunk_complete": True,
            "native_snapshot_persisted": True,
            "native_reply_scope_verified": True,
        })
        return alice_key, alice_token, bob_key, bob_token, charlie_key, charlie_token, file_a

    alice_key, alice_token, bob_key, bob_token, charlie_key, charlie_token, file_a = asyncio.run(seed())
    accounts.attach_key(alice_id, alice_key["key_id"])
    accounts.attach_key(bob_id, bob_key["key_id"])
    accounts.attach_key(charlie_id, charlie_key["key_id"])

    tenants = app.state.tenant_memberships
    tenant_a = tenants.create("alice-workspace", "Alice Workspace")
    tenant_b = tenants.create("bob-workspace", "Bob Workspace")
    tenants.upsert_member(tenant_a["tenant_id"], alice_id, "owner")
    tenants.upsert_member(tenant_b["tenant_id"], bob_id, "owner")
    tenants.upsert_member(tenant_a["tenant_id"], charlie_id, "member")
    tenants.bind_key(tenant_a["tenant_id"], alice_id, alice_key["key_id"])
    tenants.bind_key(tenant_b["tenant_id"], bob_id, bob_key["key_id"])
    tenants.bind_key(tenant_a["tenant_id"], charlie_id, charlie_key["key_id"])

    image_buffer = io.BytesIO()
    with Image.new("RGB", (2, 2), (20, 80, 160)) as image:
        image.save(image_buffer, format="PNG")
    media = app.state.media_archive.store_image(
        image_buffer.getvalue(), "image/png", alice_key["key_id"],
        "req_alice_native_0001",
    )
    generated = app.state.generated_files.store(
        b"alice generated report", "alice-report.txt", "text/plain",
        alice_key["key_id"], "req_alice_history_0001",
    )

    app.state.user_billing.charges["req_alice_history_0001"] = {
        "request_id": "req_alice_history_0001", "user_id": alice_id,
        "cost_cny_cents": 11, "debited": False, "created_at": "2026-11-13T00:00:00Z",
    }
    app.state.user_billing.charges["req_bob_history_0001"] = {
        "request_id": "req_bob_history_0001", "user_id": bob_id,
        "cost_cny_cents": 7, "debited": False, "created_at": "2026-11-13T00:00:01Z",
    }
    app.state.user_billing._save()

    snapshot = {
        "version": 1, "revision": 1,
        "root": {"tag": "div", "children": [{"tag": "p", "text": "private"}]},
    }
    assert app.state.native_content.publish(
        "req_alice_native_0001", alice_key["key_id"], snapshot,
    )

    alice_session = accounts.create_session(alice_id)
    bob_session = accounts.create_session(bob_id)
    charlie_session = accounts.create_session(charlie_id)

    with TestClient(app) as client:
        client.cookies.set(USER_SESSION_COOKIE, bob_session)
        bob_requests = client.get("/api/user/requests")
        assert bob_requests.status_code == 200
        assert {row["request_id"] for row in bob_requests.json()["data"]} == {
            "req_bob_history_0001",
        }
        bob_billing = client.get("/api/user/billing")
        assert bob_billing.status_code == 200
        assert {row["request_id"] for row in bob_billing.json()["charges"]} == {
            "req_bob_history_0001",
        }
        assert client.get(f"/api/user/keys/{alice_key['key_id']}/secret").status_code == 404

        bob_auth = {"Authorization": f"Bearer {bob_token}"}
        alice_auth = {"Authorization": f"Bearer {alice_token}"}
        assert client.get(f"/v1/files/{file_a.file_id}", headers=bob_auth).status_code == 403
        assert client.get(f"/v1/files/{file_a.file_id}", headers=alice_auth).status_code == 200
        assert client.delete(f"/v1/files/{file_a.file_id}", headers=bob_auth).status_code == 403
        assert app.state.file_store.get(file_a.file_id) is not None

        assert client.get(f"/v1/media/{media['id']}", headers=bob_auth).status_code == 404
        assert client.get(f"/v1/media/{media['id']}", headers=alice_auth).status_code == 200
        assert client.get(
            f"/v1/generated-files/{generated['id']}", headers=bob_auth
        ).status_code == 404
        assert client.get(
            f"/v1/generated-files/{generated['id']}", headers=alice_auth
        ).status_code == 200

        assert client.get(
            "/v1/native-content/req_alice_native_0001", headers=bob_auth
        ).status_code == 404

        # Even a member of Alice's tenant gets no implicit access to Alice's
        # personal files, media, history, billing or native sidecar.
        charlie_auth = {"Authorization": f"Bearer {charlie_token}"}
        assert client.get(f"/v1/files/{file_a.file_id}", headers=charlie_auth).status_code == 403
        assert client.get(f"/v1/media/{media['id']}", headers=charlie_auth).status_code == 404
        assert client.get(
            f"/v1/generated-files/{generated['id']}", headers=charlie_auth
        ).status_code == 404
        assert client.get(
            "/v1/native-content/req_alice_native_0001", headers=charlie_auth
        ).status_code == 404
        client.cookies.set(USER_SESSION_COOKIE, charlie_session)
        assert client.get("/api/user/requests").json()["data"] == []
        assert client.get("/api/user/billing").json()["charges"] == []

        client.cookies.set(USER_SESSION_COOKIE, alice_session)
        alice_requests = client.get("/api/user/requests")
        assert alice_requests.status_code == 200
        assert "req_alice_history_0001" in {
            row["request_id"] for row in alice_requests.json()["data"]
        }
        alice_billing = client.get("/api/user/billing")
        assert {row["request_id"] for row in alice_billing.json()["charges"]} == {
            "req_alice_history_0001",
        }
        assert client.get(
            "/v1/native-content/req_alice_native_0001", headers=alice_auth
        ).status_code == 200


def test_non_master_api_key_cannot_delete_master_owned_file(tmp_path: Path):
    import asyncio
    import base64

    app = app_with_tenants(tmp_path)

    async def seed():
        _key, token = await app.state.api_keys.create("ordinary user key")
        item = await app.state.file_store.create(
            filename="master-owned.txt",
            data_base64=base64.b64encode(b"privileged asset").decode("ascii"),
            mime_type="text/plain",
            owner_key_id="master",
            purpose="chat2api",
        )
        return token, item

    token, item = asyncio.run(seed())
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        # Metadata/read and delete must enforce the same owner boundary. A
        # regular key cannot read a master-owned file and must not be able to
        # destroy it through the delete endpoint either.
        assert client.get(f"/v1/files/{item.file_id}", headers=headers).status_code == 403
        deleted = client.delete(f"/v1/files/{item.file_id}", headers=headers)
        assert deleted.status_code == 403
        assert app.state.file_store.get(item.file_id) is not None
        _stored, payload = app.state.file_store.read(item.file_id)
        assert payload == b"privileged asset"



def test_legacy_owner_invitation_is_revoked_before_membership_acceptance(tmp_path: Path):
    store = TenantMembershipStore(tmp_path)
    tenant = store.create("legacy-owner-invite", "Legacy Owner Invitation")
    store.upsert_member(tenant["tenant_id"], "usr_legacy_owner123", "owner")

    # Simulate an outstanding invitation written by a version that still
    # allowed owner-role invitations.
    invitation, code = store.create_invitation(
        tenant["tenant_id"], "legacy-owner@example.com", "member", "root",
    )
    store.db.execute(
        "UPDATE tenant_invitations SET role='owner' WHERE invitation_id=?",
        (invitation["invitation_id"],),
    )

    with pytest.raises(TenantMembershipError, match="email does not match"):
        store.accept_invitation(
            code, "usr_legacy_invitee123", "attacker@example.com",
        )
    assert next(
        item for item in store.list_invitations(tenant["tenant_id"])
        if item["invitation_id"] == invitation["invitation_id"]
    )["status"] == "issued"

    with pytest.raises(TenantMembershipError, match="role is no longer permitted"):
        store.accept_invitation(
            code, "usr_legacy_invitee123", "legacy-owner@example.com",
        )

    row = next(
        item for item in store.list_invitations(tenant["tenant_id"])
        if item["invitation_id"] == invitation["invitation_id"]
    )
    assert row["status"] == "revoked"
    assert row["revoked_at"]
    assert store.role_for(tenant["tenant_id"], "usr_legacy_invitee123") is None
    store.close()



def test_workspace_owner_transitions_are_enforced_inside_the_write_transaction(tmp_path: Path):
    store = TenantMembershipStore(tmp_path)
    tenant = store.create("owner-transaction-guard", "Owner Transaction Guard")
    tenant_id = tenant["tenant_id"]
    owner_id = "usr_owner_txn12345"
    member_id = "usr_member_txn12345"
    store.upsert_member(tenant_id, owner_id, "owner")
    store.upsert_member(tenant_id, member_id, "member")

    with pytest.raises(TenantMembershipError, match="owner transitions require super_admin"):
        store.upsert_member(
            tenant_id, member_id, "owner", allow_owner_change=False,
        )
    with pytest.raises(TenantMembershipError, match="owner transitions require super_admin"):
        store.upsert_member(
            tenant_id, owner_id, "member", allow_owner_change=False,
        )
    with pytest.raises(TenantMembershipError, match="owner transitions require super_admin"):
        store.remove_member(tenant_id, owner_id, allow_owner_change=False)

    assert store.role_for(tenant_id, owner_id) == "owner"
    assert store.role_for(tenant_id, member_id) == "member"
    store.close()
