"""Versioned native-page tool: contract, safety and administrator job tests."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.native_page_diagnostic_api import install_native_page_diagnostics
from scripts import native_page_tool_control as control


ROOT = Path(__file__).resolve().parents[1]


def _archive(request_id, worker_id):
    buffer=io.BytesIO()
    with ZipFile(buffer,"w") as archive:
        archive.writestr("assistant_dom.json","{}")
        archive.writestr("diagnostic.log","tool_version=1.2.1")
        archive.writestr("manifest.json",json.dumps({
            "request_id":request_id,"worker_id":worker_id,"version":"1.2.1",
            "request_binding":"manual-conversation-url-unverified-turn"}))
    return buffer.getvalue()


def test_browser_collector_is_versioned_parseable_and_cdp_loopback_only():
    source=(ROOT/"scripts/native_page_diagnostic_v1.py").read_text()
    assert 'VERSION = "1.2.1"' in source
    assert 'const forbidden=' in source
    assert 'document.cookie' not in source
    assert "localStorage" not in source
    assert "Page.navigate" not in source
    assert "Page.captureScreenshot" in source
    assert '"diagnostic.log"' in source
    proc=subprocess.run([sys.executable,"scripts/native_page_diagnostic_v1.py","--version"],
        cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert proc.returncode == 0,proc.stderr
    assert proc.stdout.strip()=="1.2.1"
    match=re.search(r'CAPTURE = r"""(.*?)"""',source,re.S)
    assert match
    js=match.group(1)+"('req_example123456');"
    compile=subprocess.run(["node","--check"],cwd=ROOT,input=js,text=True,
                           capture_output=True,timeout=10)
    assert compile.returncode == 0,compile.stderr


def test_controller_uses_only_fixed_commands_and_worker_id(monkeypatch,tmp_path):
    monkeypatch.setattr(control,"ROOT",tmp_path)
    monkeypatch.setattr(control,"INSTALLED",tmp_path/"overridden-tool.py")
    worker="wrk_abc123456"
    v=control.execute("native_page_version",{},worker,1,"https://example.test")
    assert v["ok"] and v["installed"] is True and v["version"]=="1.2.1"
    assert control.execute("unknown_tool_command",{},worker,1,"https://example.test")["ok"] is False
    assert control.execute("native_page_start",{"job_id":"../../../bad",
        "request_id":"req_abc123456"},worker,1,"https://example.test")["ok"] is False
    assert control.execute("native_page_start",{"job_id":"a"*32,
        "request_id":"not-id"},worker,1,"https://example.test")["ok"] is False
    assert not list(tmp_path.glob("*")), "invalid requests must not create any files"


def test_admin_jobs_are_request_bound_and_zip_authenticated(tmp_path,monkeypatch):
    from app import native_page_diagnostic_api as module
    monkeypatch.setattr(module,"POLL_SECONDS",0.01)
    app=FastAPI()
    worker="wrk_abc123456"
    request_id="req_abc12345678"
    app.state.settings=SimpleNamespace(data_dir=tmp_path,api_key="master-secret")
    app.state.admin_sessions=None
    app.state.telemetry=SimpleNamespace(get=lambda rid: {
        "request_id":rid,"linux_worker_id":worker,"worker_slot":1}
        if rid==request_id else None)
    app.state.linux_workers=SimpleNamespace(data={"workers":{
        worker:{"revoked_at":None},"wrk_wrong123456":{"revoked_at":None}}},
        list_public=lambda: [
            {"worker_id":worker,"name":"Linux-free","status":"ready","revoked_at":None,
             "metadata":{"worker_slot":1}},
            {"worker_id":"wrk_wrong123456","name":"Offline","status":"offline",
             "revoked_at":None,"metadata":{"worker_slot":2}},
        ])
    app.state.worker_sockets={worker:object()}
    raw=_archive(request_id,worker)
    seen=[]
    fail_status={"enabled":False}
    probe={"route":"conversation","assistant_roles":0,"assistant_testids":0,
           "verified_roleless_headings":0,"ready_state":"complete",
           "capture_error":"no-verified-assistant-boundary"}
    async def send(worker_id,command,args,wait,timeout):
        assert worker_id==worker
        if command=="native_page_start":
            seen.append(dict(args))
            nonlocal raw
            raw=_archive(args["request_id"],worker)
            if args["request_id"].startswith("req_diag_"):
                # URL-only must be explicitly identified in its ZIP, rather
                # than masquerading as a known chat2api request.
                from io import BytesIO
                buf=BytesIO()
                with ZipFile(buf,"w") as z:
                    z.writestr("assistant_dom.json","{}")
                    z.writestr("manifest.json",json.dumps({
                        "request_id":args["request_id"],"worker_id":worker,
                        "version":"1.2.1","request_binding":"url-only-unbound"}))
                raw=buf.getvalue()
            return {"result":{"ok":True,"tool_version":"1.2.1"}}
        if command=="native_page_status":
            if fail_status["enabled"]:
                return {"result":{"ok":True,"status":"failed","stage":"page-unavailable",
                    "percent":100,"tool_version":"1.2.1",
                    "error":"history_assistant_boundary_not_found",
                    "page_probe":probe}}
            return {"result":{"ok":True,"status":"completed",
                "stage":"complete","percent":100,"size":len(raw),
                "sha256":hashlib.sha256(raw).hexdigest(),
                "tool_version":"1.2.1","counts":{"tables":3,"images":2}}}
        if command=="native_page_chunk":
            i=args["index"];slice_=raw[i*65536:(i+1)*65536]
            import base64
            return {"result":{"ok":True,"index":i,
                "count":(len(raw)+65535)//65536,"byte_length":len(raw),
                "sha256":hashlib.sha256(raw).hexdigest(),
                "data":base64.b64encode(slice_).decode()}}
        raise AssertionError(command)
    app.state.send_linux_worker_command=send
    install_native_page_diagnostics(app)
    auth={"Authorization":"Bearer master-secret"}
    with TestClient(app) as client:
        assert client.get("/api/admin/worker-tools/native-page/jobs").status_code==401
        assert client.get("/api/admin/worker-tools/native-page/request-worker/"+request_id,
            headers=auth).json()["worker_id"]==worker
        assert client.get("/api/admin/worker-tools/native-page/workers").status_code==401
        listed=client.get("/api/admin/worker-tools/native-page/workers",headers=auth).json()
        assert len(listed["data"])==2
        assert listed["data"][0]["worker_id"]==worker
        assert listed["data"][0]["name"]=="Linux-free"
        mismatch=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"request_id":request_id,"worker_id":"wrk_wrong123456"})
        assert mismatch.status_code==409
        created=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"request_id":request_id})
        assert created.status_code==200,created.text
        job=created.json()["job"]["job_id"]
        result=None
        for _ in range(70):
            result=client.get("/api/admin/worker-tools/native-page/jobs/"+job,headers=auth)
            if result.json()["job"]["status"]=="completed":break
            time.sleep(0.03)
        assert result.json()["job"]["status"]=="completed",result.text
        assert result.json()["job"]["tool_version"]=="1.2.1"
        assert client.get("/api/admin/worker-tools/native-page/jobs/"+job+"/download").status_code==401
        download=client.get("/api/admin/worker-tools/native-page/jobs/"+job+"/download",headers=auth)
        assert download.status_code==200
        assert download.content==raw
        assert "v1.2.1.zip" in download.headers["content-disposition"]
        record=client.get("/api/admin/worker-tools/native-page/jobs",headers=auth).json()
        assert record["data"][0]["request_id"]==request_id
        # A standalone valid history URL is diagnosable with only an owner
        # Worker, and the internal synthetic ID is not displayed as API ID.
        history="https://chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b"
        assert client.post("/api/admin/worker-tools/native-page/jobs",
            headers=auth,json={"target":history,"worker_id":""}).status_code==400
        for bad in ("http://chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b",
                    history+"?token=secret","https://evil.example/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b"):
            assert client.post("/api/admin/worker-tools/native-page/jobs",
                headers=auth,json={"target":bad,"worker_id":worker}).status_code==422
        url_job=client.post("/api/admin/worker-tools/native-page/jobs",
            headers=auth,json={"target":history,"worker_id":worker})
        assert url_job.status_code==200,url_job.text
        visible=url_job.json()["job"]
        assert visible["request_id"]==""
        assert visible["target_type"]=="conversation_url"
        assert visible["target_label"]==history
        second=visible["job_id"]
        for _ in range(70):
            result=client.get("/api/admin/worker-tools/native-page/jobs/"+second,headers=auth)
            if result.json()["job"]["status"]=="completed":break
            time.sleep(0.03)
        assert result.json()["job"]["status"]=="completed",result.text
        assert seen[-1]["request_id"].startswith("req_diag_")
        assert seen[-1]["conversation_url"]==history
        zip_resp=client.get("/api/admin/worker-tools/native-page/jobs/"+second+"/download",headers=auth)
        assert zip_resp.status_code==200
        with ZipFile(io.BytesIO(zip_resp.content)) as zipf:
            manifest=json.loads(zipf.read("manifest.json"))
        assert manifest["request_binding"]=="url-only-unbound"
        assert manifest["request_id"].startswith("req_diag_")
        assert "history-url" in zip_resp.headers["content-disposition"]
        assert client.get("/api/admin/worker-tools/native-page/jobs/"+second+"/log",
            headers=auth).json()["record"]["target_type"]=="conversation_url"
        fail_status["enabled"]=True
        third=client.post("/api/admin/worker-tools/native-page/jobs",
            headers=auth,json={"target":history,"worker_id":worker})
        assert third.status_code==200,third.text
        failed_id=third.json()["job"]["job_id"]
        for _ in range(70):
            info=client.get("/api/admin/worker-tools/native-page/jobs/"+failed_id,headers=auth)
            if info.json()["job"]["status"]=="failed":break
            time.sleep(0.03)
        assert info.json()["job"]["status"]=="failed",info.text
        assert info.json()["job"]["page_probe"]==probe
        exported=client.get("/api/admin/worker-tools/native-page/jobs/"+failed_id+"/log",
            headers=auth)
        assert exported.status_code==200
        assert exported.json()["record"]["page_probe"]==probe
        assert client.get("/api/admin/worker-tools/native-page/jobs/"+failed_id+"/download",
            headers=auth).status_code==409


def test_native_page_history_recovery_uses_time_filter_and_content_sha(tmp_path):
    import sqlite3
    from scripts.native_page_diagnostic_v1 import chrome_history_candidates,reference_matches
    path=tmp_path/"History"
    db=sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT);
        CREATE TABLE visits(url INTEGER,visit_time INTEGER);
    """)
    epoch=1793591108
    def chrome(t):return int((t+11644473600)*1000000)
    db.executemany("INSERT INTO urls(id,url) VALUES (?,?)",[
        (1,"https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"),
        (2,"https://chatgpt.com/c/abcdef12-abcd-abcd-abcd-abcdefabcdef"),
        (3,"https://untrusted.example/c/fake123456789")])
    db.executemany("INSERT INTO visits(url,visit_time) VALUES (?,?)",[
        (1,chrome(epoch-20)),(2,chrome(epoch+800)),(3,chrome(epoch-20))])
    db.commit();db.close()
    phrase="Original assistant rich content with a distinctive phrase and tables."
    cleaned="".join(ch.lower() for ch in phrase if ch.isalnum())
    digests=[hashlib.sha256(cleaned[i:i+48].encode()).hexdigest() for i in (0,4)]
    hits=chrome_history_candidates(9222,{"start":epoch-120,
        "end":epoch+120,"hashes":digests},history_file=path)
    assert hits==["https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"]
    report={"ok":True,"root":{"tag":"assistant-reply","children":[
        {"tag":"p","children":[{"tag":"#text","text":phrase}]}]}}
    assert reference_matches(report,digests)==2
    assert reference_matches({"ok":True,"root":{"tag":"assistant-reply",
        "children":[{"tag":"#text","text":"Unrelated assistant response"}]}},digests)==0
    assert chrome_history_candidates(9222,{"start":epoch-120,
        "end":epoch+120,"hashes":[]},history_file=path)==[]


