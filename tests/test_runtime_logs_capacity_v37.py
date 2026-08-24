from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
from app.runtime_logs import RuntimeLogStore, redact_text
from app.runtime_logs_patch import install_runtime_logs_patch


ROOT = Path(__file__).resolve().parents[1]


class AdminSessions:
    def authenticate(self, token: str | None) -> bool:
        return token == "session-test"


def test_runtime_log_store_redacts_secrets_and_preserves_exception_context(tmp_path: Path) -> None:
    store = RuntimeLogStore(tmp_path)
    store.append(
        level="ERROR",
        logger_name="chat2api.capacity",
        message="failed Authorization: Bearer secret-bearer worker_token=secret-worker ?token=secret-query",
        exception="Traceback\nValueError: password=hunter2\nsecond line",
        context={"client_id": "ext_test", "api_key": "sk-secret"},
    )
    payload = store.query(limit=20)
    assert payload["returned"] == 1
    row = payload["data"][0]
    assert "secret-bearer" not in row["message"]
    assert "secret-worker" not in row["message"]
    assert "secret-query" not in row["message"]
    assert "hunter2" not in row["exception"]
    assert "second line" in row["exception"]
    assert row["context"]["api_key"] == "[REDACTED]"
    assert row["context"]["client_id"] == "ext_test"
    exported = store.export_text(limit=20)
    assert "chat2api.capacity" in exported
    assert "Traceback" in exported
    assert "secret-bearer" not in exported
    assert redact_text("password=abc") == "password=[REDACTED]"


def test_runtime_logs_admin_api_and_export(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path, api_key="master-secret")
    app.state.admin_sessions = AdminSessions()
    install_runtime_logs_patch(app)
    app.state.runtime_logs.append(
        level="WARNING",
        logger_name="chat2api.capacity",
        message="control not ready client=ext_test",
        context={"client_id": "ext_test"},
    )
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "session-test")

    response = client.get("/api/admin/runtime-logs", params={"q": "ext_test", "limit": 50})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["returned"] >= 1
    assert any("control not ready" in row["message"] for row in data["data"])

    exported = client.get("/api/admin/runtime-logs/export", params={"q": "ext_test"})
    assert exported.status_code == 200, exported.text
    assert "chat2api-runtime-logs-" in exported.headers["content-disposition"]
    assert "control not ready" in exported.text


def test_native_capacity_dispatch_and_v37_reporter_are_packaged() -> None:
    background = (ROOT / "chrome_extension" / "background.js").read_text(encoding="utf-8")
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    reporter = (ROOT / "chrome_extension" / "background_capacity_capability_v37.js").read_text(encoding="utf-8")

    assert "__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__" in background
    assert 'message.type === "extension.control"' in background
    assert "nativeCapacityControlMetadata()" in background
    assert "background-native-dispatch-v37" in background
    assert '"background_capacity_capability_v37.js"' in entry
    assert entry.index('"background_capacity_control_v36.js"') < entry.index('"background_capacity_capability_v37.js"')
    assert "extension_control_native_ready" in reporter
    assert "unhandledrejection" in reporter
    assert "installPostControlReporter" in reporter


def test_capacity_capability_v37_vm_contract() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "capacity_capability_v37.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "capacity_capability_v37 VM contract passed" in result.stdout


def test_worker_diagnostics_captures_service_worker_runtime_and_longer_journal() -> None:
    source = (ROOT / "scripts" / "linux_worker_diagnostics.sh").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "linux_worker_diagnostics_patch.py").read_text(encoding="utf-8")
    assert "extension service worker runtime / CDP probe" in source
    assert "Runtime.evaluate" in source
    assert "Runtime.exceptionThrown" in source
    assert "diagnostics-cdp-probe" in source
    assert "capacity_capability_v37_source" in source
    assert "--since '-90 min'" in source
    assert "server-extension-state.json" in patch
    assert "server-runtime.log" in patch
    assert "extension-runtime.log" in patch
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "linux_worker_diagnostics.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_log_console_is_installed_before_capacity_patch() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    ui = (ROOT / "app" / "admin_runtime_logs.js").read_text(encoding="utf-8")
    capacity = (ROOT / "app" / "extension_capacity_control_patch.py").read_text(encoding="utf-8")
    assert "install_runtime_logs_patch(app)" in entry
    assert entry.index("install_runtime_logs_patch(app)") < entry.index("install_extension_capacity_control_patch(app)")
    assert "chat2api 运行日志" in ui
    assert "/api/admin/runtime-logs/export" in ui
    assert "chat2api.capacity" in capacity
    assert "Capacity control capability not ready" in capacity
    assert "Capacity control confirmation timeout" in capacity
