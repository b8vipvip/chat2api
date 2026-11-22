from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RECONCILE = ROOT / "scripts" / "chat2api_update_reconcile.py"


def run_reconcile(tmp_path: Path, result: str):
    env = dict(os.environ, APP_DIR=str(tmp_path), SERVICE_RESULT=result,
               EXIT_STATUS="15")
    return subprocess.run([sys.executable, str(RECONCILE)], env=env,
                          capture_output=True, text=True, timeout=10)


def test_timeout_marks_only_running_update_failed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    state = {
        "request_id": "upd_123", "status": "running", "stage": "build",
        "percent": 55, "from_commit": "a" * 40,
        "target_commit": "b" * 40, "rollback_succeeded": False,
    }
    file = data / "admin-update-status.json"
    file.write_text(json.dumps(state), encoding="utf-8")
    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 0, result.stderr
    updated = json.loads(file.read_text(encoding="utf-8"))
    assert updated["status"] == "failed"
    assert updated["stage"] == "build"
    assert updated["request_id"] == "upd_123"
    assert updated["rollback_succeeded"] is False
    assert updated["completed_at"]
    assert "systemd=timeout" in updated["message"]
    assert "不能推定已回滚" in updated["message"]


def test_normal_or_completed_update_never_overwritten(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    file = data / "admin-update-status.json"
    state = {"status": "running", "stage": "build"}
    file.write_text(json.dumps(state), encoding="utf-8")
    assert run_reconcile(tmp_path, "success").returncode == 0
    assert json.loads(file.read_text(encoding="utf-8")) == state
    state["status"] = "succeeded"
    file.write_text(json.dumps(state), encoding="utf-8")
    assert run_reconcile(tmp_path, "timeout").returncode == 0
    assert json.loads(file.read_text(encoding="utf-8")) == state


def test_installer_has_longer_bound_and_failure_hook():
    source = (ROOT / "scripts" / "install_chat2api_server_updater.sh").read_text(
        encoding="utf-8")
    assert "TimeoutStartSec=75min" in source
    assert "ExecStopPost=/usr/bin/python3" in source
    assert "scripts/chat2api_update_reconcile.py" in source
    assert "TimeoutStartSec=30min" not in source


def test_reconcile_restores_compose_backup_with_atomic_private_write(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    backup = data / ".admin-update-docker-compose.backup"
    backup.write_text("services:\\n  chat2api:\\n    image: safe\\n", encoding="utf-8")
    status = data / "admin-update-status.json"
    status.write_text(json.dumps({"status": "running", "stage": "deploy"}), encoding="utf-8")

    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 0, result.stderr
    target = tmp_path / "docker-compose.yml"
    assert target.read_text(encoding="utf-8") == backup.read_text(encoding="utf-8") if backup.exists() else target.read_text(encoding="utf-8") == "services:\\n  chat2api:\\n    image: safe\\n"
    assert not backup.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
def test_reconcile_never_reads_symlinked_or_hardlinked_status_files(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside-status.json"
    outside.write_text(json.dumps({"status": "running", "stage": "outside"}), encoding="utf-8")
    status = data / "admin-update-status.json"
    status.symlink_to(outside)

    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 0, result.stderr
    assert json.loads(outside.read_text(encoding="utf-8"))["stage"] == "outside"
    assert status.is_symlink()

    status.unlink()
    regular = data / "status-source.json"
    regular.write_text(json.dumps({"status": "running", "stage": "outside"}), encoding="utf-8")
    os.link(regular, status)
    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 0, result.stderr
    assert json.loads(regular.read_text(encoding="utf-8"))["stage"] == "outside"
    assert json.loads(status.read_text(encoding="utf-8"))["stage"] == "outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
def test_reconcile_rejects_linked_compose_backup_without_copying_target(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside-compose.yml"
    outside.write_text("external content", encoding="utf-8")
    backup = data / ".admin-update-docker-compose.backup"
    backup.symlink_to(outside)
    status = data / "admin-update-status.json"
    status.write_text(json.dumps({"status": "running", "stage": "deploy"}), encoding="utf-8")

    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 1
    assert outside.read_text(encoding="utf-8") == "external content"
    assert not (tmp_path / "docker-compose.yml").exists()

    backup.unlink()
    os.link(outside, backup)
    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 1
    assert outside.read_text(encoding="utf-8") == "external content"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink assertion")
def test_reconcile_refuses_symlinked_compose_destination(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside-compose-target"
    outside.write_text("keep", encoding="utf-8")
    target = tmp_path / "docker-compose.yml"
    target.symlink_to(outside)
    (data / ".admin-update-docker-compose.backup").write_text("services: {}\\n", encoding="utf-8")
    (data / "admin-update-status.json").write_text(
        json.dumps({"status": "running", "stage": "deploy"}), encoding="utf-8",
    )

    result = run_reconcile(tmp_path, "timeout")
    assert result.returncode == 1
    assert outside.read_text(encoding="utf-8") == "keep"
