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
            def __init__(self, payload):
                self.payload = payload

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
            def __init__(self, payload):
                self.payload = payload

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
                    assert kwargs["params"]["per_page"] == 20
                    return FakeResponse([{
                        "filename": "src/parser.py", "status": "modified",
                        "additions": 3, "deletions": 1, "changes": 4,
                        "sha": "d" * 40,
                        "blob_url": "https://github.com/acme/project/blob/main/src/parser.py",
                        "patch": "@@ -1 +1 @@\\n-old\\n+new",
                    }])
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
                        "html_url": "https://github.com/acme/project/blob/main/docs/hello%20world.md",
                        "sha": "b" * 40,
                    }]})
                if url == "https://api.github.com/repos/acme/project/contents/docs/hello%20world.md":
                    assert kwargs["params"] == {"ref": "main"}
                    raw = b"print('hello')\n"
                    return FakeResponse({
                        "type": "file", "size": len(raw),
                        "content": base64.b64encode(raw).decode("ascii"),
                        "encoding": "base64", "sha": "c" * 40,
                        "html_url": "https://github.com/acme/project/blob/main/docs/hello%20world.md",
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
            ("get_pull_request_files", {"repository": "acme/project", "pull_number": 8}),
            ("list_commits", {"repository": "acme/project", "ref": "main"}),
            ("search_code", {"query": "repo:acme/project parser"}),
            ("get_file_contents", {"repository": "acme/project", "path": "docs/hello world.md", "ref": "main"}),
        ]
        responses = []
        for tool_name, payload in requests:
            response = client.post(
                "/api/user/plugins/github/tools/" + tool_name,
                headers=ORIGIN, json=payload,
            )
            assert response.status_code == 200, (tool_name, response.text)
            responses.append(response.json()["data"])

        assert responses[0][0]["default_branch"] == "main"
        assert responses[1][0]["number"] == 7
        assert responses[1][0]["labels"] == ["bug"]
        assert responses[2][0]["draft"] is True
        assert responses[3][0]["mergeable"] is True
        assert responses[4][0]["filename"] == "src/parser.py"
        assert responses[4][0]["patch"].startswith("@@ -1 +1 @@")
        assert responses[5][0]["sha"] == "a" * 40
        assert responses[6][0]["path"] == "docs/hello world.md"
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
        assert len(fake_clients) == 10
        assert fake_clients[8].calls == []
        assert fake_clients[9].calls == []


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
