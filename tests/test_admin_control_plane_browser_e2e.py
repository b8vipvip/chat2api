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
                page.goto(base_url + "/admin", wait_until="domcontentloaded")
                page.locator("#adminSessionState").wait_for(state="visible", timeout=10000)

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
                      sessionStorage.setItem("chat2api_admin_key", "stale-root-key");
                      document.getElementById("adminKey").value = "stale-root-key";
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

                assert page.locator('[data-view="model-entitlements"]').is_visible()
                assert page.locator('[data-view="api-keys"]').is_visible()
                assert page.locator('[data-view="control-admins"]').is_hidden()
                assert page.locator('[data-view="plugins"]').is_hidden()
                assert page.locator('[data-view="tenants"]').is_hidden()
                assert page.locator('[data-view="overview"]').is_hidden()
                assert page.locator("#adminKey").is_disabled()
                assert page.locator("#adminKey").input_value() == ""
                assert page.locator("#connect").is_hidden()
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
                    "keysSecret": 403,
                    "adminsRead": 403,
                    "pluginsRead": 403,
                    "clientPolicyWrite": 403,
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
