from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.admin import admin_response
from app.config import Settings
from app.main import create_app
from app.v17_patch import install_v17_patch
from app.mode_management import (
    MODE1_COOKIE_PROXY_AUTHORIZATION_APPROVED,
    MODE1_COOKIE_PROXY_RUNTIME_IMPLEMENTED,
    ModeManagementError,
    ModeManagementStore,
)


def test_mode_management_defaults_to_mode2_and_mode1_switch_off(tmp_path: Path) -> None:
    store = ModeManagementStore(tmp_path)

    state = store.public_state()

    assert state["default_mode"] == "mode2"
    assert state["mode2"]["enabled"] is True
    assert state["mode2"]["is_default"] is True
    assert state["mode1_cookie_proxy"]["requested_enabled"] is False
    assert state["mode1_cookie_proxy"]["effective_enabled"] is False
    assert state["mode1_cookie_proxy"]["authorization_gate"] == "NO-GO"
    assert state["mode1_cookie_proxy"]["runtime_implemented"] is False
    assert MODE1_COOKIE_PROXY_AUTHORIZATION_APPROVED is False
    assert MODE1_COOKIE_PROXY_RUNTIME_IMPLEMENTED is False
    assert not store.path.exists()


def test_mode1_switch_persists_test_intent_but_cannot_enable_runtime(tmp_path: Path) -> None:
    store = ModeManagementStore(tmp_path)

    enabled = store.set_mode1_cookie_proxy_requested(True)
    restarted = ModeManagementStore(tmp_path).public_state()

    assert enabled["mode1_cookie_proxy"]["requested_enabled"] is True
    assert enabled["mode1_cookie_proxy"]["effective_enabled"] is False
    assert enabled["mode1_cookie_proxy"]["status"] == "blocked_no_go"
    assert "NO-GO" in enabled["mode1_cookie_proxy"]["reason"]
    assert restarted["mode1_cookie_proxy"]["requested_enabled"] is True
    assert restarted["mode1_cookie_proxy"]["effective_enabled"] is False

    disabled = store.set_mode1_cookie_proxy_requested(False)
    assert disabled["mode1_cookie_proxy"]["requested_enabled"] is False
    assert disabled["mode1_cookie_proxy"]["effective_enabled"] is False


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", "false", [], {}])
def test_mode1_switch_rejects_non_boolean_values(tmp_path: Path, invalid: object) -> None:
    with pytest.raises(ModeManagementError, match="must be a boolean"):
        ModeManagementStore(tmp_path).set_mode1_cookie_proxy_requested(invalid)  # type: ignore[arg-type]
    assert not (tmp_path / "mode_management.json").exists()


def test_corrupt_mode_settings_fail_closed_without_resetting_to_default(tmp_path: Path) -> None:
    path = tmp_path / "mode_management.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ModeManagementError, match="unreadable or corrupt"):
        ModeManagementStore(tmp_path).public_state()

    assert path.read_text(encoding="utf-8") == "{not-json"


def test_mode_settings_file_is_private_and_versioned(tmp_path: Path) -> None:
    store = ModeManagementStore(tmp_path)
    store.set_mode1_cookie_proxy_requested(True)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["mode1_cookie_proxy"]["requested_enabled"] is True
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_admin_modes_api_requires_master_admin_key_and_persists_only_test_intent(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="mode-test-admin-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    registered_paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/admin/modes" in registered_paths
    assert not any(
        "/mode1" in path.lower() or "cookie-proxy" in path.lower()
        for path in registered_paths
    )

    with TestClient(app) as client:
        assert client.get("/api/admin/modes").status_code == 401

        headers = {"Authorization": "Bearer mode-test-admin-key"}
        mutation_headers = {**headers, "Origin": "http://testserver"}
        initial = client.get("/api/admin/modes", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["default_mode"] == "mode2"
        assert initial.json()["mode1_cookie_proxy"]["requested_enabled"] is False
        assert initial.json()["mode1_cookie_proxy"]["effective_enabled"] is False

        missing_origin = client.put(
            "/api/admin/modes",
            headers=headers,
            json={"mode1_cookie_proxy_requested": True},
        )
        assert missing_origin.status_code == 403

        cross_origin = client.put(
            "/api/admin/modes",
            headers={**headers, "Origin": "https://attacker.example"},
            json={"mode1_cookie_proxy_requested": True},
        )
        assert cross_origin.status_code == 403

        wrong_scheme = client.put(
            "/api/admin/modes",
            headers={**headers, "Origin": "https://testserver"},
            json={"mode1_cookie_proxy_requested": True},
        )
        assert wrong_scheme.status_code == 403

        invalid = client.put(
            "/api/admin/modes",
            headers=mutation_headers,
            json={"mode1_cookie_proxy_requested": True, "effective_enabled": True},
        )
        assert invalid.status_code == 422

        saved = client.put(
            "/api/admin/modes",
            headers=mutation_headers,
            json={"mode1_cookie_proxy_requested": True},
        )
        assert saved.status_code == 200
        assert saved.json()["mode1_cookie_proxy"]["requested_enabled"] is True
        assert saved.json()["mode1_cookie_proxy"]["effective_enabled"] is False

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        state = client.get(
            "/api/admin/modes",
            headers={"Authorization": "Bearer mode-test-admin-key"},
        )
        assert state.status_code == 200
        assert state.json()["mode1_cookie_proxy"]["requested_enabled"] is True
        assert state.json()["mode1_cookie_proxy"]["effective_enabled"] is False


def test_production_admin_session_can_save_mode_intent_without_enabling_proxy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHAT2API_DATA_DIR", str(tmp_path))
    settings = Settings(
        CHAT2API_ADMIN_USERNAME="mode-owner",
        CHAT2API_ADMIN_PASSWORD="strong-test-password",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_v17_patch(app)

    with TestClient(app) as client:
        assert client.get("/api/admin/modes").status_code == 401
        login = client.post(
            "/api/admin/auth/login",
            json={"username": "mode-owner", "password": "strong-test-password"},
        )
        assert login.status_code == 200
        assert "chat2api_admin_session" in client.cookies

        loaded = client.get("/api/admin/modes")
        assert loaded.status_code == 200
        assert loaded.json()["mode1_cookie_proxy"]["requested_enabled"] is False

        saved = client.put(
            "/api/admin/modes",
            headers={"Origin": "http://testserver"},
            json={"mode1_cookie_proxy_requested": True},
        )
        assert saved.status_code == 200
        assert saved.json()["mode1_cookie_proxy"]["requested_enabled"] is True
        assert saved.json()["mode1_cookie_proxy"]["effective_enabled"] is False


def test_admin_mode_management_view_and_switch_are_present_and_default_off() -> None:
    response = admin_response("modes")
    html = response.body.decode("utf-8")

    assert 'data-view="modes">模式管理</button>' in html
    assert 'id="view-modes"' in html
    assert 'id="mode1CookieProxyRequested" type="checkbox"' in html
    assert 'id="saveModeSettings"' in html
    assert 'NO-GO 授权门禁' in html
    assert "不会启动代理" in html
    assert "mode1_cookie_proxy_requested" in html
    assert 'const titles={overview:\'概览\',modes:\'模式管理\'' in html

    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match, "admin page inline script is missing"
    node = shutil.which("node")
    if node:
        checked = subprocess.run(
            [node, "--check", "-"],
            input=match.group(1),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_admin_modes_initial_view_is_allowed() -> None:
    html = admin_response("modes").body.decode("utf-8")
    assert "const INITIAL_VIEW='modes';" in html
