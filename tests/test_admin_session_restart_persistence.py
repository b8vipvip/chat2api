from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from app.admin_auth import AdminSessionStore


def test_admin_session_survives_restart_without_persisting_raw_token(tmp_path: Path) -> None:
    path = tmp_path / "admin_sessions.json"
    first = AdminSessionStore("owner", "strong-password", ttl_seconds=3600, storage_path=path)

    token = first.create()
    assert first.authenticate(token) is True
    assert path.exists()

    raw = path.read_text(encoding="utf-8")
    assert token not in raw
    assert hashlib.sha256(token.encode("utf-8")).hexdigest() in raw

    restarted = AdminSessionStore("owner", "strong-password", ttl_seconds=3600, storage_path=path)
    assert restarted.authenticate(token) is True

    restarted.revoke(token)
    after_logout = AdminSessionStore("owner", "strong-password", ttl_seconds=3600, storage_path=path)
    assert after_logout.authenticate(token) is False


def test_configured_data_dir_enables_restart_persistence_automatically(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHAT2API_DATA_DIR", str(tmp_path))

    first = AdminSessionStore("owner", "strong-password", ttl_seconds=3600)
    token = first.create()
    expected = tmp_path / "admin_sessions.json"
    assert expected.exists()

    restarted = AdminSessionStore("owner", "strong-password", ttl_seconds=3600)
    assert restarted.authenticate(token) is True


def test_expired_persisted_session_is_not_restored(tmp_path: Path) -> None:
    path = tmp_path / "admin_sessions.json"
    first = AdminSessionStore("owner", "strong-password", ttl_seconds=3600, storage_path=path)
    token = first.create()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["sessions"]) == 1
    payload["sessions"][0]["expires_at"] = time.time() - 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = AdminSessionStore("owner", "strong-password", ttl_seconds=3600, storage_path=path)
    assert restarted.authenticate(token) is False
    assert restarted.sessions == {}