def test_native_probe_is_structural_and_kept_on_failure(tmp_path):
    import asyncio
    from scripts import native_page_diagnostic_v1 as native
    folder=tmp_path/"sample"
    folder.mkdir()
    page={"ready_state":"complete","route":"conversation",
          "assistant_roles":0,"user_roles":4,
          "conversation_turns":5,"verified_roleless_headings":0,
          "main_landmarks":1,"password_fields":False,
          "challenge_frames":False,"loading_skeletons":False,
          "capture_error":"no-verified-assistant-boundary"}
    class FakeCDP:
        async def call(self, method, params):
            assert method=="Runtime.evaluate"
            if "CAPTURE" not in params["expression"] and "expected" in params["expression"]:
                return {"result":{"value":{"ok":False,
                    "error":"no-verified-assistant-boundary"}}}
            return {"result":{"value":page}}
    async def run():
        capture,probe=await native.inspect_page(FakeCDP(),"req_test123456")
        assert capture is None
        assert probe["route"]=="conversation"
        assert probe["assistant_roles"]==0
        assert probe["capture_error"]=="no-verified-assistant-boundary"
        assert native.failure_reason(probe)=="history_assistant_boundary_not_found"
    asyncio.run(run())
    native.status(folder,"awaiting-assistant",36,page_probe=page)
    native.status(folder,"failed",100,error="history_assistant_boundary_not_found")
    recorded=json.loads((folder/"status.json").read_text())
    assert recorded["page_probe"]==page
    assert recorded["error"]=="history_assistant_boundary_not_found"
    assert "account_email" not in json.dumps(recorded)
    assert "chat_text" not in json.dumps(recorded)
    assert native.failure_reason({"route":"access-gate"})=="history_access_gate_detected"
    assert native.failure_reason({"capture_error":"capture-js-exception"})=="history_capture_runtime_failed"


