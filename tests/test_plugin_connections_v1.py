from __future__ import annotations

import base64

import httpx
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.plugin_connections_v1 import PluginOAuthStore, _gmail_body_text, install_plugin_connections_v1
from app.plugin_registry_v1_patch import install_plugin_registry
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
PASSWORD = "web-chat-password-123"


def make_app(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    ))
    install_control_plane_v1(app)
    app.state.control_plane.update_client({"web_chat_enabled": True})
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_plugin_registry(app)
    install_plugin_connections_v1(app)
    return app


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/user/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_plugin_provider_secrets_are_encrypted_and_admin_config_is_session_guarded(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    with TestClient(app) as client:
        assert client.get("/api/admin/plugins/providers").status_code == 401
        assert client.get("/api/user/plugins").status_code == 401

        login = client.post(
            "/api/admin/auth/login",
            json={"username": "owner", "password": "strong-root-password"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text

        denied = client.put(
            "/api/admin/plugins/providers/github",
            json={"enabled": True, "client_id": "gh-client", "client_secret": "gh-secret"},
        )
        assert denied.status_code == 403

        saved = client.put(
            "/api/admin/plugins/providers/github",
            headers=ORIGIN,
            json={
                "enabled": True, "client_id": "gh-client",
                "client_secret": "gh-secret", "private_repos": False,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["provider"]["configured"] is True
        assert saved.json()["provider"]["client_secret_configured"] is True
        assert "gh-secret" not in saved.text
        assert "gh-secret" not in store.path.read_bytes().decode("latin1")
        assert store.path.stat().st_mode & 0o777 == 0o600

        catalog = client.get("/api/client/plugins")
        assert catalog.status_code == 200
        assert [row["id"] for row in catalog.json()["data"]] == ["github"]

        settings = client.get("/api/admin/plugins/providers")
        assert settings.status_code == 200
        assert "gh-secret" not in settings.text
        assert settings.json()["data"][0]["redirect_uri"].endswith(
            "/api/user/plugins/oauth/github/callback"
        )


def test_oauth_state_is_one_time_and_bound_to_the_authenticated_user(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )

    with TestClient(app) as client:
        user = register(client, "plugin-user@example.com")
        plugins = client.get("/api/user/plugins")
        assert plugins.status_code == 200
        github = next(row for row in plugins.json()["data"] if row["id"] == "github")
        assert github["configured"] is True
        assert github["connected"] is False

        started = client.get("/api/user/plugins/oauth/github/start", follow_redirects=False)
        assert started.status_code == 303
        location = urlsplit(started.headers["location"])
        params = parse_qs(location.query)
        assert location.netloc == "github.com"
        assert params["client_id"] == ["gh-client"]
        assert params["redirect_uri"][0].endswith("/api/user/plugins/oauth/github/callback")
        state = params["state"][0]

        verifier = store.consume_state(state, "github", user["user_id"])
        assert verifier and len(verifier) >= 40
        assert store.consume_state(state, "github", user["user_id"]) is None

        second = client.get("/api/user/plugins/oauth/github/start", follow_redirects=False)
        state_two = parse_qs(urlsplit(second.headers["location"]).query)["state"][0]
        assert store.consume_state(state_two, "github", "another-user") is None
        assert store.consume_state(state_two, "github", user["user_id"]) is None


def test_plugin_connections_are_isolated_per_user_and_tokens_never_leave_the_store(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user_a = register(client, "plugin-a@example.com")
        user_b = register(client, "plugin-b@example.com")
        store.save_connection(
            user_a["user_id"], "github", "42", "octocat",
            "user-a-access-token", "", ["read:user", "public_repo"], None,
        )
        raw_db = store.path.read_bytes().decode("latin1")
        assert "user-a-access-token" not in raw_db
        assert store.connection(user_a["user_id"], "github")["access_token"] == "user-a-access-token"

        login_a = client.post(
            "/api/user/login",
            json={"email": "plugin-a@example.com", "password": PASSWORD},
        )
        assert login_a.status_code == 200
        rows_a = client.get("/api/user/plugins").json()["data"]
        assert next(row for row in rows_a if row["id"] == "github")["connected"] is True

        login_b = client.post(
            "/api/user/login",
            json={"email": "plugin-b@example.com", "password": PASSWORD},
        )
        assert login_b.status_code == 200
        rows_b = client.get("/api/user/plugins").json()["data"]
        github_b = next(row for row in rows_b if row["id"] == "github")
        assert github_b["connected"] is False
        assert github_b["account_label"] == ""
        assert store.connection(user_b["user_id"], "github") is None


def test_gmail_body_decoder_prefers_plain_text_and_ignores_attachments():
    plain = base64.urlsafe_b64encode("Hello from Gmail\nSecond line".encode()).decode().rstrip("=")
    html_body = base64.urlsafe_b64encode(b"<html><body><b>HTML fallback</b></body></html>").decode().rstrip("=")
    attachment = base64.urlsafe_b64encode(b"not message text").decode().rstrip("=")
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": plain}},
            {"mimeType": "text/html", "body": {"data": html_body}},
            {"mimeType": "application/pdf", "body": {"data": attachment}},
        ],
    }
    assert _gmail_body_text(payload) == "Hello from Gmail\nSecond line"

    html_only = {"mimeType": "text/html", "body": {"data": html_body}}
    assert _gmail_body_text(html_only) == "HTML fallback"


def test_github_plugin_tool_is_read_only_and_scoped_to_the_connected_user(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )

    with TestClient(app) as client:
        user_a = register(client, "plugin-tool-a@example.com")
        register(client, "plugin-tool-b@example.com")
        store.save_connection(
            user_a["user_id"], "github", "42", "octocat",
            "user-a-access-token", "", ["read:user"], None,
        )

        class FakeResponse:
            def __init__(self, payload, headers=None):
                self.payload = payload
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                assert url == "https://api.github.com/search/repositories"
                assert kwargs["params"]["per_page"] == 10
                assert kwargs["headers"]["Authorization"] == "Bearer user-a-access-token"
                return FakeResponse({
                    "items": [{
                        "full_name": "b8vipvip/chat2api",
                        "description": "ChatGPT-compatible API bridge",
                        "html_url": "https://github.com/b8vipvip/chat2api",
                        "stargazers_count": 123,
                        "language": "Python",
                    }],
                })

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)

        login_a = client.post(
            "/api/user/login",
            json={"email": "plugin-tool-a@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login_a.status_code == 200
        result = client.post(
            "/api/user/plugins/github/tools/search_repositories",
            headers=ORIGIN,
            json={"query": "chat2api"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"][0]["full_name"] == "b8vipvip/chat2api"
        assert "user-a-access-token" not in result.text
        invalid_repository = client.post(
            "/api/user/plugins/github/tools/list_issues",
            headers=ORIGIN,
            json={"repository": "../private"},
        )
        assert invalid_repository.status_code == 422
        dot_repository = client.post(
            "/api/user/plugins/github/tools/list_issues",
            headers=ORIGIN,
            json={"repository": "owner/.."},
        )
        assert dot_repository.status_code == 422

        login_b = client.post(
            "/api/user/login",
            json={"email": "plugin-tool-b@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login_b.status_code == 200
        denied = client.post(
            "/api/user/plugins/github/tools/search_repositories",
            headers=ORIGIN,
            json={"query": "chat2api"},
        )
        assert denied.status_code == 409

@pytest.mark.parametrize("provider_id", ["github", "gmail"])
def test_oauth_callback_persists_the_authenticated_users_provider_connection(
    tmp_path: Path, monkeypatch, provider_id: str,
):
    app = make_app(tmp_path / provider_id)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    client_id = provider_id + "-client"
    access_token = provider_id + "-user-access-token"
    store.save_provider(
        provider_id, enabled=True, client_id=client_id,
        client_secret=provider_id + "-client-secret", private_repos=False,
    )

    with TestClient(app) as client:
        user = register(client, provider_id + "-oauth@example.com")
        started = client.get(
            "/api/user/plugins/oauth/" + provider_id + "/start",
            follow_redirects=False,
        )
        assert started.status_code == 303
        authorization = parse_qs(urlsplit(started.headers["location"]).query)
        state = authorization["state"][0]
        assert authorization["code_challenge_method"] == ["S256"]

        class FakeResponse:
            def __init__(self, payload, headers=None):
                self.payload = payload
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                if provider_id == "github":
                    assert url == "https://github.com/login/oauth/access_token"
                    assert kwargs["data"]["code_verifier"]
                    return FakeResponse({
                        "access_token": access_token,
                        "refresh_token": "github-refresh-token",
                        "expires_in": 3600,
                    })
                assert url == "https://oauth2.googleapis.com/token"
                assert kwargs["data"]["code_verifier"]
                return FakeResponse({
                    "access_token": access_token,
                    "refresh_token": "gmail-refresh-token",
                    "expires_in": 3600,
                    "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
                })

            async def get(self, url, **kwargs):
                if provider_id == "github":
                    assert url == "https://api.github.com/user"
                    return FakeResponse(
                        {"id": 4242, "login": "octocat"},
                        {"X-OAuth-Scopes": "read:user"},
                    )
                assert url == "https://openidconnect.googleapis.com/v1/userinfo"
                return FakeResponse({
                    "sub": "google-subject-4242",
                    "email": provider_id + "-oauth@example.com",
                    "name": "Plugin Test User",
                    "email_verified": True,
                })

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        callback = client.get(
            "/api/user/plugins/oauth/" + provider_id + "/callback",
            params={"state": state, "code": "authorization-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == (
            "/chat?tab=plugins&plugin=connected&provider=" + provider_id
        )

        connection = store.connection(user["user_id"], provider_id)
        assert connection is not None
        assert connection["access_token"] == access_token
        assert connection["refresh_token"]
        catalog = client.get("/api/user/plugins")
        assert catalog.status_code == 200
        row = next(item for item in catalog.json()["data"] if item["id"] == provider_id)
        assert row["connected"] is True
        assert access_token not in catalog.text


def test_github_repository_issue_pull_request_commit_and_file_read_tools(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user = register(client, "github-read-tools@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "github-read-token", "", ["read:user"], None,
        )

        class FakeResponse:
            def __init__(self, payload, headers=None):
                self.payload = payload
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                assert kwargs["headers"]["Authorization"] == "Bearer github-read-token"
                if url == "https://api.github.com/repos/acme/project":
                    return FakeResponse({
                        "full_name": "acme/project", "description": "Demo repo",
                        "html_url": "https://github.com/acme/project", "private": False,
                        "default_branch": "main", "language": "Python",
                        "stargazers_count": 12, "forks_count": 3, "open_issues_count": 4,
                        "updated_at": "2026-11-26T00:00:00Z",
                    })
                if url == "https://api.github.com/repos/acme/project/issues/7":
                    return FakeResponse({
                        "number": 7, "title": "Fix parser", "state": "open",
                        "html_url": "https://github.com/acme/project/issues/7",
                        "body": "Issue body", "labels": [{"name": "bug"}],
                        "created_at": "2026-11-20T00:00:00Z",
                        "updated_at": "2026-11-25T00:00:00Z", "closed_at": None,
                    })
                if url == "https://api.github.com/repos/acme/project/pulls":
                    assert kwargs["params"]["per_page"] == 20
                    return FakeResponse([{
                        "number": 8, "title": "Add parser", "state": "open",
                        "html_url": "https://github.com/acme/project/pull/8",
                        "body": "PR body", "draft": True,
                        "head": {"ref": "feature/parser"}, "base": {"ref": "main"},
                        "updated_at": "2026-11-25T00:00:00Z",
                    }])
                if url == "https://api.github.com/repos/acme/project/pulls/8":
                    return FakeResponse({
                        "number": 8, "title": "Add parser", "state": "open",
                        "html_url": "https://github.com/acme/project/pull/8",
                        "body": "Full PR body", "draft": False, "merged": False,
                        "mergeable": True, "head": {"ref": "feature/parser"},
                        "base": {"ref": "main"}, "changed_files": 2,
                        "additions": 20, "deletions": 3, "commits": 1,
                        "created_at": "2026-11-20T00:00:00Z",
                        "updated_at": "2026-11-25T00:00:00Z",
                    })
                if url == "https://api.github.com/repos/acme/project/pulls/8/files":
                    assert kwargs["params"] == {"per_page": 5, "page": 2}
                    return FakeResponse([{
                        "filename": "src/parser.py", "status": "modified",
                        "additions": 3, "deletions": 1, "changes": 4,
                        "sha": "d" * 40,
                        "blob_url": "https://github.com/acme/project/blob/main/src/parser.py",
                        "patch": "@@ -1 +1 @@\n-old\n+new",
                    }, *[{
                        "filename": f"src/module-{index}.py", "status": "modified",
                        "additions": 1, "deletions": 0, "changes": 1,
                        "sha": str(index) * 40,
                        "blob_url": f"https://github.com/acme/project/blob/main/src/module-{index}.py",
                    } for index in range(2, 6)]], {
                        "Link": '<https://api.github.com/repos/acme/project/pulls/8/files?page=3>; rel="next"',
                    })
                if url == "https://api.github.com/repos/acme/project/commits":
                    assert kwargs["params"]["sha"] == "main"
                    return FakeResponse([{
                        "sha": "a" * 40,
                        "commit": {"message": "Improve parser", "author": {
                            "name": "Developer", "date": "2026-11-25T00:00:00Z",
                        }},
                        "html_url": "https://github.com/acme/project/commit/" + "a" * 40,
                    }])
                if url == "https://api.github.com/search/code":
                    assert kwargs["params"]["q"] == "repo:acme/project parser"
                    return FakeResponse({"items": [{
                        "repository": {"full_name": "acme/project"},
                        "path": "docs/hello world.md",
                        "html_url": "https://github.com/acme/project/blob/feature/parser-fix/docs/hello%20world.md",
                        "sha": "b" * 40,
                    }]})
                if url == "https://api.github.com/repos/acme/project/contents/docs/hello%20world.md":
                    assert kwargs["params"] == {"ref": "feature/parser-fix"}
                    raw = b"print('hello')\n"
                    return FakeResponse({
                        "type": "file", "size": len(raw),
                        "content": base64.b64encode(raw).decode("ascii"),
                        "encoding": "base64", "sha": "c" * 40,
                        "html_url": "https://github.com/acme/project/blob/feature/parser-fix/docs/hello%20world.md",
                    })
                raise AssertionError("unexpected GitHub URL: " + url)

        fake_clients = []
        original = FakeAsyncClient
        class TrackingAsyncClient(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                fake_clients.append(self)

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", TrackingAsyncClient)
        login = client.post(
            "/api/user/login",
            json={"email": "github-read-tools@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        requests = [
            ("get_repository", {"repository": "acme/project"}),
            ("get_issue", {"repository": "acme/project", "issue_number": 7}),
            ("list_pull_requests", {"repository": "acme/project", "state": "open"}),
            ("get_pull_request", {"repository": "acme/project", "pull_number": 8}),
            ("get_pull_request_files", {"repository": "acme/project", "pull_number": 8, "page": 2}),
            ("list_commits", {"repository": "acme/project", "ref": "main"}),
            ("search_code", {"query": "repo:acme/project parser"}),
            ("get_file_contents", {"repository": "acme/project", "path": "docs/hello world.md", "ref": "feature/parser-fix"}),
        ]
        responses = []
        response_payloads = []
        for tool_name, payload in requests:
            response = client.post(
                "/api/user/plugins/github/tools/" + tool_name,
                headers=ORIGIN, json=payload,
            )
            assert response.status_code == 200, (tool_name, response.text)
            payload_json = response.json()
            response_payloads.append(payload_json)
            responses.append(payload_json["data"])

        assert response_payloads[4]["pagination"] == {
            "page": 2, "per_page": 5, "has_more": True,
        }
        assert responses[0][0]["default_branch"] == "main"
        assert responses[1][0]["number"] == 7
        assert responses[1][0]["labels"] == ["bug"]
        assert responses[2][0]["draft"] is True
        assert responses[3][0]["mergeable"] is True
        assert responses[4][0]["filename"] == "src/parser.py"
        assert responses[4][0]["page"] == 2
        assert responses[4][0]["has_more"] is True
        assert responses[4][0]["patch_available"] is True
        assert responses[4][0]["patch"].startswith("@@ -1 +1 @@")
        assert responses[5][0]["sha"] == "a" * 40
        assert responses[6][0]["path"] == "docs/hello world.md"
        assert responses[6][0]["ref"] == "feature/parser-fix"
        assert responses[7][0]["content"] == "print('hello')\n"
        assert len(fake_clients) == 8

        invalid_path = client.post(
            "/api/user/plugins/github/tools/get_file_contents",
            headers=ORIGIN,
            json={"repository": "acme/project", "path": "../secrets.txt"},
        )
        assert invalid_path.status_code == 422
        invalid_number = client.post(
            "/api/user/plugins/github/tools/get_issue",
            headers=ORIGIN,
            json={"repository": "acme/project", "issue_number": True},
        )
        assert invalid_number.status_code == 422
        invalid_page = client.post(
            "/api/user/plugins/github/tools/get_pull_request_files",
            headers=ORIGIN,
            json={"repository": "acme/project", "pull_number": 8, "page": True},
        )
        assert invalid_page.status_code == 422
        assert len(fake_clients) == 11
        assert fake_clients[8].calls == []
        assert fake_clients[9].calls == []
        assert fake_clients[10].calls == []


@pytest.mark.parametrize(
    ("private_repos", "granted_scopes", "expected_mode"),
    [
        (True, ["read:user"], "upgrade"),
        (False, ["read:user", "repo"], "downgrade"),
    ],
)
def test_github_scope_change_requires_reauthorization_and_blocks_tools(
    tmp_path: Path, private_repos: bool, granted_scopes: list[str], expected_mode: str,
):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=private_repos,
    )
    with TestClient(app) as client:
        user = register(client, "github-scope@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "github-scope-token", "", granted_scopes, None,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-scope@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        catalog = client.get("/api/user/plugins")
        assert catalog.status_code == 200
        github = next(row for row in catalog.json()["data"] if row["id"] == "github")
        assert github["reauthorization_required"] is True
        assert github["reauthorization_mode"] == expected_mode
        assert github["scopes"] == granted_scopes

        denied = client.post(
            "/api/user/plugins/github/tools/search_repositories",
            headers=ORIGIN, json={"query": "chat2api"},
        )
        assert denied.status_code == 409
        if expected_mode == "upgrade":
            assert "授予 repo 权限" in denied.json()["detail"]
        else:
            assert "撤销旧 repo 权限" in denied.json()["detail"]


@pytest.mark.parametrize(
    ("private_repos", "granted_scopes"),
    [
        (False, ["read:user"]),
        (True, ["read:user", "repo"]),
    ],
)
def test_github_matching_oauth_scope_does_not_require_reauthorization(
    tmp_path: Path, private_repos: bool, granted_scopes: list[str],
):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=private_repos,
    )
    with TestClient(app) as client:
        user = register(client, "github-scope-match@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "github-scope-token", "", granted_scopes, None,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-scope-match@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200
        catalog = client.get("/api/user/plugins")
        github = next(row for row in catalog.json()["data"] if row["id"] == "github")
        assert github["reauthorization_required"] is False
        assert github["reauthorization_mode"] == ""


def test_github_invalid_oauth_token_does_not_expire_web_chat_login(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user = register(client, "github-invalid-token@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "revoked-github-token", "", ["read:user"], None,
        )

        class UnauthorizedResponse:
            status_code = 401
            headers = {}

            def __init__(self, url: str):
                self.url = url

            def raise_for_status(self):
                request = httpx.Request("GET", self.url)
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError(
                    "GitHub token revoked", request=request, response=response,
                )

            def json(self):
                return {"message": "Bad credentials"}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                return UnauthorizedResponse(url)

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        login = client.post(
            "/api/user/login",
            json={"email": "github-invalid-token@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        failed_tool = client.post(
            "/api/user/plugins/github/tools/search_repositories",
            headers=ORIGIN, json={"query": "chat2api"},
        )
        assert failed_tool.status_code == 409
        assert "GitHub 授权已失效" in failed_tool.json()["detail"]
        assert store.connection(user["user_id"], "github") is None
        refreshed_catalog = client.get("/api/user/plugins")
        github = next(row for row in refreshed_catalog.json()["data"] if row["id"] == "github")
        assert github["connected"] is False

        # A provider-token failure must not clear the separate Web Chat account session.
        session = client.get("/api/user/me")
        assert session.status_code == 200
        assert session.json()["user"]["user_id"] == user["user_id"]


def test_expired_github_token_without_refresh_is_cleared_for_reconnect(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user = register(client, "github-expired-token@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "expired-github-token", "", ["read:user"], 1,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-expired-token@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        failed_tool = client.post(
            "/api/user/plugins/github/tools/search_repositories",
            headers=ORIGIN, json={"query": "chat2api"},
        )
        assert failed_tool.status_code == 409
        assert "插件授权已过期" in failed_tool.json()["detail"]
        assert store.connection(user["user_id"], "github") is None
        assert client.get("/api/user/me").status_code == 200


def test_gmail_invalid_oauth_token_does_not_expire_web_chat_login(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "gmail", enabled=True, client_id="google-client",
        client_secret="google-secret",
    )
    with TestClient(app) as client:
        user = register(client, "gmail-invalid-token@example.com")
        store.save_connection(
            user["user_id"], "gmail", "google-subject", "user@example.com",
            "revoked-gmail-token", "refresh-token", ["gmail.readonly"], None,
        )

        class UnauthorizedResponse:
            status_code = 401
            headers = {}

            def __init__(self, url: str):
                self.url = url

            def raise_for_status(self):
                request = httpx.Request("GET", self.url)
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError(
                    "Gmail token revoked", request=request, response=response,
                )

            def json(self):
                return {"error": {"message": "Invalid Credentials"}}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                return UnauthorizedResponse(url)

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        login = client.post(
            "/api/user/login",
            json={"email": "gmail-invalid-token@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        failed_tool = client.post(
            "/api/user/plugins/gmail/tools/search_messages",
            headers=ORIGIN, json={"query": "from:example.com"},
        )
        assert failed_tool.status_code == 409
        assert "Gmail 授权已失效" in failed_tool.json()["detail"]
        assert store.connection(user["user_id"], "gmail") is None
        assert client.get("/api/user/me").status_code == 200


def test_github_scope_downgrade_keeps_connection_when_remote_revocation_fails(
    tmp_path: Path, monkeypatch,
):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user = register(client, "github-revoke-failure@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "broad-github-token", "", ["read:user", "repo"], None,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-revoke-failure@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        class FailedResponse:
            status_code = 500

            def __init__(self, url: str):
                self.url = url

            def raise_for_status(self):
                request = httpx.Request("DELETE", self.url)
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError(
                    "GitHub revocation unavailable", request=request, response=response,
                )

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def delete(self, url, **kwargs):
                return FailedResponse(url)

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        response = client.post(
            "/api/user/plugins/github/disconnect",
            headers=ORIGIN,
            json={"require_provider_revocation": True},
        )
        assert response.status_code == 503
        assert "已保留当前连接" in response.json()["detail"]
        assert store.connection(user["user_id"], "github") is not None


def test_github_scope_downgrade_disconnects_only_after_confirmed_remote_revocation(
    tmp_path: Path, monkeypatch,
):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user = register(client, "github-revoke-success@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "broad-github-token", "", ["read:user", "repo"], None,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-revoke-success@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        class SuccessResponse:
            status_code = 204

            def raise_for_status(self):
                return None

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def delete(self, url, **kwargs):
                assert url == "https://api.github.com/applications/gh-client/grant"
                assert kwargs["json"] == {"access_token": "broad-github-token"}
                return SuccessResponse()

        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        response = client.post(
            "/api/user/plugins/github/disconnect",
            headers=ORIGIN,
            json={"require_provider_revocation": True},
        )
        assert response.status_code == 200
        assert response.json()["provider_revocation"] == "revoked"
        assert store.connection(user["user_id"], "github") is None



def test_github_read_tools_support_bounded_pagination_and_report_has_more(
    tmp_path: Path, monkeypatch,
):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    calls = []

    class FakeResponse:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            assert kwargs["params"]["page"] == 2
            if url == "https://api.github.com/search/repositories":
                assert kwargs["params"]["per_page"] == 10
                return FakeResponse({
                    "total_count": 21, "incomplete_results": False,
                    "items": [{
                        "full_name": "acme/project", "html_url": "https://github.com/acme/project",
                        "description": "Project", "stargazers_count": 12,
                        "language": "Python", "private": False, "default_branch": "main",
                    }],
                }, {"Link": '<https://api.github.com/search/repositories?page=3>; rel="next"'})
            if url == "https://api.github.com/repos/acme/project/issues":
                assert kwargs["params"]["per_page"] == 20
                return FakeResponse([
                    {"number": 12, "title": "Issue 12", "state": "open", "html_url": "https://github.com/acme/project/issues/12"},
                    {"number": 13, "title": "PR 13", "pull_request": {"url": "https://api.github.com/repos/acme/project/pulls/13"}},
                ], {"Link": '<https://api.github.com/repos/acme/project/issues?page=3>; rel="next"'})
            if url == "https://api.github.com/repos/acme/project/pulls":
                assert kwargs["params"]["per_page"] == 20
                return FakeResponse([
                    {"number": 15, "title": "PR 15", "state": "open", "html_url": "https://github.com/acme/project/pull/15",
                     "draft": False, "head": {"ref": "feature"}, "base": {"ref": "main"}},
                ], {"Link": '<https://api.github.com/repos/acme/project/pulls?page=3>; rel="next"'})
            if url == "https://api.github.com/repos/acme/project/commits":
                assert kwargs["params"]["per_page"] == 20
                assert kwargs["params"]["sha"] == "main"
                return FakeResponse([
                    {"sha": "a" * 40, "commit": {"message": "Commit page 2", "author": {"name": "Dev", "date": "2026-11-26T00:00:00Z"}},
                     "html_url": "https://github.com/acme/project/commit/" + "a" * 40},
                ], {"Link": '<https://api.github.com/repos/acme/project/commits?page=3>; rel="next"'})
            if url == "https://api.github.com/search/code":
                assert kwargs["params"]["per_page"] == 10
                return FakeResponse({
                    "total_count": 31, "incomplete_results": False,
                    "items": [{
                        "repository": {"full_name": "acme/project"}, "path": "src/page_two.py",
                        "html_url": "https://github.com/acme/project/blob/main/src/page_two.py",
                        "sha": "b" * 40,
                    }],
                }, {"Link": '<https://api.github.com/search/code?page=3>; rel="next"'})
            raise AssertionError("unexpected GitHub URL: " + url)

    monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)

    with TestClient(app) as client:
        user = register(client, "github-pagination@example.com")
        store.save_connection(
            user["user_id"], "github", "42", "octocat",
            "user-access-token", "", ["read:user"], None,
        )
        login = client.post(
            "/api/user/login",
            json={"email": "github-pagination@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert login.status_code == 200

        cases = [
            ("search_repositories", {"query": "acme project", "page": 2}, 10),
            ("list_issues", {"repository": "acme/project", "state": "open", "page": 2}, 20),
            ("list_pull_requests", {"repository": "acme/project", "state": "open", "page": 2}, 20),
            ("list_commits", {"repository": "acme/project", "ref": "main", "page": 2}, 20),
            ("search_code", {"query": "repo:acme/project parser", "page": 2}, 10),
        ]
        for tool_name, payload, per_page in cases:
            response = client.post(
                "/api/user/plugins/github/tools/" + tool_name,
                headers=ORIGIN,
                json=payload,
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["pagination"]["page"] == 2
            assert result["pagination"]["per_page"] == per_page
            assert result["pagination"]["has_more"] is True
            if tool_name in {"search_repositories", "search_code"}:
                assert result["pagination"]["total_count"] in {21, 31}
                assert result["pagination"]["incomplete_results"] is False
            if tool_name == "list_issues":
                assert [row["number"] for row in result["data"]] == [12]
        assert len(calls) == 5

        invalid = client.post(
            "/api/user/plugins/github/tools/list_issues",
            headers=ORIGIN,
            json={"repository": "acme/project", "page": True},
        )
        assert invalid.status_code == 422
        assert len(calls) == 5


def test_github_extended_read_tools_are_bounded_and_use_connected_user(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store = app.state.plugin_oauth_store
    store.save_provider("github", enabled=True, client_id="gh-client",
                        client_secret="gh-secret", private_repos=False)
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
            self.headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer own-token"
            calls.append((url, kwargs.get("params")))
            if url.endswith("/branches"):
                return FakeResponse([{"name": "feature/a", "commit": {"sha": "a" * 40}, "protected": True}])
            if url.endswith("/commits/" + "a" * 40):
                assert kwargs.get("params") is None, "commit detail must not pretend to support pagination"
                return FakeResponse({"sha": "a" * 40, "commit": {"message": "Fix"}, "files": [
                    {"filename": f"src/module-{index}.py", "patch": "+fix"}
                    for index in range(6)
                ]})
            if url.endswith("/contents/src"):
                return FakeResponse([{"name": "main.py", "path": "src/main.py", "type": "file", "sha": "b" * 40}])
            return FakeResponse([{"id": 42, "user": {"login": "reviewer"}, "body": "review",
                                  "state": "APPROVED", "path": "main.py", "line": 7,
                                  "html_url": "https://github.com/acme/project/pull/12#discussion_r42"}])

    with TestClient(app) as client:
        user = register(client, "extended-github@example.com")
        store.save_connection(user["user_id"], "github", "42", "octocat",
                              "own-token", "", ["read:user"], None)
        assert client.post("/api/user/login", json={"email": "extended-github@example.com", "password": PASSWORD}, headers=ORIGIN).status_code == 200
        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)
        cases = [
            ("list_issue_comments", {"issue_number": 11}, "/issues/11/comments", "body", "review"),
            ("list_pull_reviews", {"pull_number": 12}, "/pulls/12/reviews", "state", "APPROVED"),
            ("list_pull_review_comments", {"pull_number": 12}, "/pulls/12/comments", "line", 7),
            ("list_pull_comments", {"pull_number": 12}, "/issues/12/comments", "author", "reviewer"),
            ("list_branches", {}, "/branches", "name", "feature/a"),
            ("get_commit", {"sha": "a" * 40}, "/commits/" + "a" * 40, "message", "Fix"),
            ("list_directory", {"path": "src", "ref": "main"}, "/contents/src", "path", "src/main.py"),
        ]
        for tool, args, suffix, field, value in cases:
            response = client.post("/api/user/plugins/github/tools/" + tool, headers=ORIGIN,
                                   json={"repository": "acme/project", **args})
            assert response.status_code == 200, response.text
            assert response.json()["data"][0][field] == value
            assert response.json()["pagination"]["page"] == 1
            if tool == "get_commit":
                commit_row = response.json()["data"][0]
                assert commit_row["files_returned_by_github"] == 6
                assert commit_row["files_included"] == 6
                assert commit_row["files_truncated"] is False
                assert len(commit_row["files"]) == 6
            assert calls[-1][0] == "https://api.github.com/repos/acme/project" + suffix
        assert calls[-1][1] == {"ref": "main"}
        for tool, args in [
            ("list_directory", {"path": "../secret"}),
            ("get_commit", {"sha": "main"}),
            ("list_issue_comments", {"issue_number": True}),
            ("list_branches", {"page": 101}),
        ]:
            response = client.post("/api/user/plugins/github/tools/" + tool, headers=ORIGIN,
                                   json={"repository": "acme/project", **args})
            assert response.status_code == 422, response.text
        assert len(calls) == len(cases)


def test_github_actions_releases_tags_issue_search_and_review_threads(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    store = app.state.plugin_oauth_store
    store.save_provider("github", enabled=True, client_id="gh-client",
                        client_secret="gh-secret", private_repos=False)
    calls = []

    class FakeResponse:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer own-token"
            calls.append(("GET", url, kwargs.get("params")))
            if url.endswith("/search/issues"):
                assert kwargs["params"]["q"] == "repo:acme/project is:open bug"
                return FakeResponse({"total_count": 1, "incomplete_results": False, "items": [{
                    "number": 7, "title": "Fix bug", "state": "open", "html_url": "https://github.com/acme/project/issues/7",
                    "repository_url": "https://api.github.com/repos/acme/project", "body": "Issue body",
                    "labels": [{"name": "bug"}],
                }]})
            if url.endswith("/releases"):
                return FakeResponse([{"id": 9, "name": "v1", "tag_name": "v1.0.0", "draft": False,
                                      "prerelease": False, "body": "Release notes", "html_url": "https://github.com/acme/project/releases/tag/v1.0.0",
                                      "assets": [{"name": "app.zip", "size": 123, "download_count": 4}]}])
            if url.endswith("/tags"):
                return FakeResponse([{"name": "v1.0.0", "commit": {"sha": "a" * 40}}])
            if url.endswith("/actions/runs"):
                return FakeResponse({"total_count": 1, "workflow_runs": [{
                    "id": 987654321, "name": "CI", "display_title": "Build", "run_number": 8,
                    "run_attempt": 1, "event": "push", "status": "completed", "conclusion": "success",
                    "head_branch": "main", "head_sha": "a" * 40, "actor": {"login": "octocat"},
                    "html_url": "https://github.com/acme/project/actions/runs/987654321",
                }]}, {"Link": '<https://api.github.com/next>; rel="next"'})
            if url.endswith("/actions/runs/987654321/jobs"):
                return FakeResponse({"total_count": 1, "jobs": [{
                    "id": 3, "name": "Tests", "status": "completed", "conclusion": "success",
                    "steps": [{"name": "pytest", "number": 1, "status": "completed", "conclusion": "success"}],
                }]})
            raise AssertionError(url)
        async def post(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer own-token"
            calls.append(("POST", url, kwargs.get("json")))
            if "node(id: $threadId)" in kwargs["json"]["query"]:
                variables = kwargs["json"]["variables"]
                assert variables["threadId"] == "PRRT_kw123"
                assert variables["after"] == "comment-next-cursor"
                return FakeResponse({"data": {"node": {
                    "id": "PRRT_kw123", "isResolved": False, "isOutdated": True,
                    "path": "src/main.py", "line": 17, "originalLine": 16,
                    "pullRequest": {"number": 12, "repository": {"nameWithOwner": "acme/project"}},
                    "comments": {
                        "nodes": [{
                            "databaseId": 45, "author": {"login": "maintainer"}, "body": "Fixed in the next commit",
                            "url": "https://github.com/acme/project/pull/12#discussion_r45",
                            "diffHunk": "@@ -1 +1 @@", "replyTo": {"databaseId": 44},
                        }],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }}})
            variables = kwargs["json"]["variables"]
            assert variables["owner"] == "acme" and variables["name"] == "project"
            assert variables["number"] == 12
            if variables.get("after"):
                assert variables["after"] == "opaque-next-cursor"
                return FakeResponse({"data": {"repository": {"pullRequest": {"reviewThreads": {
                    "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None},
                }}}}})
            return FakeResponse({"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [{
                    "id": "PRRT_kw123", "isResolved": False, "isOutdated": True,
                    "path": "src/main.py", "line": 17, "originalLine": 16,
                    "comments": {"nodes": [{
                        "databaseId": 44, "author": {"login": "reviewer"}, "body": "Please fix",
                        "url": "https://github.com/acme/project/pull/12#discussion_r44",
                        "diffHunk": "@@ -1 +1 @@", "replyTo": None,
                    }], "pageInfo": {"hasNextPage": True, "endCursor": "comment-next-cursor"}},
                }],
                "pageInfo": {"hasNextPage": True, "endCursor": "opaque-next-cursor"},
            }}}}})

    with TestClient(app) as client:
        user = register(client, "github-read-expansion@example.com")
        store.save_connection(user["user_id"], "github", "42", "octocat",
                              "own-token", "", ["read:user"], None)
        assert client.post("/api/user/login", json={"email": "github-read-expansion@example.com", "password": PASSWORD}, headers=ORIGIN).status_code == 200
        monkeypatch.setattr("app.plugin_connections_v1.httpx.AsyncClient", FakeAsyncClient)

        def call(tool, payload):
            response = client.post("/api/user/plugins/github/tools/" + tool, headers=ORIGIN, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        issue = call("search_issues", {"query": "is:open bug", "repository": "acme/project"})
        assert issue["data"][0]["number"] == 7
        assert issue["data"][0]["repository"] == "acme/project"
        assert issue["pagination"]["total_count"] == 1
        release = call("list_releases", {"repository": "acme/project"})
        assert release["data"][0]["assets"][0]["name"] == "app.zip"
        tag = call("list_tags", {"repository": "acme/project"})
        assert tag["data"][0]["sha"] == "a" * 40
        runs = call("list_workflow_runs", {"repository": "acme/project", "branch": "main", "status": "completed"})
        assert runs["data"][0]["conclusion"] == "success"
        assert runs["pagination"]["has_more"] is True
        assert runs["pagination"]["total_count"] == 1
        jobs = call("list_workflow_jobs", {"repository": "acme/project", "run_id": 987654321})
        assert jobs["data"][0]["steps"][0]["name"] == "pytest"
        threads = call("list_pull_review_threads", {"repository": "acme/project", "pull_number": 12})
        assert threads["data"][0]["resolved"] is False
        assert threads["data"][0]["comments"][0]["id"] == 44
        assert threads["data"][0]["comments_truncated"] is True
        assert threads["data"][0]["comments_next_cursor"] == "comment-next-cursor"
        assert threads["pagination"]["next_cursor"] == "opaque-next-cursor"
        assert threads["pagination"]["per_page"] == 10
        threads_next = call("list_pull_review_threads", {
            "repository": "acme/project", "pull_number": 12, "cursor": "opaque-next-cursor",
        })
        assert threads_next["pagination"]["has_more"] is False
        assert calls[-1][2]["variables"]["after"] == "opaque-next-cursor"
        comment_page = call("list_pull_review_thread_comments", {
            "repository": "acme/project", "pull_number": 12,
            "thread_id": threads["data"][0]["thread_id"],
            "cursor": threads["data"][0]["comments_next_cursor"],
        })
        assert comment_page["data"][0]["comments"][0]["id"] == 45
        assert comment_page["data"][0]["comments"][0]["in_reply_to_id"] == 44
        assert comment_page["pagination"]["has_more"] is False
        assert comment_page["pagination"]["per_page"] == 8
        wrong_parent = client.post(
            "/api/user/plugins/github/tools/list_pull_review_thread_comments",
            headers=ORIGIN,
            json={"repository": "acme/project", "pull_number": 13,
                  "thread_id": "PRRT_kw123", "cursor": "comment-next-cursor"},
        )
        assert wrong_parent.status_code == 404

        for tool, payload in [
            ("search_issues", {"query": "is:open bug repo:other/project", "repository": "acme/project"}),
            ("list_workflow_runs", {"repository": "acme/project", "status": "invalid"}),
            ("list_workflow_runs", {"repository": "acme/project", "status": []}),
            ("list_workflow_jobs", {"repository": "acme/project", "run_id": True}),
            ("list_pull_review_threads", {"repository": "acme/project", "pull_number": 12, "cursor": "bad\nvalue"}),
            ("list_pull_review_thread_comments", {"repository": "acme/project", "pull_number": 12, "thread_id": "PRRT_kw123"}),
            ("list_pull_review_thread_comments", {"repository": "acme/project", "pull_number": 12, "thread_id": "bad\nthread", "cursor": "cursor"}),
        ]:
            response = client.post("/api/user/plugins/github/tools/" + tool, headers=ORIGIN, json=payload)
            assert response.status_code == 422, response.text
        assert len(calls) == 9
