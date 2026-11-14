from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.registration_invitations import InvitationError, InvitationStore
from app.registration_invitations_v1_patch import install_registration_invitations
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def make_app(path: Path):
    app = create_app(Settings(
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
        CHAT2API_DATA_DIR=str(path),
    ))
    install_control_plane_v1(app)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_registration_invitations(app)
    return app


def login(client: TestClient):
    return client.post(
        "/api/admin/auth/login",
        json={"username": "owner", "password": "strong-root-password"},
        headers=ORIGIN,
    )


def test_invitation_email_normalization_and_validation(tmp_path: Path):
    store = InvitationStore(tmp_path)
    created = store.create("Alice+Chat2API@Example.co.uk", "root")
    assert created["invitation"]["email"] == "alice+chat2api@example.co.uk"
    reservation = store.reserve(created["code"], "ALICE+Chat2API@example.co.uk")
    store.consume(reservation)

    for invalid in (
        "alice example@example.com",
        "alice@example",
        "alice@@example.com",
        "alice@example..com",
        "alice@ example.com",
    ):
        with pytest.raises(InvitationError, match="valid email"):
            store.create(invalid, "root")


def test_user_console_clears_one_time_invitation_code_on_auth_transitions():
    source = (
        Path(__file__).resolve().parents[1] / "app" / "user_console_v104.js"
    ).read_text(encoding="utf-8")
    show_auth = source.split("function showAuth", 1)[1].split("function showApp", 1)[0]
    show_app = source.split("function showApp", 1)[1].split("async function loadRegistrationPolicy", 1)[0]
    assert '$("registerInviteCode")' in show_auth
    assert 'inviteInput.value = ""' in show_auth
    assert '$("registerInviteCode")' in show_app
    assert 'inviteInput.value = ""' in show_app


def test_invitation_store_is_email_bound_single_use_and_hash_only(tmp_path: Path):
    store = InvitationStore(tmp_path)
    created = store.create("Alice@example.com", "root", expires_in_seconds=600)
    invitation = created["invitation"]
    code = created["code"]

    raw_db = (tmp_path / "registration_invitations_v1.sqlite3").read_bytes()
    assert code.encode("ascii") not in raw_db
    assert store.list_invitations()[0]["email"] == "alice@example.com"
    assert "token_hash" not in store.list_invitations()[0]
    assert "reservation_id" not in store.list_invitations()[0]

    with pytest.raises(InvitationError):
        store.reserve(code, "mallory@example.com")

    reservation = store.reserve(code, "ALICE@example.com")
    with pytest.raises(InvitationError):
        store.reserve(code, "alice@example.com")

    store.consume(reservation)
    assert store.list_invitations()[0]["status"] == "consumed"
    with pytest.raises(InvitationError):
        store.reserve(code, "alice@example.com")
    assert invitation["status"] == "issued"


def test_revocation_does_not_interrupt_a_reserved_registration(tmp_path: Path):
    store = InvitationStore(tmp_path)
    created = store.create("alice@example.com", "root")
    invitation_id = created["invitation"]["invitation_id"]
    reservation = store.reserve(created["code"], "alice@example.com")
    assert store.revoke(invitation_id) is False
    store.consume(reservation)
    assert store.list_invitations()[0]["status"] == "consumed"


def test_invitation_reservation_is_atomic_under_concurrent_use(tmp_path: Path):
    store = InvitationStore(tmp_path)
    code = store.create("alice@example.com", "root")["code"]

    def attempt():
        try:
            return store.reserve(code, "alice@example.com")
        except InvitationError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: attempt(), range(8)))
    assert sum(value is not None for value in results) == 1


def test_invitation_release_revoke_and_expiry_states(tmp_path: Path, monkeypatch):
    store = InvitationStore(tmp_path)
    released = store.create("alice@example.com", "root", expires_in_seconds=600)
    reservation = store.reserve(released["code"], "alice@example.com")
    assert store.release(reservation) is True
    assert store.list_invitations()[0]["status"] == "issued"

    revoked = store.create("bob@example.com", "root")
    assert store.revoke(revoked["invitation"]["invitation_id"]) is True
    assert store.list_invitations()[0]["status"] == "revoked"
    with pytest.raises(InvitationError):
        store.reserve(revoked["code"], "bob@example.com")

    expiring = store.create("carol@example.com", "root", expires_in_seconds=300)
    original = __import__("time").time
    monkeypatch.setattr("app.registration_invitations.time.time", lambda: original() + 301)
    with pytest.raises(InvitationError):
        store.reserve(expiring["code"], "carol@example.com")
    assert store.list_invitations()[0]["status"] == "expired"


def test_invite_only_registration_consumes_code_only_after_success(tmp_path: Path):
    app = make_app(tmp_path)
    app.state.control_plane.update_client({"registration_mode": "invite_only"})
    with TestClient(app) as client:
        assert login(client).status_code == 200
        issued = client.post(
            "/api/admin/control/invitations",
            json={"email": "alice@example.com", "expires_in_seconds": 3600},
            headers=ORIGIN,
        )
        assert issued.status_code == 201
        assert issued.headers["cache-control"] == "no-store"
        payload = issued.json()
        code = payload["code"]
        invitation_id = payload["invitation"]["invitation_id"]

        listing = client.get("/api/admin/control/invitations")
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        assert code not in json.dumps(listing.json())
        assert "token_hash" not in json.dumps(listing.json())

        wrong_email = client.post("/api/user/register", json={
            "email": "mallory@example.com",
            "password": "strong-password-123",
            "invite_code": code,
        })
        assert wrong_email.status_code == 403

        invalid_password = client.post("/api/user/register", json={
            "email": "alice@example.com",
            "password": "short",
            "invite_code": code,
        })
        assert invalid_password.status_code == 422

        registered = client.post("/api/user/register", json={
            "email": "alice@example.com",
            "password": "strong-password-123",
            "display_name": "Alice",
            "invite_code": code,
        })
        assert registered.status_code == 200
        assert registered.headers["cache-control"] == "no-store"
        assert registered.json()["ok"] is True
        assert app.state.invitations.list_invitations()[0]["status"] == "consumed"

        replay = client.post("/api/user/register", json={
            "email": "alice2@example.com",
            "password": "strong-password-123",
            "invite_code": code,
        })
        assert replay.status_code == 403

        revoke = client.post(
            f"/api/admin/control/invitations/{invitation_id}/revoke",
            headers=ORIGIN,
        )
        assert revoke.status_code == 404


