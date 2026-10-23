from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
