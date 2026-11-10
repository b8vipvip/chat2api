"""Real Playground model requests must never await a UI history reload."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_chat_run_durable_first_then_model_dispatch_unblocked_by_ui_refresh():
    if not shutil.which("node"):
        return
    result=subprocess.run(
        ["node","tests/playground_nonblocking_history_refresh_v337.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=30,
    )
    assert result.returncode==0,result.stdout+"\n"+result.stderr


def test_script_keeps_mandatory_persistence_but_detaches_optional_view():
    script=(ROOT/"app/admin_playground_chat_records_v3.js").read_text()
    assert "await startChatRun(requestId, body, transportContract);" in script
    assert "return baseFetch(input, init);" in script
    assert "void Promise.resolve().then(() => globalThis.loadTests()).catch(() => {});" in script
    assert "await globalThis.loadTests()" not in script
