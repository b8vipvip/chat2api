"""Worker platform consolidation and credential-vault regression contracts."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.worker_auto_login_v153_patch import WorkerLoginVault, normalize_totp, totp_code


ROOT = Path(__file__).resolve().parents[1]


def test_worker_login_vault_encrypts_secrets_and_persists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CHAT2API_WORKER_LOGIN_KEY", raising=False)
    vault = WorkerLoginVault(tmp_path)
    profile = {
        "username": "person@example.com",
        "password": "test-password-should-be-encrypted",
        "totp_secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        "enabled": True,
    }
    vault.put("linux_worker_01", profile)
    text = vault.path.read_text()
    assert "test-password-should-be-encrypted" not in text
    assert "person@example.com" not in text
    assert profile["totp_secret"] not in text
    assert vault.get("linux_worker_01") == profile
    assert vault.public("linux_worker_01") == {
        "worker_id": "linux_worker_01",
        "configured": True, "enabled": True,
        "username": "person@example.com",
        "has_password": True, "has_totp": True,
    }
    assert WorkerLoginVault(tmp_path).get("linux_worker_01") == profile
    if os.name == "posix":
        assert stat.S_IMODE(vault.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(vault.key_path.stat().st_mode) == 0o600
    vault.remove("linux_worker_01")
    assert WorkerLoginVault(tmp_path).get("linux_worker_01") is None


def test_worker_totp_uses_rfc6238_algorithm() -> None:
    # RFC 6238 SHA1 / 30-second interval at 59 s (6-digit last six).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert normalize_totp(secret.lower().replace("G", "g")) == secret
    assert totp_code(secret, now=59) == "287082"
    with pytest.raises(ValueError):
        normalize_totp("invalid-secret")


def test_unified_console_uses_single_platform_grouping() -> None:
    unified = (ROOT / "app/admin_unified_workers_v153.js").read_text(encoding="utf-8")
    linux = (ROOT / "app/admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    windows = (ROOT / "app/admin_extension_columns.js").read_text(encoding="utf-8")
    assert "workerGroup-linux" in unified
    assert "workerGroup-windows" in unified
    assert "linuxNav.remove()" in unified
    assert "linuxPanel" in unified
    assert "data-linux-worker-id" in linux
    assert 'id="linuxDeviceTableV124"' in linux
    assert 'id="linuxWorkerTableV155"' in linux
    assert 'id="windowsDeviceTableV155"' in unified
    assert 'byId("workerGroup-linux").appendChild(linuxWorkers)' in unified
    assert "data-worker-login-edit" in linux
    assert "data-worker-login-edit" in windows
    assert 'id="v153-close" type="button"' in unified
    assert 'dialog.close()' in unified
    assert 'row.metadata?.linux_worker_id' in windows


def test_login_recovery_requires_secure_bound_transport_and_never_sends_totp_seed() -> None:
    source = (ROOT / "app/worker_auto_login_v153_patch.py").read_text(encoding="utf-8")
    opening = (ROOT / "chrome_extension/background_login_recovery_v153.js").read_text(encoding="utf-8")
    autofill = (ROOT / "chrome_extension/background_login_autofill_v154.js").read_text(encoding="utf-8")
    entry = (ROOT / "chrome_extension/background_entry.js").read_text(encoding="utf-8")
    assert "secure_extension_socket(client_id)" in source
    assert 'socket.url.scheme' in source
    assert '["127.0.0.1", "::1", "localhost"]' not in source  # only literal loopback peers
    assert '"worker.login.start.v154"' in source
    assert '"worker.login.totp.v154"' in source
    assert '"totp_secret": profile.' not in source
    assert "worker_for_extension(client_id)" in source
    assert "openLoginWindow" in opening
    assert 'worker_login_attempt_id' in opening
    assert "background_login_autofill_v154.js" in entry
    assert 'func: loginStep' in autofill
    assert "trustedPage(url)" in autofill
    assert "password" not in autofill.split("chrome.storage.local.set(")[-1] if "chrome.storage.local.set(" in autofill else True
    assert "worker.login.totp.v154" in autofill


def test_recovery_requires_unique_fresh_probes_and_correlates_acknowledgments() -> None:
    source = (ROOT / "app/worker_auto_login_v153_patch.py").read_text(encoding="utf-8")
    assert "if checked_at <= last_checked_at:" in source
    assert "attempt.get(\"id\") == ack_id" in source
    assert "checked_at >= int(attempt.get(\"started_at_ms\")" in source
    assert "RECOVERY_TIMEOUT_SECONDS = 180" in source
    assert "COOLDOWN_SECONDS = 300" in source


def test_login_chrome_script_parses_when_node_is_available() -> None:
    import shutil

    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    for filename in (
        "background_login_recovery_v153.js",
        "background_login_autofill_v154.js",
        "background_entry.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / "chrome_extension" / filename)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 0, filename + ": " + result.stderr


def test_worker_login_routes_are_installed_in_production_entry() -> None:
    script = """
import json
from app.entry import app
print(json.dumps(sorted({
    (route.path, ",".join(sorted(getattr(route, "methods", []) or [])))
    for route in app.routes
    if route.path.startswith("/api/admin/worker-login")
})))
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert process.returncode == 0, process.stderr
    routes = json.loads(process.stdout.strip().splitlines()[-1])
    paths = {row[0] for row in routes}
    assert "/api/admin/worker-login" in paths
    assert "/api/admin/worker-login/{worker_id}" in paths
    assert "/api/admin/worker-login/{worker_id}/trigger" in paths
    assert "/api/admin/worker-login/{worker_id}/manual" in paths
    assert "/api/admin/worker-login/{worker_id}/totp" in paths
