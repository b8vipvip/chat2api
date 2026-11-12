from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.plugin_registry import PluginRegistry, PluginRegistryError, validate_manifest
from app.plugin_registry_v1_patch import install_plugin_registry
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def manifest(version="1.0.0"):
    return {
        "id": "sample_tool",
        "name": "Sample Tool",
        "description": "An inert test plugin",
        "publisher": "internal-tests",
        "version": version,
        "tools": [{
            "name": "lookup",
            "description": "Returns data",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        }],
    }


def make_app(tmp_path):
    settings = Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    )
    app = create_app(settings)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_plugin_registry(app)
    return app


def test_registry_is_versioned_transactional_and_inert(tmp_path: Path):
    store = PluginRegistry(tmp_path)
    created = store.add(manifest())
    assert created["trusted"] is False
    assert created["execution_enabled"] is False
    assert created["sha256"]
    assert store.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(PluginRegistryError, match="already exists"):
        store.add(manifest())
    store.add(manifest("1.0.1"))
    assert len(store.list_public_metadata()) == 2
    assert store.get("sample_tool", "1.0.0")["manifest"]["name"] == "Sample Tool"
    with pytest.raises(PermissionError, match="disabled"):
        store.execute_tool(plugin_id="sample_tool", name="lookup")
    store.close()

    reopened = PluginRegistry(tmp_path)
    assert len(reopened.list_public_metadata()) == 2
    reopened.close()


@pytest.mark.parametrize("bad", [
    {"other": True},
    {"id": "bad/id"},
    {"version": "latest"},
    {"tools": []},
    {"tools": [{"name": "lookup", "description": "x", "input_schema": {"$ref": "https://evil.example/schema", "type": "object"}, "output_schema": {"type": "object"}}]},
    {"tools": [{"name": "lookup", "description": "x", "input_schema": {"type": "object", "properties": {"p": {"type": "string", "default": "secret"}}}, "output_schema": {"type": "object"}}]},
    {"source_url": "http://127.0.0.1/"},
])
def test_manifest_rejects_unsafe_or_unsupported_metadata(bad):
    item = {**manifest(), **bad}
    with pytest.raises(PluginRegistryError):
        validate_manifest(item)


def test_plugin_admin_endpoints_use_existing_session_and_csrf_gate(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/admin/plugins").status_code == 401
        assert client.get("/api/client/plugins").json() == {"data": [], "execution_enabled": False}
        assert client.post("/api/admin/plugins", json=manifest(), headers=ORIGIN).status_code == 401

        login = client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-root-password"
        }, headers=ORIGIN)
        assert login.status_code == 200, login.text

        assert client.post("/api/admin/plugins", json=manifest()).status_code == 403
        assert client.post("/api/admin/plugins", json=manifest(), headers={
            "Origin": "https://attacker.example"
        }).status_code == 403
        created = client.post("/api/admin/plugins", json=manifest(), headers=ORIGIN)
        assert created.status_code == 201, created.text
        assert created.json()["plugin"]["execution_enabled"] is False
        assert "manifest" not in json.dumps(client.get("/api/admin/plugins").json())
        assert client.get("/api/admin/plugins/sample_tool/versions/1.0.0").json()["trusted"] is False
        assert client.post("/api/admin/plugins", json=manifest(), headers=ORIGIN).status_code == 409
        assert client.get("/api/client/plugins").json()["data"] == []

        assert client.get("/v1/models").status_code == 401
        events = app.state.admin_audit.recent(10)
        assert any(row["action"] == "plugin.manifest_completed" for row in events)
        assert all("input_schema" not in json.dumps(row) for row in events)
