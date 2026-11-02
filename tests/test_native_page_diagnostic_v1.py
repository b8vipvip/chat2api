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
        archive.writestr("diagnostic.log","tool_version=1.1.0")
        archive.writestr("manifest.json",json.dumps({
            "request_id":request_id,"worker_id":worker_id,"version":"1.1.0",
            "request_binding":"manual-conversation-url-unverified-turn"}))
    return buffer.getvalue()


def test_browser_collector_is_versioned_parseable_and_cdp_loopback_only():
    source=(ROOT/"scripts/native_page_diagnostic_v1.py").read_text()
    assert 'VERSION = "1.1.0"' in source
    assert 'const forbidden=' in source
    assert 'document.cookie' not in source
    assert "localStorage" not in source
    assert "Page.navigate" not in source
    assert "Page.captureScreenshot" in source
    assert '"diagnostic.log"' in source
    proc=subprocess.run([sys.executable,"scripts/native_page_diagnostic_v1.py","--version"],
        cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert proc.returncode == 0,proc.stderr
    assert proc.stdout.strip()=="1.1.0"
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
    assert v["ok"] and v["installed"] is True and v["version"]=="1.1.0"
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
    async def send(worker_id,command,args,wait,timeout):
        assert worker_id==worker
        if command=="native_page_start":
            return {"result":{"ok":True,"tool_version":"1.1.0"}}
        if command=="native_page_status":
            return {"result":{"ok":True,"status":"completed",
                "stage":"complete","percent":100,"size":len(raw),
                "sha256":hashlib.sha256(raw).hexdigest(),
                "tool_version":"1.1.0","counts":{"tables":3,"images":2}}}
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
        assert result.json()["job"]["tool_version"]=="1.1.0"
        assert client.get("/api/admin/worker-tools/native-page/jobs/"+job+"/download").status_code==401
        download=client.get("/api/admin/worker-tools/native-page/jobs/"+job+"/download",headers=auth)
        assert download.status_code==200
        assert download.content==raw
        assert "v1.1.0.zip" in download.headers["content-disposition"]
        record=client.get("/api/admin/worker-tools/native-page/jobs",headers=auth).json()
        assert record["data"][0]["request_id"]==request_id


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


def test_cdp_isolated_content_world_has_visible_request_dom_marker():
    source=(ROOT/"scripts/native_page_diagnostic_v1.py").read_text()
    content=(ROOT/"chrome_extension/content_request_v6.js").read_text()
    assert "data-chat2api-native-request-id" in source
    assert "data-chat2api-native-request-id" in content
    assert "tagged?expected:null" in source
    assert 'assistantNode.setAttribute(' in content
    assert 'native_chatgpt_conversation_url' in content
