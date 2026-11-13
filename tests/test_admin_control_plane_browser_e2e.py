from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server_error(process: subprocess.Popen[str]) -> str:
    try:
        _, stderr = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return "server process did not exit after startup failure"
    return stderr or f"server exited with code {process.returncode}"


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
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(base_url + "/admin", wait_until="domcontentloaded")
                page.locator("#adminSessionState").wait_for(state="visible", timeout=10000)
                page.wait_for_function(
                    "() => window.__CHAT2API_CONTROL_PLANE_ADMIN_V1_READY__ === true",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录管理会话')",
                    timeout=10000,
                )

                # Authenticate as the environment-managed root and create a real
                # delegated support administrator through the production UI.
                page.locator("#adminUsername").fill(root_username)
                page.locator("#adminPassword").fill(root_password)
                page.locator("#adminLoginButton").click()
                page.wait_for_function(
                    "() => document.getElementById('adminLoginGate')?.style.display === 'none'",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('super_admin')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.querySelector('[data-view=control-admins]')?.disabled === false",
                    timeout=5000,
                )
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

                page.locator("#adminLogout").click()
                page.wait_for_function(
                    "() => document.getElementById('adminLoginGate')?.style.display === 'flex'",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )

                # Simulate a stale root credential left by a previous console
                # session. Delegated login must clear it before legacy requests.
                page.evaluate(
                    """() => {
                      // The production login gate removes the legacy master-key
                      // input entirely; seed only the persisted stale credential.
                      sessionStorage.setItem("chat2api_admin_key", "stale-root-key");
                    }"""
                )
                page.locator("#adminUsername").fill(support_username)
                page.locator("#adminPassword").fill(support_password)
                page.locator("#adminLoginButton").click()
                page.wait_for_function(
                    "() => document.getElementById('adminLoginGate')?.style.display === 'none'",
                    timeout=10000,
                )
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
                assert page.locator('[data-view="api-keys"]').is_visible()
                assert page.locator('[data-view="control-admins"]').is_hidden()
                assert page.locator('[data-view="plugins"]').is_hidden()
                assert page.locator('[data-view="tenants"]').is_hidden()
                assert page.locator('[data-view="overview"]').is_hidden()
                # Existing Commerce views must remain available according to
                # their role rather than being treated as legacy root-only nav.
                assert page.locator('[data-view="admin-users"]').is_visible()
                assert page.locator('[data-view="user-pricing"]').is_hidden()
                assert page.locator('[data-view="user-payments"]').is_hidden()
                page.locator('[data-view="admin-users"]').click()
                page.wait_for_function(
                    "() => document.getElementById('view-admin-users')?.classList.contains('active')",
                    timeout=5000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminUserStatus')?.textContent.includes('共')",
                    timeout=10000,
                )
                assert page.locator("#adminAuditPanel").is_hidden()
                # The legacy gate replaces the master-key controls with
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

                page.locator("#adminLogout").click()
                page.wait_for_function(
                    "() => document.getElementById('adminLoginGate')?.style.display === 'flex'",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('未登录')",
                    timeout=10000,
                )
                page.locator("#adminUsername").fill(billing_username)
                page.locator("#adminPassword").fill(billing_password)
                page.locator("#adminLoginButton").click()
                page.wait_for_function(
                    "() => document.getElementById('adminSessionState')?.textContent.includes('billing_admin')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.adminCommerceRole === 'billing_admin' && document.documentElement.dataset.adminCommerceSessionReady === 'true'",
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
                assert page.locator("#commercePayKey").count() == 1, (
                    "Commerce payment form was not mounted; view="
                    + page.locator("#view-user-payments").evaluate("(node) => node.outerHTML")
                    + "; page errors=" + repr(page_errors)
                )
                assert page.locator("#commercePayKey").is_disabled()
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
                        paymentSecretsWrite: await write("/api/admin/user-payments", {}),
                        usersRead: await read("/api/admin/users"),
                      };
                    }"""
                )
                assert billing_statuses == {
                    "pricingRead": 200,
                    "paymentsRead": 200,
                    "paymentSecretsWrite": 403,
                    "usersRead": 403,
                }
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
