from __future__ import annotations

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
