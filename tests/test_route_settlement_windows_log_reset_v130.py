from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_v130_browser_regressions():
    if not shutil.which("node"):
        return
    script=ROOT/"tests"/"route_settlement_windows_log_reset_v130.mjs"
    proc=subprocess.run(["node",str(script)],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stdout+"\n"+proc.stderr

def test_v130_single_terminal_and_server_fifo_authority():
    route=(ROOT/"chrome_extension/conversation_routing.js").read_text(encoding="utf-8")
    logs=(ROOT/"chrome_extension/background_logging.js").read_text(encoding="utf-8")
    assert "terminalSettlements: new Map()" in route
    assert "await settlement.catch" in route
    assert "restore-stale-router-lease-v147" in route
    assert "state.activeRequests.has(String(item.inflight_request_id))" in route
    assert "routeAllocations.get(baseKey)" in route
    assert "::parallel::" in route
    assert "Server scheduler invariant violated" not in route
    assert 'if (SUCCESS_TYPES.has(event.type) || FAILURE_TYPES.has(event.type))' in route
    assert "resetWindowsLogsOnRuntimeReload" in logs
    assert 'platform?.os !== "win"' in logs
    assert "chrome.storage.session.get(WINDOWS_SESSION_KEY)" in logs
    assert "await logSessionReady;" in logs
