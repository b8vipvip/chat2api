from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def read(path):
    return (ROOT / path).read_text(encoding="utf-8")

def test_old_assistant_nodes_do_not_mask_new_roleless_answer():
    result=subprocess.run(
        ["node","tests/historical_assistant_roleless_fallback_v242.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=20,check=False,
    )
    assert result.returncode==0, result.stdout+"\\n"+result.stderr

def test_downloads_are_current_turn_verified_and_independent_of_network_eof():
    owner=read("chrome_extension/content_request_v6.js")
    native=read("chrome_extension/native_file_main_v210.js")
    assert "function verifiedFileReplyBoundary(active, current)" in owner
    assert "same-request-new-reply-toolbar-v242" in owner
    assert "const fileReplyBoundary = fileCreationRequested && !generating" in owner
    assert "&& !visibleTurnFiles && !upstreamStillWorking()" not in owner
    assert "fileReplyBoundary.replyScope, active.requestId" in owner
    assert "allRequiredFileTypesDelivered" in owner
    assert "media_native_file_failed_types: active.mediaNativeFileFailedTypes" in owner
    assert "fileCreationRequested\n      ? \"ChatGPT did not deliver all required verified downloadable file bytes" in owner
    assert "fetch-verified-same-type-alias" in native
    assert "dispositionName.split(\".\").pop().toLowerCase() === ext" in native
    assert "validBytes(bytes, session.filename)" in native
    assert "Date.now() - Number(session.armedAt || 0) <= 6000" in native
    assert "session.used = true" in native

def test_v242_worker_continuity_remains_bounded_and_server_owned():
    patch=read("app/worker_transport_recovery_patch.py")
    assert "WORKER_RECONNECT_GRACE_SECONDS = 20.0" in patch
    assert "same_runtime" in patch
    assert "broker.publish = publish_with_worker_reconnect" in patch
    for path in ("chrome_extension/content_request_v6.js","chrome_extension/native_file_main_v210.js"):
        check=subprocess.run(["node","--check",str(ROOT/path)],capture_output=True,text=True)
        assert check.returncode==0, path+":"+check.stderr
