from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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


def _open_admin_login_dialog(page) -> None:
    page.wait_for_function(
        """() => {
          const gate = document.getElementById("adminLoginGate");
          return !gate || getComputedStyle(gate).display === "none";
        }""",
        timeout=10000,
    )
    login_button = page.locator("#adminSessionOpenLogin")
    if login_button.is_visible():
        login_button.click()
    page.locator("#adminSessionLoginDialog").wait_for(state="visible")


def _login(page, base_url: str, username: str, password: str, role: str) -> None:
    page_errors = []
    console_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    response = page.goto(base_url + "/admin", wait_until="domcontentloaded")
    try:
        page.wait_for_function(
            "() => document.querySelector('#adminSessionState')?.textContent.includes('未登录')",
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
    if "未登录" not in state:
        raise AssertionError(f"unexpected initial admin session state: {state}; page_errors={page_errors}; console_errors={console_errors}")
    _open_admin_login_dialog(page)
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
    assert page.locator("#adminSessionOpenLogin").is_hidden()
    assert page.locator("#adminSessionLogout").is_visible()
    group = page.locator("#web-user-nav-group")
    if group.is_visible():
        if role == "super_admin":
            assert not group.evaluate("(node) => node.open"), "Web 用户端 should be collapsed by default"
        if not group.evaluate("(node) => node.open"):
            group.locator("summary").click()


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

def _server_error(process: subprocess.Popen[str]) -> str:
    try:
        _, stderr = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return "server process did not exit after startup failure"
    return stderr or f"server exited with code {process.returncode}"

def _check_mobile_invitation_registration(browser, admin, base_url):
    policy = "/api/admin/control/client"
    assert _request_status(admin, base_url, "PUT", policy, {"registration_mode": "invite_only"}) == 200
    admin.locator('[data-view="registration-invitations"]').click()
    email = "mobile-invited@example.test"
    admin.locator("#cInviteEmail").fill(email)
    admin.locator("#cInviteCreate").click()
    admin.locator("#cInviteCodePanel").wait_for(state="visible")
    code = admin.locator("#cInviteCode").inner_text()
    assert code
    admin.locator('[data-view="client-policy"]').click()
    admin.locator('[data-view="registration-invitations"]').click()
    assert not admin.locator("#cInviteCode").text_content()
    assert admin.locator("#cInviteCodePanel").is_hidden()

    # Deliver a real issuance response after the administrator has left its view.
    def leave_before_response(route):
        response = route.fetch()
        admin.evaluate("show('client-policy')")
        route.fulfill(response=response)

    pattern = "**/api/admin/control/invitations"
    admin.route(pattern, leave_before_response, times=1)
    delayed_email = "delayed-invitation@example.test"
    admin.locator("#cInviteEmail").fill(delayed_email)
    with admin.expect_response(lambda r: r.url.endswith("/api/admin/control/invitations")) as delayed:
        admin.locator("#cInviteCreate").click()
    assert delayed.value.status == 201
    admin.locator('[data-view="registration-invitations"]').click()
    admin.locator("#cInviteRows tr").filter(has_text=delayed_email).wait_for()
    assert not admin.locator("#cInviteCode").text_content()

    mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        page = mobile.new_page()
        with page.expect_response(lambda r: r.url.endswith("/assets/chat2api-user-console-v104.js")) as script:
            page.goto(base_url + "/console")
        assert script.value.ok
        page.get_by_role("button", name="注册", exact=True).click()
        form = page.locator("#registerForm")
        form.get_by_label("邮箱").fill(email)
        form.get_by_label("密码").fill("mobile-registration-password")
        form.get_by_label("邀请代码").fill(code)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        with page.expect_response(lambda r: r.url.endswith("/api/user/register")) as registered:
            form.get_by_role("button", name="创建账户").click()
        assert registered.value.status == 200
        page.locator("#appShell").wait_for(state="visible")
        assert page.request.get(base_url + "/api/user/models").ok
        assert not form.get_by_label("邀请代码").input_value()
        admin.locator("#cInviteReload").click()
        admin.locator("#cInviteRows tr").filter(has_text=email).get_by_text("consumed", exact=True).wait_for()
    finally:
        mobile.close()
        assert _request_status(admin, base_url, "PUT", policy, {"registration_mode": "open"}) == 200


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
                _check_mobile_invitation_registration(browser, root_page, base_url)

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
                    "super_admin": {"control-admins", "client-policy", "registration-invitations", "tenants", "model-entitlements", "plugins", "quota-ledger", "user-pricing", "user-payments", "admin-users"},
                    "user_admin": {"registration-invitations", "admin-users"},
                    "billing_admin": {"quota-ledger", "user-pricing", "user-payments"},
                    "support": {"model-entitlements", "admin-users"},
                    "system_admin": {"client-policy", "registration-invitations", "tenants", "model-entitlements", "quota-ledger", "admin-users"},
                    "plugin_admin": {"plugins"},
                    "api_key_admin": {"quota-ledger"},
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
                    if target_view == "api-keys":
                        page.evaluate("show('api-keys')")
                    else:
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
                        ("GET", "/api/admin/users/missing/keys", 403),
                        ("GET", "/api/admin/tenants", 200),
                        ("GET", "/api/admin/model-entitlements", 200),
                        ("GET", "/api/admin/user-payments", 403),
                    ],
                    "plugin_admin": [
                        ("GET", "/api/admin/plugins", 200),
                        ("GET", "/api/admin/tenants", 403),
                    ],
                    "api_key_admin": [
                        ("GET", "/api/admin/users/missing/keys", 403),
                        ("PATCH", "/api/admin/users/missing/keys/missing", 403),
                        ("GET", "/api/admin/keys", 200),
                        ("GET", "/api/admin/quota-reservations", 200),
                        ("GET", "/api/admin/keys/missing/secret", 403),
                    ],
                }
                assert _request_status(root_page, base_url, "GET", "/api/admin/users/missing/keys") == 404

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
                    "() => document.querySelector('#adminSessionState')?.textContent.includes('未登录')",
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
                    "() => document.querySelector('#adminSessionState')?.textContent.includes('未登录')",
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