def test_native_page_roleless_capture_vm_and_page_probe():
    result=subprocess.run(["node","tests/native_page_roleless_probe_v121.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+"\\n"+result.stderr


def test_native_page_probe_vm_syntax_and_no_text_or_media_exfiltration():
    source=(ROOT/"scripts/native_page_diagnostic_v1.py").read_text()
    extracted=re.search(r'PAGE_PROBE = r"""(.*?)"""',source,re.S)
    assert extracted
    js=extracted.group(1)
    check=subprocess.run(["node","--check"],cwd=ROOT,input=js,
                         capture_output=True,text=True,timeout=10)
    assert check.returncode==0,check.stderr
    for forbidden in ["document.cookie","localStorage","document.body.innerText",
                      "document.documentElement.outerHTML","Page.navigate",
                      "Page.captureScreenshot"]:
        assert forbidden not in js
    assert "verified-roleless-turn" in source
    assert "heading.closest(" in source
    assert "scope='roleless-heading-siblings'" in source


def test_cdp_isolated_content_world_has_visible_request_dom_marker():
    source=(ROOT/"scripts/native_page_diagnostic_v1.py").read_text()
    content=(ROOT/"chrome_extension/content_request_v6.js").read_text()
    assert "data-chat2api-native-request-id" in source
    assert "data-chat2api-native-request-id" in content
    assert "tagged?expected:null" in source
    assert 'assistantNode.setAttribute(' in content
    assert 'native_chatgpt_conversation_url' in content
