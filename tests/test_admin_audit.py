from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.admin_audit import AdminAuditError, AdminAuditStore


def test_admin_audit_is_append_only_hash_chained_and_private(tmp_path: Path) -> None:
    store = AdminAuditStore(tmp_path)
    first = store.append(
        actor="owner",
        action="user.status_change_requested",
        target_type="user",
        target_id="usr_123",
        request_id="req_123",
        details={"enabled": False},
    )
    second = store.append(
        actor="owner",
        action="user.status_change_completed",
        target_type="user",
        target_id="usr_123",
        details={"enabled": False, "revoked_sessions": 2},
    )

    rows = store.recent(10)
    assert [row["event_id"] for row in rows] == [second["event_id"], first["event_id"]]
    assert second["previous_hash"] == first["event_hash"]
    assert first["result"] == "requested"
    assert second["result"] == "completed"
    assert first["policy_version"] == 1
    assert len(first["event_hash"]) == 64
    assert store.path.stat().st_mode & 0o777 == 0o600
    serialized = store.path.read_text(encoding="utf-8")
    assert "password" not in serialized.lower()
    assert "session_token" not in serialized.lower()


def test_admin_audit_read_restores_private_permissions(tmp_path: Path) -> None:
    store = AdminAuditStore(tmp_path)
    store.append(
        actor="owner",
        action="user.status_change_completed",
        target_type="user",
        target_id="usr_123",
        details={"enabled": False},
    )
    store.path.chmod(0o644)

    assert store.recent()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_admin_audit_detects_modified_event_payload(tmp_path: Path) -> None:
    store = AdminAuditStore(tmp_path)
    store.append(
        actor="owner",
        action="user.sessions_revoke_completed",
        target_type="user",
        target_id="usr_123",
        details={"revoked_sessions": 1},
    )
    rows = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    rows[0]["details"]["revoked_sessions"] = 99
    store.path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AdminAuditError, match="hash chain failed"):
        store.recent()


@pytest.mark.parametrize("limit", [0, -1, 201, True, "10"])
def test_admin_audit_rejects_invalid_recent_limit(tmp_path: Path, limit: object) -> None:
    with pytest.raises(ValueError, match="between 1 and 200"):
        AdminAuditStore(tmp_path).recent(limit)  # type: ignore[arg-type]



def test_admin_audit_redacts_nested_secrets_before_persisting_and_hashing(tmp_path: Path) -> None:
    store = AdminAuditStore(tmp_path)
    secrets_in_fixture = (
        "raw-api-key-value",
        "raw-password-value",
        "Bearer raw-access-token",
        "raw-cookie-value",
        "raw-invitation-code",
        "raw-client-secret",
    )
    row = store.append(
        actor="owner",
        action="security.policy_updated",
        target_type="control_plane",
        target_id="policy_1",
        details={
            "apiKey": secrets_in_fixture[0],
            "password": secrets_in_fixture[1],
            "refresh_token": secrets_in_fixture[2],
            "nested": {
                "Authorization": "Bearer raw-access-token",
                "cookie": secrets_in_fixture[3],
                "invite_code": secrets_in_fixture[4],
                "key_id": "key_safe_identifier",
                "email": "operator@example.test",
            },
            "plugins": [{"clientSecret": secrets_in_fixture[5], "enabled": True}],
        },
    )

    serialized = store.path.read_text(encoding="utf-8")
    for secret in secrets_in_fixture:
        assert secret not in serialized
    assert row["details"]["apiKey"] == "[REDACTED]"
    assert row["details"]["password"] == "[REDACTED]"
    assert row["details"]["refresh_token"] == "[REDACTED]"
    assert row["details"]["nested"]["Authorization"] == "[REDACTED]"
    assert row["details"]["nested"]["cookie"] == "[REDACTED]"
    assert row["details"]["nested"]["invite_code"] == "[REDACTED]"
    assert row["details"]["nested"]["key_id"] == "key_safe_identifier"
    assert row["details"]["nested"]["email"] == "operator@example.test"
    assert row["details"]["plugins"][0]["clientSecret"] == "[REDACTED]"
    assert store.recent()[0]["event_hash"] == row["event_hash"]


def test_admin_audit_read_redacts_legacy_secret_fields_without_rewriting_hash_chain(tmp_path: Path) -> None:
    store = AdminAuditStore(tmp_path)
    payload = {
        "event_id": "aud_legacy_secret_record",
        "timestamp": "2026-11-17T00:00:00+00:00",
        "actor": "owner",
        "action": "legacy.policy_updated",
        "target_type": "control_plane",
        "target_id": "policy_legacy",
        "request_id": "req_legacy_audit",
        "result": "completed",
        "policy_version": 1,
        "details": {
            "password": "legacy-password-must-not-export",
            "nested": {"api_key": "legacy-api-key-must-not-export"},
            "key_id": "key_safe_identifier",
        },
        "previous_hash": store.ZERO_HASH,
    }
    event_hash = hashlib.sha256(store._canonical(payload)).hexdigest()
    raw_row = {**payload, "event_hash": event_hash}
    store.path.write_text(json.dumps(raw_row, ensure_ascii=False) + "\\n", encoding="utf-8")

    visible = store.recent()
    assert visible[0]["details"]["password"] == "[REDACTED]"
    assert visible[0]["details"]["nested"]["api_key"] == "[REDACTED]"
    assert visible[0]["details"]["key_id"] == "key_safe_identifier"
    assert visible[0]["details_redacted"] is True
    assert "legacy-password-must-not-export" not in json.dumps(visible)
    assert "legacy-api-key-must-not-export" not in json.dumps(visible)
    # Preserve the original hash-chain bytes for offline forensic verification.
    assert "legacy-password-must-not-export" in store.path.read_text(encoding="utf-8")
