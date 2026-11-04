"""End-to-end authenticated Windows native page diagnosis API contract.

The Chrome extension itself is replaced with a fake authenticated transport;
this never opens a real browser or mutates a live ChatGPT request.
"""
import asyncio
import base64
import hashlib
import io
import json
import time
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.native_page_diagnostic_api import install_native_page_diagnostics
from app.native_page_windows_bridge import PART_BYTES
from app.windows_native_page_tool_release import WindowsCollectorReleaseSource


def test_windows_native_page_job_ext_identity_and_read_only_provenance(tmp_path,monkeypatch):
    app=FastAPI()
    ext="ext_winplus123456"
    history="https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
    report={
        "schema":"windows-native-page-evidence-v1",
        "version":"windows-dom-1.1.2","boundary_verified":True,
        "request_bound":False,"complete":True,"message_id":"assistant-native-1",
        "probe":{"ready_state":"complete","assistant_role_nodes":1},
        "counts":{"nodes":6,"text_chars":10,"tables":1,"images":1},
        "root":{"tag":"div","children":[{"tag":"#text","text":"test reply"}]},
        "limitations":["No full-page screenshot","No source image bytes",
                       "No API request identity validation"],
    }
    independent='/* verified GitHub source */(()=>{const DIAGNOSTIC_TOOL_VERSION="1.0.0";return {ok:true};})()'
    independent_digest=hashlib.sha256(independent.encode()).hexdigest()
    independent_manifest={"version":"1.0.0","sha256":independent_digest,
                          "bytes":len(independent.encode()),"source":"github-dedicated-release-branch"}
    async def fake_release(self,*,force=False):
        return independent,independent_manifest
    monkeypatch.setattr(WindowsCollectorReleaseSource,"load",fake_release)
    body=json.dumps(report,ensure_ascii=False).encode("utf8")
    digest=hashlib.sha256(body).hexdigest()
    class Registry:
        def __init__(self):
            self.clients={
                ext:SimpleNamespace(connection_enabled=True,name="win-plus",
                    metadata={"platform_os":"win",
                              "extension_version":"0.22.265",
                              "windows_native_page_diagnostic_version":"windows-dom-1.1.2"}),
                "ext_mac777777":SimpleNamespace(connection_enabled=True,name="Mac",
                    metadata={"platform_os":"mac",
                              "windows_native_page_diagnostic_version":"windows-dom-1.1.2"}),
            }
            self.sockets={ext:object()}
            self.sent=[]
        async def send(self,worker,payload):
            self.sent.append((worker,payload))
            assert worker==ext
            if payload["type"]=="native.page.windows.tool.update":
                assert payload["version"]=="1.0.0"
                assert payload["sha256"]==independent_digest
                async def reply_update():
                    await app.state.native_page_windows_ingest(ext,{
                        "type":"native.page.windows.tool.updated",
                        "update_id":payload["update_id"],"ok":True,
                        "version":"1.0.0","sha256":independent_digest,
                        "updated":True})
                asyncio.create_task(reply_update())
                return
            assert payload["type"]=="native.page.windows.start"
            assert payload["conversation_url"]==history
            assert type(payload["show_tab"]) is bool
            async def reply():
                receiver=app.state.native_page_windows_ingest
                for phase,elapsed in [("accepted",10),("worker_lookup",28),
                    ("tab_opening",34),("tab_opened",58),("page_ready",143),
                    ("assistant_scanning",150),("assistant_captured",512),
                    ("uploading",520),("upload_complete",550),("tab_closed",571)]:
                    await receiver(ext,{"type":"native.page.windows.progress",
                        "job_id":payload["job_id"],"stage":phase,"elapsed_ms":elapsed,
                        "tab_id":101,"window_id":26,"show_tab":payload["show_tab"]})
                count=(len(body)+PART_BYTES-1)//PART_BYTES
                for i,offset in enumerate(range(0,len(body),PART_BYTES)):
                    await receiver(ext,{"type":"native.page.windows.chunk",
                        "job_id":payload["job_id"],"index":i,"count":count,
                        "size":len(body),"sha256":digest,
                        "data":base64.b64encode(body[offset:offset+PART_BYTES]).decode()})
                await receiver(ext,{"type":"native.page.windows.finished",
                    "job_id":payload["job_id"],"ok":True,"sha256":digest,"size":len(body)})
            asyncio.create_task(reply())
    registry=Registry()
    app.state.registry=registry
    app.state.settings=SimpleNamespace(data_dir=tmp_path,api_key="api-secret")
    app.state.admin_sessions=None
    missing_link_id="req_windowsnomedia12345"
    app.state.telemetry=SimpleNamespace(get=lambda rid:
        {"request_id":rid,"worker_client_id":ext,"diagnostics":{}}
        if rid==missing_link_id else None)
    app.state.linux_workers=SimpleNamespace(data={"workers":{}},list_public=lambda:[])
    app.state.worker_sockets={}
    install_native_page_diagnostics(app)
    auth={"Authorization":"Bearer api-secret"}
    with TestClient(app) as client:
        assert client.get("/api/admin/worker-tools/native-page/workers").status_code==401
        rows=client.get("/api/admin/worker-tools/native-page/workers",headers=auth).json()["data"]
        assert len(rows)==1,rows
        assert rows[0]["worker_id"]==ext
        assert rows[0]["platform"]=="windows"
        assert rows[0]["diagnostic_supported"] is True
        assert rows[0]["diagnostic_version"]=="windows-dom-1.1.2"
        assert rows[0]["extension_version"]=="0.22.265"
        ver=client.get(f"/api/admin/worker-tools/native-page/workers/{ext}/version",headers=auth)
        assert ver.status_code==200
        assert ver.json()["tool"]["version"]=="未安装独立诊断工具"
        assert ver.json()["tool"]["extension_diagnostic_version"]=="windows-dom-1.1.2"
        assert ver.json()["latest"]["version"]=="1.0.0"
        update=client.post(f"/api/admin/worker-tools/native-page/workers/{ext}/update",headers=auth)
        assert update.status_code==200,update.text
        assert update.json()["tool"]["version"]=="1.0.0"
        assert update.json()["tool"]["sha256"]==independent_digest
        assert registry.clients[ext].metadata["windows_native_tool_version"]=="1.0.0"
        after=client.get(f"/api/admin/worker-tools/native-page/workers/{ext}/version",headers=auth)
        assert after.json()["tool"]["version"]=="1.0.0"
        assert after.json()["tool"]["installed"] is True
        not_owner=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"target":history,"worker_id":"ext_mac777777"})
        assert not_owner.status_code==409

        owner=client.get("/api/admin/worker-tools/native-page/request-worker/"+missing_link_id,
            headers=auth)
        assert owner.status_code==200
        assert owner.json()["worker_id"]==ext
        missing=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"target":missing_link_id,"worker_id":ext})
        assert missing.status_code==422,missing.text
        assert len(registry.sent)==1  # Only authenticated hot-update command sent.

        created=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"target":history,"worker_id":ext})
        assert created.status_code==200,created.text
        job=created.json()["job"]
        assert job["worker_id"]==ext
        assert job["worker_platform"]=="windows"
        assert job["request_id"]==""
        result=None
        for _ in range(150):
            result=client.get("/api/admin/worker-tools/native-page/jobs/"+job["job_id"],headers=auth)
            if result.json()["job"]["status"] in ("completed","failed"):
                break
            time.sleep(0.02)
        assert result.json()["job"]["status"]=="completed",result.text
        assert len(registry.sent)==2
        exported=client.get("/api/admin/worker-tools/native-page/jobs/"+job["job_id"]+"/download",
                            headers=auth)
        assert exported.status_code==200
        with ZipFile(io.BytesIO(exported.content)) as archive:
            assert json.loads(archive.read("assistant_dom.json"))==report
            manifest=json.loads(archive.read("manifest.json"))
            trace=json.loads(archive.read("execution_trace.json"))
        assert trace["diagnostic_tab_id"]==101
        assert trace["diagnostic_window_id"]==26
        assert trace["show_tab"] is False
        assert [item["stage"] for item in trace["trace"]][-1]=="tab_closed"
        assert trace["trace"][0]["elapsed_ms"]==10

        assert manifest["request_binding"]=="url-only-unbound"
        assert manifest["worker_id"]==ext
        assert manifest["worker_platform"]=="windows"
        assert manifest["boundary_verified"] is True
        assert manifest["completeness_verified"] is False
        assert manifest["visual_verified"] is False
        log=client.get("/api/admin/worker-tools/native-page/jobs/"+job["job_id"]+"/log",
                       headers=auth).json()
        assert log["record"]["worker_id"]==ext
        assert log["record"]["worker_platform"]=="windows"
        assert log["record"]["diagnostic_tab_id"]==101
        assert log["record"]["trace"][-1]["stage"]=="tab_closed"
        assert log["record"]["show_tab"] is False
