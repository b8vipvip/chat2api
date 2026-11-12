from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.quota_reservation_admin_v1_patch import install_quota_reservation_admin_v1
from app.quota_reservations import QuotaReservationStore
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def test_admin_reservation_api_requires_session_and_audits_reconcile(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    ))
    install_v17_patch(app)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    app.state.quota_reservations.reserve(
        "req_pending", "key_1", 12, quota_limit=100, completed_tokens=0
    )
    install_quota_reservation_admin_v1(app)

    with TestClient(app) as client:
        assert client.get("/api/admin/quota-reservations").status_code == 401
        login = client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-root-password",
        }, headers=ORIGIN)
        assert login.status_code == 200
        listed = client.get("/api/admin/quota-reservations")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"][0]["request_id"] == "req_pending"
        assert listed.json()["data"][0]["state"] == "reserved"
        assert listed.json()["runtime_integration_enabled"] is False
        assert listed.json()["accounting_owner"] == "ApiKeyStore.account_completed_usage"

        assert client.post("/api/admin/quota-reservations/reconcile").status_code == 403
        reconciled = client.post("/api/admin/quota-reservations/reconcile", headers=ORIGIN)
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["settled"] == 0
        invalid = client.get("/api/admin/quota-reservations?state=unknown")
        assert invalid.status_code == 400

        events = app.state.admin_audit.recent(10)
        assert any(row["action"] == "quota.reservation_reconcile_completed" for row in events)


def test_reservation_list_is_bounded_and_filterable(tmp_path: Path):
    store = QuotaReservationStore(tmp_path)
    store.reserve("req_1", "key_1", 10, quota_limit=100, completed_tokens=0)
    store.reserve("req_2", "key_2", 15, quota_limit=100, completed_tokens=0)
    assert [row["request_id"] for row in store.list_reservations(key_id="key_1")] == ["req_1"]
    assert len(store.list_reservations(state="reserved", limit=1)) == 1
    store.release("req_1", "key_1")
    assert store.list_reservations(state="released")[0]["request_id"] == "req_1"
    store.close()
