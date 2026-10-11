"""Linux Worker standby count must use the bound Chrome Bridge's physical truth."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT/path).read_text(encoding="utf-8")


def test_linux_worker_no_longer_renders_permanent_question_mark():
    ui=read("app/admin_linux_device_authority_v124.js")
    assert 'const reserve="?";' not in ui
    assert 'api("/api/admin/window-manager")' in ui
    assert 'const reserve=linuxStandbyCell(w,ext)' in ui
    assert 'state.standbyTruth.get(id)' in ui
    assert 'worker?.extension_client_id' in ui
    assert 'data-linux-standby-source="live"' in ui
    assert 'data-linux-standby-source="cached"' in ui
    assert '上次核验' in ui
    assert 'const LINUX_STANDBY_GRACE_MS = 45000' in ui
    assert "if(refreshInFlight)return refreshInFlight" in ui
    assert 'raw!==null && raw!==undefined' in ui
    assert 'worker.chatgpt_routing_ready===true' in ui
    assert 'state.verifiedStandby.delete(id)' in ui
    assert 'max_windows' not in ui.split("function updateLinuxStandbyTruth",1)[1].split("function renderDevices",1)[0].replace("configured max_windows","")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_linux_live_standby_two_worker_isolation_in_real_javascript():
    r=subprocess.run(["node", str(ROOT/"tests/linux_standby_truth_ui_v158.mjs")],
                     capture_output=True,text=True,timeout=20,check=False)
    assert r.returncode==0,r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_linux_worker_js_syntax():
    r=subprocess.run(["node","--check",str(ROOT/"app/admin_linux_device_authority_v124.js")],
                     capture_output=True,text=True,timeout=20,check=False)
    assert r.returncode==0,r.stderr