def test_control_plane_admin_console_enforces_delegated_role_in_browser(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    root_username = "c_track_root"
    root_password = "c-track-root-password-2026"
    support_username = "support_agent"
    support_password = "support-agent-password-2026"
    billing_username = "billing_agent"
    billing_password = "billing-agent-password-2026"
    user_username = "user_operator"
    user_password = "user-operator-password-2026"

    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated-control-plane-data")
    env["CHAT2API_API_KEY"] = "c-track-browser-e2e-master-key"
    env["CHAT2API_ADMIN_USERNAME"] = root_username
    env["CHAT2API_ADMIN_PASSWORD"] = root_password
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        env.pop(name, None)

    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.entry:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "error",
        ],
        cwd=repository_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = False
        for _ in range(120):
            if process.poll() is not None:
                raise AssertionError("production app.entry server failed to start: " + _server_error(process))
            try:
                with urlopen(base_url + "/admin", timeout=1) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (OSError, URLError):
                pass
            time.sleep(0.25)
        assert ready, "production app.entry server did not become ready within 30 seconds"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context()
            try:
                page = context.new_page()
                page_errors: list[str] = []
                auth_responses: list[dict[str, object]] = []
                page.on("pageerror", lambda error: page_errors.append(error.stack or str(error)))
                page.on(
                    "response",
                    lambda response: auth_responses.append({
                        "path": response.url.split("?")[0],
                        "status": response.status,
                    }) if "/api/admin/auth/" in response.url else None,
                )
                page.goto(base_url + "/admin", wait_until="domcontentloaded")
                page.locator("#adminSessionState").wait_for(state="visible", timeout=10000)
                page.wait_for_function(
                    "() => window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ === true",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )

                # Authenticate as the environment-managed root and create a real
                # delegated support administrator through the production UI.
                _open_admin_login_dialog(page)
                page.locator("#adminSessionUser").fill(root_username)
                page.locator("#adminSessionPassword").fill(root_password)
                page.locator("#adminSessionLogin").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('super_admin')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.querySelector('[data-view=control-admins]')?.disabled === false",
                    timeout=5000,
                )

                # Mobile viewport and keyboard-only accessibility smoke for the
                # same production session UI and role-aware navigation.
                mobile_context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                    has_touch=True,
                )
                try:
                    mobile_page = mobile_context.new_page()
                    _login(mobile_page, base_url, root_username, root_password, "super_admin")
                    mobile_metrics = mobile_page.evaluate(
                        """() => ({
                          viewport: window.innerWidth,
                          document: document.documentElement.scrollWidth,
                          body: document.body.scrollWidth,
                          loginGate: getComputedStyle(document.getElementById("adminLoginGate")).display,
                          logoutVisible: !document.getElementById("adminSessionLogout").hidden
                        })"""
                    )
                    assert mobile_metrics["document"] <= mobile_metrics["viewport"] + 1, mobile_metrics
                    assert mobile_metrics["body"] <= mobile_metrics["viewport"] + 1, mobile_metrics
                    assert mobile_metrics["loginGate"] == "none", mobile_metrics
                    assert mobile_metrics["logoutVisible"] is True, mobile_metrics

                    rbac_button = mobile_page.get_by_role("button", name="管理员 RBAC")
                    assert rbac_button.is_visible()
                    rbac_button.focus()
                    assert rbac_button.evaluate("(node) => document.activeElement === node")
                    mobile_page.keyboard.press("Enter")
                    mobile_page.wait_for_function(
                        "() => document.getElementById('view-control-admins')?.classList.contains('active')",
                        timeout=5000,
                    )

                    mobile_page.locator("#adminSessionLogout").click()
                    mobile_page.wait_for_function(
                        "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                        timeout=10000,
                    )
                    _open_admin_login_dialog(mobile_page)
                    assert mobile_page.get_by_role("dialog", name="管理员登录").is_visible()
                    assert mobile_page.get_by_label("管理员用户名").is_visible()
                    assert mobile_page.get_by_label("管理员密码").is_visible()
                    mobile_page.keyboard.press("Escape")
                    assert not mobile_page.locator("#adminSessionLoginDialog").evaluate("(node) => node.open")
                finally:
                    mobile_context.close()

                # Exercise the real tenant-list DOM sink with hostile API text.
                # The C-track UI must render it as text, never as executable markup.
                page.evaluate(
                    """() => {
                      window.__c07_xss_fired = false;
                      window.__c07_original_fetch = window.fetch.bind(window);
                      window.fetch = async (input, init = {}) => {
                        const url = typeof input === "string" ? input : input?.url || "";
                        const method = String(init.method || "GET").toUpperCase();
                        if (url === "/api/admin/tenants" && method === "GET") {
                          return new Response(JSON.stringify({data: [{
                            tenant_id: "ten_xss_123456",
                            slug: "xss-fixture",
                            display_name: '<img src=x onerror="window.__c07_xss_fired=true">'
                          }]}), {status: 200, headers: {"Content-Type": "application/json"}});
                        }
                        return window.__c07_original_fetch(input, init);
                      };
                    }"""
                )
                # Web-user views intentionally live in a collapsed second-level menu.
                page.locator("#web-user-nav-group > summary").click()
                page.locator('[data-view="tenants"]').wait_for(state="visible")
                page.locator('[data-view="tenants"]').click()
                page.wait_for_function(
                    "() => document.getElementById('cTenantRows')?.textContent.includes('<img src=x')",
                    timeout=5000,
                )
                assert page.locator("#cTenantRows img").count() == 0
                assert page.evaluate("window.__c07_xss_fired === false")
                page.evaluate(
                    "() => { window.fetch = window.__c07_original_fetch; delete window.__c07_original_fetch; }"
                )

                page.locator('[data-view="control-admins"]').click()
                page.wait_for_function(
                    "() => document.querySelectorAll('#cAdminRole option').length > 0",
                    timeout=10000,
                )
                page.locator("#cAdminName").fill(support_username)
                page.locator("#cAdminPassword").fill(support_password)
                page.locator("#cAdminRole").select_option("support")
                page.locator("#cAdminCreate").click()
                page.wait_for_function(
                    "() => document.getElementById('cAdminStatus')?.textContent.includes('管理员已创建')",
                    timeout=10000,
                )
                assert support_password not in page.locator("#cAdminRows").inner_text()

                page.locator("#cAdminName").fill(billing_username)
                page.locator("#cAdminPassword").fill(billing_password)
                page.locator("#cAdminRole").select_option("billing_admin")
                page.locator("#cAdminCreate").click()
                page.wait_for_function(
                    "() => document.getElementById('cAdminStatus')?.textContent.includes('管理员已创建')",
                    timeout=10000,
                )
                assert billing_password not in page.locator("#cAdminRows").inner_text()

                page.locator("#cAdminName").fill(user_username)
                page.locator("#cAdminPassword").fill(user_password)
                page.locator("#cAdminRole").select_option("user_admin")
                page.locator("#cAdminCreate").click()
                page.wait_for_function(
                    "() => document.getElementById('cAdminStatus')?.textContent.includes('管理员已创建')",
                    timeout=10000,
                )
                assert user_password not in page.locator("#cAdminRows").inner_text()

                page.locator("#adminSessionLogout").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )

                # Simulate a stale root credential left by a previous console
                # session. Delegated login must clear it before legacy requests.
                page.evaluate(
                    """() => {
                      // The production v17 shell replaces the legacy master-key
                      // controls with session identity controls; seed only the
                      // persisted stale credential before delegated login.
                      sessionStorage.setItem("chat2api_admin_key", "stale-root-key");
                    }"""
                )
                _open_admin_login_dialog(page)
                page.locator("#adminSessionUser").fill(support_username)
                page.locator("#adminSessionPassword").fill(support_password)
                page.locator("#adminSessionLogin").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('support_agent')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.adminCommerceRole === 'support' && document.documentElement.dataset.adminCommerceSessionReady === 'true'",
                    timeout=10000,
                )

                assert page.locator("#view-model-entitlements").evaluate("(node) => node.classList.contains('active')")
                assert page.locator('[data-view="model-entitlements"]').is_visible()
                assert page.locator('[data-view="api-keys"]').count() == 0
                assert page.locator('[data-view="control-admins"]').is_hidden()
                assert page.locator('[data-view="plugins"]').is_hidden()
                assert page.locator('[data-view="tenants"]').is_hidden()
                assert page.locator('[data-view="overview"]').is_hidden()
                # Existing Commerce views must remain available according to
                # their role rather than being treated as legacy root-only nav.
                assert page.locator('[data-view="admin-users"]').is_visible()
                assert page.locator('[data-view="user-pricing"]').is_hidden()
                assert page.locator('[data-view="user-payments"]').is_hidden()
                page.evaluate(
                    """() => {
                      window.__c07_users_fetch = window.fetch.bind(window);
                      window.fetch = async (input, init = {}) => {
                        const url = typeof input === "string" ? input : input?.url || "";
                        const method = String(init.method || "GET").toUpperCase();
                        if (url.startsWith("/api/admin/users?") && method === "GET") {
                          return new Response(JSON.stringify({data: [{
                            user_id: "usr_fixture_123456",
                            email: "fixture@example.test",
                            display_name: "Fixture User",
                            enabled: true,
                            created_at: "2026-11-13",
                            key_count: 1,
                            active_sessions: 0,
                            balance_cents: 0
                          }], total: 1}), {status: 200, headers: {"Content-Type": "application/json"}});
                        }
                        return window.__c07_users_fetch(input, init);
                      };
                    }"""
                )
                page.locator('[data-view="admin-users"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-admin-users')?.classList.contains('active')",
                    timeout=5000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminUserStatus')?.textContent.includes('共 1 个用户')",
                    timeout=10000,
                )
                assert page.locator('#adminUsersBody button[data-user-action="keys"]').count() == 1
                assert page.locator("#adminAuditPanel").is_hidden()
                page.evaluate(
                    "() => { window.fetch = window.__c07_users_fetch; delete window.__c07_users_fetch; }"
                )
                # The v17 shell replaces the master-key controls with
                # session identity controls; delegated login must also purge
                # the stale persisted credential.
                assert page.locator("#adminKey").count() == 0
                assert page.locator("#connect").count() == 0
                assert page.evaluate("sessionStorage.getItem('chat2api_admin_key')") is None

                page.locator('[data-view="model-entitlements"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-model-entitlements')?.classList.contains('active')",
                    timeout=5000,
                )

                # UI visibility is not the security boundary: verify the same
                # delegated identity is denied by the production middleware.
                statuses = page.evaluate(
                    """async () => {
                      const read = async (url) => (await fetch(url, {credentials: "same-origin"})).status;
                      const write = async (url, method, body) => (await fetch(url, {
                        method,
                        credentials: "same-origin",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body),
                      })).status;
                      return {
                        modelsRead: await read("/api/admin/model-entitlements"),
                        usersRead: await read("/api/admin/users"),
                        billingRead: await read("/api/admin/user-pricing"),
                        keysSecret: await read("/api/admin/keys/missing/secret"),
                        adminsRead: await read("/api/admin/control/admins"),
                        pluginsRead: await read("/api/admin/plugins"),
                        clientPolicyWrite: await write("/api/admin/control/client", "PUT", {
                          registration_mode: "closed",
                        }),
                      };
                    }"""
                )
                assert statuses == {
                    "modelsRead": 200,
                    "usersRead": 200,
                    "billingRead": 403,
                    "keysSecret": 403,
                    "adminsRead": 403,
                    "pluginsRead": 403,
                    "clientPolicyWrite": 403,
                }

                page.locator("#adminSessionLogout").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )
                _open_admin_login_dialog(page)
                page.locator("#adminSessionUser").fill(billing_username)
                page.locator("#adminSessionPassword").fill(billing_password)
                page.locator("#adminSessionLogin").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('billing_admin')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.adminCommerceRole === 'billing_admin' && document.documentElement.dataset.adminCommerceSessionReady === 'true'",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.adminPaymentRole === 'billing_admin' && document.documentElement.dataset.adminPaymentSessionReady === 'true'",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.querySelector('[data-view=user-pricing]')?.disabled === false",
                    timeout=5000,
                )
                assert page.locator('[data-view="user-pricing"]').is_visible()
                assert page.locator('[data-view="user-payments"]').is_visible()
                assert page.locator('[data-view="admin-users"]').is_hidden()
                assert page.locator('[data-view="model-entitlements"]').is_hidden()
                page.locator('[data-view="user-pricing"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-user-pricing')?.classList.contains('active')",
                    timeout=5000,
                )
                page.locator('[data-view="user-payments"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-user-payments')?.classList.contains('active')",
                    timeout=5000,
                )
                assert page.locator("#payV106PaypalSecret").count() == 1, (
                    "V106 payment form was not mounted; view="
                    + page.locator("#view-user-payments").evaluate("(node) => node.outerHTML")
                    + "; page errors=" + repr(page_errors)
                )
                assert page.locator("#payV106PaypalSecret").is_disabled()
                assert page.locator("#payV106Save").is_disabled()
                billing_statuses = page.evaluate(
                    """async () => {
                      const read = async (url) => (await fetch(url, {credentials: "same-origin"})).status;
                      const write = async (url, body) => (await fetch(url, {
                        method: "PUT",
                        credentials: "same-origin",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body),
                      })).status;
                      return {
                        pricingRead: await read("/api/admin/user-pricing"),
                        paymentsRead: await read("/api/admin/user-payments"),
                        paymentChannelsRead: await read("/api/admin/payment-channels-v106"),
                        paymentSecretsWrite: await write("/api/admin/payment-channels-v106", {}),
                        usersRead: await read("/api/admin/users"),
                      };
                    }"""
                )
                assert billing_statuses == {
                    "pricingRead": 200,
                    "paymentsRead": 200,
                    "paymentChannelsRead": 200,
                    "paymentSecretsWrite": 403,
                    "usersRead": 403,
                }
                assert not page_errors, "Unexpected browser exceptions: " + repr(page_errors)

                page.locator("#adminSessionLogout").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )
                _open_admin_login_dialog(page)
                page.locator("#adminSessionUser").fill(user_username)
                page.locator("#adminSessionPassword").fill(user_password)
                auth_responses.clear()
                page.locator("#adminSessionLogin").click()
                try:
                    page.wait_for_function(
                        "() => document.getElementById('adminSessionState')?.textContent.includes('user_operator')",
                        timeout=10000,
                    )
                except Exception as error:
                    diagnostics = page.evaluate(
                        """() => ({
                          sessionLoginStatus: document.getElementById("adminSessionState")?.textContent || "",
                          loginFormVisibility: document.getElementById("adminSessionUser")?.getClientRects().length ? "visible" : "hidden",
                          commerceRole: document.documentElement.dataset.adminCommerceRole || "",
                          commerceSessionReady: document.documentElement.dataset.adminCommerceSessionReady || "",
                          adminUiReady: window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ === true,
                          loginButtonDisabled: Boolean(document.getElementById("adminSessionLogin")?.disabled),
                          usernameValue: document.getElementById("adminSessionUser")?.value || "",
                          passwordLength: document.getElementById("adminSessionPassword")?.value?.length || 0
                        })"""
                    )
                    raise AssertionError(
                        "delegated user admin login did not hydrate: "
                        + repr({
                            "auth_responses": auth_responses[-8:],
                            "diagnostics": diagnostics,
                            "page_errors": page_errors[-8:],
                        })
                    ) from error
                page.wait_for_function(
                    "() => document.documentElement.dataset.adminCommerceRole === 'user_admin' && document.documentElement.dataset.adminCommerceSessionReady === 'true'",
                    timeout=10000,
                )
                assert page.locator('[data-view="admin-users"]').is_visible()
                assert page.locator('[data-view="registration-invitations"]').is_visible()
                assert page.locator('[data-view="user-pricing"]').is_hidden()
                assert page.locator('[data-view="user-payments"]').is_hidden()
                page.evaluate(
                    """() => {
                      window.__c07_users_fetch = window.fetch.bind(window);
                      window.fetch = async (input, init = {}) => {
                        const url = typeof input === "string" ? input : input?.url || "";
                        const method = String(init.method || "GET").toUpperCase();
                        if (url.startsWith("/api/admin/users?") && method === "GET") {
                          return new Response(JSON.stringify({data: [{
                            user_id: "usr_fixture_123456",
                            email: "fixture@example.test",
                            display_name: "Fixture User",
                            enabled: true,
                            created_at: "2026-11-13",
                            key_count: 1,
                            active_sessions: 0,
                            balance_cents: 0
                          }], total: 1}), {status: 200, headers: {"Content-Type": "application/json"}});
                        }
                        return window.__c07_users_fetch(input, init);
                      };
                    }"""
                )
                page.locator('[data-view="admin-users"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-admin-users')?.classList.contains('active')",
                    timeout=5000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminUserStatus')?.textContent.includes('共 1 个用户')",
                    timeout=10000,
                )
                assert page.locator('#adminUsersBody button[data-user-action="keys"]').count() == 0
                assert page.locator('#adminUsersBody button[data-user-action="toggle"]').count() == 1
                assert page.locator('#adminUsersBody button[data-user-action="revoke"]').count() == 1
                assert page.locator("#adminAuditPanel").is_hidden()
                user_admin_key_read = page.evaluate(
                    """async () => (await fetch(
                      "/api/admin/users/usr_fixture_123456/keys",
                      {credentials: "same-origin"}
                    )).status"""
                )
                assert user_admin_key_read == 403
                page.evaluate(
                    "() => { window.fetch = window.__c07_users_fetch; delete window.__c07_users_fetch; }"
                )
                assert not page_errors, "Unexpected browser exceptions: " + repr(page_errors)
            finally:
                context.close()
                browser.close()
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()
