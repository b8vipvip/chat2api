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
    assert "Worker ID</th><th>设备 / Slot" in linux
    assert "data-worker-login-edit" in linux
    assert "data-worker-login-edit" in windows
    assert 'row.metadata?.linux_worker_id' in windows


def test_login_recovery_never_sends_profile_credentials_to_extension() -> None:
    source = (ROOT / "app/worker_auto_login_v153_patch.py").read_text(encoding="utf-8")
    script = (ROOT / "chrome_extension/background_login_recovery_v153.js").read_text(encoding="utf-8")
    assert '"type": "worker.login.open.v153"' in source
    assert '"username": profile[' not in source
    assert '"password": profile[' not in source
    assert '"totp_secret": profile.' not in source
    assert "openLoginWindow" in script


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
