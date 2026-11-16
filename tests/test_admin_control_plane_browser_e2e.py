from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    for _ in range(120):
        if process.poll() is not None:
            raise AssertionError(log_path.read_text(encoding="utf-8", errors="replace"))
        try:
            with urllib.request.urlopen(base_url + "/api/admin/auth/session", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise AssertionError("production ASGI entry did not become ready:\n" + log_path.read_text(encoding="utf-8", errors="replace"))


def _login(page, base_url: str, username: str, password: str, role: str) -> None:
    page_errors = []
    console_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    response = page.goto(base_url + "/admin", wait_until="domcontentloaded")
    try:
        page.wait_for_function(
            "() => { const node = document.querySelector('#adminSessionState'); return !!node && node.textContent !== '未检查管理会话'; }",
            timeout=15000,
        )
    except Exception as exc:
        state = page.locator("#adminSessionState").text_content() if page.locator("#adminSessionState").count() else None
        scripts = page.locator("script").evaluate_all("(nodes) => nodes.map(node => node.src || '[inline]')")
        session_probe = page.request.get(base_url + "/api/admin/auth/session")
        asset_probe = page.request.get(base_url + "/assets/chat2api-control-plane-admin-v1.js")
        diagnostics = {
            "page_status": response.status if response else None,
            "url": page.url,
            "title": page.title(),
            "session_state": state,
            "scripts": scripts,
            "session_status": session_probe.status,
            "session_body": session_probe.text(),
            "asset_status": asset_probe.status,
            "page_errors": page_errors,
            "console_errors": console_errors,
            "body": page.locator("body").inner_text()[:1200],
        }
        raise AssertionError(f"admin session bootstrap did not complete: {diagnostics}") from exc
    state = page.locator("#adminSessionState").text_content() or ""
    if "未登录管理会话" not in state:
        raise AssertionError(f"unexpected initial admin session state: {state}; page_errors={page_errors}; console_errors={console_errors}")
    page.locator("#adminSessionUser").fill(username)
    page.locator("#adminSessionPassword").fill(password)
    page.locator("#adminSessionLogin").click()
    page.wait_for_function(
        "role => document.querySelector('#adminSessionState')?.textContent.includes(role)",
        arg=role,
        timeout=20000,
    )
    try:
        page.wait_for_function(
            "role => document.documentElement.dataset.adminCommerceSessionReady === 'true' && document.documentElement.dataset.adminCommerceRole === role",
            arg=role,
            timeout=20000,
        )
    except Exception as exc:
        diagnostics = page.evaluate(
            """async () => ({
              sessionState: document.querySelector('#adminSessionState')?.textContent || "",
              adminV17Authenticated: document.documentElement.dataset.adminV17Authenticated || "",
              commerceRole: document.documentElement.dataset.adminCommerceRole || "",
              commerceSessionReady: document.documentElement.dataset.adminCommerceSessionReady || "",
              session: await fetch('/api/admin/auth/session', {credentials:'same-origin', cache:'no-store'}).then(r => r.json()).catch(error => ({error:String(error)})),
              scripts: Array.from(document.scripts).map(script => script.src || '[inline]'),
              adminUsersButton: document.querySelector('button[data-view="admin-users"]')?.outerHTML || null,
              loginButtonDisabled: Boolean(document.querySelector('#adminSessionLogin')?.disabled),
              controlPlaneReady: window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ === true,
              body: document.body.innerText.slice(0, 1400)
            })"""
        )
        raise AssertionError(
            f"commerce role synchronization did not complete for {role}: "
            + repr({"diagnostics": diagnostics, "page_errors": page_errors, "console_errors": console_errors})
        ) from exc
    page.wait_for_function(
        "() => document.documentElement.dataset.adminV17Authenticated === 'true'",
        timeout=10000,
    )


def _request_status(page, base_url: str, method: str, path: str, data=None, *, origin: str | None = None, fetch_site: str = "same-origin") -> int:
    headers = {}
    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        headers = {
            "Origin": origin or base_url,
            "Sec-Fetch-Site": fetch_site,
        }
    response = page.request.fetch(
        base_url + path,
        method=method.upper(),
        data=data,
        headers=headers,
        timeout=15000,
    )
    return response.status


def test_delegated_admin_browser_navigation_permissions_csrf_and_revocation(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "browser-e2e-data"
    data_dir.mkdir()
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    root_username = "e2e_root"
    root_password = "strong-e2e-root-password"
    log_path = tmp_path / "uvicorn-browser-e2e.log"
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(data_dir)
    env["CHAT2API_API_KEY"] = "browser-e2e-legacy-migration-key"
    env["CHAT2API_ADMIN_USERNAME"] = root_username
    env["CHAT2API_ADMIN_PASSWORD"] = root_password
    env["CHAT2API_HOST"] = "127.0.0.1"
    env["CHAT2API_PORT"] = str(port)
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        env.pop(name, None)

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.entry:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=repository_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(base_url, process, log_path)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                root_context = browser.new_context()
                root_page = root_context.new_page()
                _login(root_page, base_url, root_username, root_password, "super_admin")

                accounts = {
                    "user_admin": ("e2e_users", "strong-e2e-users-password"),
                    "billing_admin": ("e2e_billing", "strong-e2e-billing-password"),
                    "support": ("e2e_support", "strong-e2e-support-password"),
                    "system_admin": ("e2e_system", "strong-e2e-system-password"),
                    "plugin_admin": ("e2e_plugins", "strong-e2e-plugins-password"),
                    "api_key_admin": ("e2e_keys", "strong-e2e-keys-password"),
                }
                for role, (username, password) in accounts.items():
                    response = root_page.request.post(
                        base_url + "/api/admin/control/admins",
                        data={"username": username, "password": password, "role": role},
                        headers={"Origin": base_url, "Sec-Fetch-Site": "same-origin"},
                    )
                    assert response.status == 201, (role, response.status, response.text())

                visible_views = {
                    "super_admin": {"control-admins", "client-policy", "registration-invitations", "tenants", "model-entitlements", "plugins", "quota-ledger", "api-keys", "user-pricing", "user-payments", "admin-users"},
                    "user_admin": {"registration-invitations", "admin-users"},
                    "billing_admin": {"quota-ledger", "user-pricing", "user-payments"},
                    "support": {"model-entitlements", "api-keys", "admin-users"},
                    "system_admin": {"client-policy", "registration-invitations", "tenants", "model-entitlements", "quota-ledger", "admin-users"},
                    "plugin_admin": {"plugins"},
                    "api_key_admin": {"quota-ledger", "api-keys"},
                }
                default_views = {
                    "super_admin": "control-admins",
                    "user_admin": "admin-users",
                    "billing_admin": "user-pricing",
                    "support": "model-entitlements",
                    "system_admin": "tenants",
                    "plugin_admin": "plugins",
                    "api_key_admin": "api-keys",
                }
                all_views = tuple(visible_views["super_admin"])
                pages = {}
                contexts = {}

                for role, (username, password) in accounts.items():
                    context = browser.new_context()
                    page = context.new_page()
                    _login(page, base_url, username, password, role)
                    contexts[role] = context
                    pages[role] = page
                    for view in all_views:
                        button = page.locator(f'.nav button[data-view="{view}"]')
                        assert button.count() == 1, (role, view)
                        assert button.is_visible() is (view in visible_views[role]), (role, view)
                    target_view = default_views[role]
                    page_errors = []
                    console_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                    page.locator(f'.nav button[data-view="{target_view}"]').click()
                    try:
                        page.wait_for_function(
                            "view => document.querySelector('#view-' + view)?.classList.contains('active')",
                            arg=target_view,
                            timeout=10000,
                        )
                    except Exception as exc:
                        diagnostics = page.evaluate(
                            """view => ({
                              targetView: view,
                              hash: location.hash,
                              sessionState: document.querySelector('#adminSessionState')?.textContent || "",
                              commerceRole: document.documentElement.dataset.adminCommerceRole || "",
                              commerceReady: document.documentElement.dataset.adminCommerceSessionReady || "",
                              controlPlaneReady: window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ === true,
                              targetExists: !!document.querySelector('#view-' + view),
                              targetClass: document.querySelector('#view-' + view)?.className || null,
                              activeViews: Array.from(document.querySelectorAll('.view.active')).map(node => node.id),
                              navButton: document.querySelector('.nav button[data-view="' + view + '"]')?.outerHTML || null,
                              pageTitle: document.querySelector('#pageTitle')?.textContent || "",
                              showType: typeof window.show,
                              body: document.body.innerText.slice(0, 900)
                            })""",
                            target_view,
                        )
                        raise AssertionError(
                            f"admin navigation did not activate view for role={role}: "
                            + repr({"diagnostics": diagnostics, "page_errors": page_errors, "console_errors": console_errors})
                        ) from exc

                expectations = {
                    "user_admin": [
                        ("GET", "/api/admin/users", 200),
                        ("GET", "/api/admin/users/missing/keys", 403),
                        ("PATCH", "/api/admin/users/missing/keys/missing", 403),
                        ("GET", "/api/admin/keys", 403),
                        ("GET", "/api/admin/user-pricing", 403),
                        ("GET", "/api/admin/control/admins", 403),
                    ],
                    "billing_admin": [
                        ("GET", "/api/admin/user-pricing", 200),
                        ("GET", "/api/admin/user-payments", 200),
                        ("PUT", "/api/admin/user-payments", 403),
                        ("GET", "/api/admin/keys", 403),
                    ],
                    "support": [
                        ("GET", "/api/admin/users/missing/keys", 404),
                        ("PATCH", "/api/admin/users/missing/keys/missing", 403),
                        ("GET", "/api/admin/keys", 200),
                        ("GET", "/api/admin/keys/missing/secret", 403),
                        ("GET", "/api/admin/requests", 403),
                        ("GET", "/api/admin/model-entitlements", 200),
                    ],
                    "system_admin": [
                        ("GET", "/api/admin/tenants", 200),
                        ("GET", "/api/admin/model-entitlements", 200),
                        ("GET", "/api/admin/user-payments", 403),
                    ],
                    "plugin_admin": [
                        ("GET", "/api/admin/plugins", 200),
                        ("GET", "/api/admin/tenants", 403),
                    ],
                    "api_key_admin": [
                        ("PATCH", "/api/admin/users/missing/keys/missing", 404),
                        ("GET", "/api/admin/keys", 200),
                        ("GET", "/api/admin/quota-reservations", 200),
                        ("GET", "/api/admin/keys/missing/secret", 403),
                    ],
                }
                for role, checks in expectations.items():
                    for method, path, expected_status in checks:
                        actual = _request_status(pages[role], base_url, method, path, data={} if method not in {"GET", "HEAD"} else None)
                        assert actual == expected_status, (role, method, path, actual, expected_status)

                user_page = pages["user_admin"]
                user_page.evaluate(
                    """() => {
                      window.__userAdminFetch = window.fetch.bind(window);
                      window.fetch = async (input, init = {}) => {
                        const url = typeof input === "string" ? input : input?.url || "";
                        const method = String(init.method || "GET").toUpperCase();
                        if (url.startsWith("/api/admin/users?") && method === "GET") {
                          return new Response(JSON.stringify({
                            data: [{
                              user_id: "usr_fixture_123456",
                              email: "fixture@example.test",
                              display_name: "Fixture User",
                              enabled: true,
                              created_at: "2026-11-16T00:00:00Z",
                              key_count: 2,
                              active_sessions: 0,
                              balance_cents: 0
                            }],
                            total: 1
                          }), {status: 200, headers: {"Content-Type": "application/json"}});
                        }
                        return window.__userAdminFetch(input, init);
                      };
                    }"""
                )
                user_page.locator('.nav button[data-view="admin-users"]').click()
                user_page.wait_for_function(
                    "() => document.querySelector('#adminUsersBody')?.textContent.includes('fixture@example.test')",
                    timeout=10000,
                )
                assert user_page.locator('#adminUsersBody button[data-user-action="keys"]').count() == 0
                assert user_page.locator("#adminUserKeysPanel").is_hidden()
                user_page.evaluate(
                    "() => { window.fetch = window.__userAdminFetch; delete window.__userAdminFetch; }"
                )

                # A pending user-list response from the old session must not
                # repopulate user data after that session logs out.
                race_context = browser.new_context()
                race_page = race_context.new_page()
                race_username, race_password = accounts["user_admin"]
                _login(race_page, base_url, race_username, race_password, "user_admin")
                race_page.evaluate(
                    """() => {
                      window.__raceOriginalFetch = window.fetch.bind(window);
                      window.__raceResolveUserList = null;
                      window.__raceResponseReleased = false;
                      window.fetch = (input, init = {}) => {
                        const url = typeof input === "string" ? input : input?.url || "";
                        const method = String(init.method || "GET").toUpperCase();
                        if (url.startsWith("/api/admin/users?") && method === "GET") {
                          return new Promise(resolve => {
                            window.__raceResolveUserList = (response) => {
                              resolve(response);
                              window.__raceResponseReleased = true;
                            };
                          });
                        }
                        return window.__raceOriginalFetch(input, init);
                      };
                    }"""
                )
                race_page.locator('.nav button[data-view="admin-users"]').click()
                race_page.wait_for_function(
                    "() => typeof window.__raceResolveUserList === 'function'",
                    timeout=10000,
                )
                race_page.locator("#adminSessionLogout").click()
                race_page.wait_for_function(
                    "() => document.querySelector('#adminSessionState')?.textContent.includes('未登录管理会话')",
                    timeout=10000,
                )
                race_page.evaluate(
                    """() => window.__raceResolveUserList(new Response(JSON.stringify({
                      data: [{
                        user_id: "usr_stale_123456",
                        email: "stale@example.test",
                        display_name: "Stale User",
                        enabled: true,
                        created_at: "2026-11-16T00:00:00Z",
                        key_count: 1,
                        active_sessions: 0,
                        balance_cents: 0
                      }],
                      total: 1
                    }), {status: 200, headers: {"Content-Type": "application/json"}}))"""
                )
                race_page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                )
                assert "stale@example.test" not in race_page.locator("#adminUsersBody").inner_text()
                assert race_page.locator("#adminUserStatus").text_content() == ""
                race_context.close()

                assert _request_status(
                    user_page, base_url, "PATCH", "/api/admin/users/missing",
                    data={"enabled": False},
                ) == 404
                assert _request_status(
                    user_page, base_url, "PATCH", "/api/admin/users/missing",
                    data={"enabled": False},
                    origin="http://evil.example",
                    fetch_site="cross-site",
                ) == 403

                revoked = root_page.request.patch(
                    base_url + "/api/admin/control/admins/e2e_users",
                    data={"enabled": False},
                    headers={"Origin": base_url, "Sec-Fetch-Site": "same-origin"},
                )
                assert revoked.status == 200, revoked.text()
                assert revoked.json()["revoked_sessions"] >= 1
                assert _request_status(user_page, base_url, "GET", "/api/admin/users") == 401
                session = user_page.request.get(base_url + "/api/admin/auth/session")
                assert session.status == 200
                assert session.json()["authenticated"] is False
                user_page.reload(wait_until="domcontentloaded")
                user_page.wait_for_function(
                    "() => document.querySelector('#adminSessionState')?.textContent.includes('未登录管理会话')",
                    timeout=20000,
                )
                assert not user_page.locator('.nav button[data-view="admin-users"]').is_visible()

                for context in contexts.values():
                    context.close()
                root_context.close()
                browser.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