def test_failed_duplicate_registration_releases_invitation_for_admin_recovery(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        # Create an account while registration is open, then switch to invite-only.
        first = client.post("/api/user/register", json={
            "email": "alice@example.com", "password": "strong-password-123"
        })
        assert first.status_code == 200
        app.state.control_plane.update_client({"registration_mode": "invite_only"})
        created = app.state.invitations.create("alice@example.com", "root")
        duplicate = client.post("/api/user/register", json={
            "email": "alice@example.com",
            "password": "strong-password-456",
            "invite_code": created["code"],
        })
        assert duplicate.status_code == 409
        assert app.state.invitations.list_invitations()[0]["status"] == "issued"


def test_invite_only_requires_code_closed_rejects_and_open_remains_compatible(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        app.state.control_plane.update_client({"registration_mode": "invite_only"})
        missing = client.post("/api/user/register", json={
            "email": "alice@example.com", "password": "strong-password-123"
        })
        assert missing.status_code == 403

        app.state.control_plane.update_client({"registration_mode": "closed"})
        closed = client.post("/api/user/register", json={
            "email": "alice@example.com", "password": "strong-password-123"
        })
        assert closed.status_code == 403

        app.state.control_plane.update_client({"registration_mode": "open"})
        opened = client.post("/api/user/register", json={
            "email": "alice@example.com", "password": "strong-password-123"
        })
        assert opened.status_code == 200


def test_delegated_admin_self_password_rotation_revokes_existing_sessions(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert login(client).status_code == 200
        created = client.post(
            "/api/admin/control/admins",
            json={"username": "useradmin", "password": "strong-useradmin-password", "role": "user_admin"},
            headers=ORIGIN,
        )
        assert created.status_code == 201
        client.post("/api/admin/auth/logout", headers=ORIGIN)
        assert client.post(
            "/api/admin/auth/login",
            json={"username": "useradmin", "password": "strong-useradmin-password"},
            headers=ORIGIN,
        ).status_code == 200

        rotated = client.post(
            "/api/admin/control/admins/useradmin/password",
            json={
                "current_password": "strong-useradmin-password",
                "new_password": "rotated-useradmin-password",
            },
            headers=ORIGIN,
        )
        assert rotated.status_code == 200
        assert rotated.json()["revoked_sessions"] >= 1
        assert client.get("/api/admin/control/admins").status_code == 401

        assert client.post(
            "/api/admin/auth/login",
            json={"username": "useradmin", "password": "strong-useradmin-password"},
            headers=ORIGIN,
        ).status_code == 401
        assert client.post(
            "/api/admin/auth/login",
            json={"username": "useradmin", "password": "rotated-useradmin-password"},
            headers=ORIGIN,
        ).status_code == 200

        root_rotation = client.post(
            "/api/admin/control/admins/owner/password",
            json={"new_password": "new-root-password-123"},
            headers=ORIGIN,
        )
        assert root_rotation.status_code == 409


def test_all_delegated_roles_can_rotate_their_own_password(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert login(client).status_code == 200
        created = client.post(
            "/api/admin/control/admins",
            json={"username": "pluginagent", "password": "strong-plugin-password", "role": "plugin_admin"},
            headers=ORIGIN,
        )
        assert created.status_code == 201
        client.post("/api/admin/auth/logout", headers=ORIGIN)
        assert client.post(
            "/api/admin/auth/login",
            json={"username": "pluginagent", "password": "strong-plugin-password"},
            headers=ORIGIN,
        ).status_code == 200
        rotated = client.post(
            "/api/admin/control/self/password",
            json={
                "current_password": "strong-plugin-password",
                "new_password": "rotated-plugin-password",
            },
            headers=ORIGIN,
        )
        assert rotated.status_code == 200
        assert rotated.json()["revoked_sessions"] >= 1
        assert client.post(
            "/api/admin/auth/login",
            json={"username": "pluginagent", "password": "rotated-plugin-password"},
            headers=ORIGIN,
        ).status_code == 200


def test_delegated_user_admin_can_issue_invitation_but_not_root_only_routes(tmp_path: Path):
    app = make_app(tmp_path)
    app.state.control_plane.update_client({"registration_mode": "invite_only"})
    with TestClient(app) as client:
        assert login(client).status_code == 200
        created_admin = client.post(
            "/api/admin/control/admins",
            json={"username": "useradmin", "password": "strong-useradmin-password", "role": "user_admin"},
            headers=ORIGIN,
        )
        assert created_admin.status_code == 201
        client.post("/api/admin/auth/logout", headers=ORIGIN)
        delegated = client.post(
            "/api/admin/auth/login",
            json={"username": "useradmin", "password": "strong-useradmin-password"},
            headers=ORIGIN,
        )
        assert delegated.status_code == 200

        issued = client.post(
            "/api/admin/control/invitations",
            json={"email": "member@example.com"},
            headers=ORIGIN,
        )
        assert issued.status_code == 201

        secret = client.get("/api/admin/keys/master/secret")
        assert secret.status_code in {401, 403, 404}
