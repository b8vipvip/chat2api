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


def test_windows_native_page_job_ext_identity_and_read_only_provenance(tmp_path):
    app=FastAPI()
    ext="ext_winplus123456"
    history="https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
    report={
        "schema":"windows-native-page-evidence-v1",
        "version":"windows-dom-1.0.0","boundary_verified":True,
        "request_bound":False,"complete":True,"message_id":"assistant-native-1",
        "probe":{"ready_state":"complete","assistant_role_nodes":1},
        "counts":{"nodes":6,"text_chars":10,"tables":1,"images":1},
        "root":{"tag":"div","children":[{"tag":"#text","text":"test reply"}]},
        "limitations":["No full-page screenshot","No source image bytes",
                       "No API request identity validation"],
    }
    body=json.dumps(report,ensure_ascii=False).encode("utf8")
    digest=hashlib.sha256(body).hexdigest()
    class Registry:
        def __init__(self):
            self.clients={
                ext:SimpleNamespace(connection_enabled=True,name="win-plus",
                    metadata={"platform_os":"win",
                              "windows_native_page_diagnostic_version":"windows-dom-1.0.0"}),
                "ext_mac777777":SimpleNamespace(connection_enabled=True,name="Mac",
                    metadata={"platform_os":"mac",
                              "windows_native_page_diagnostic_version":"windows-dom-1.0.0"}),
            }
            self.sockets={ext:object()}
            self.sent=[]
        async def send(self,worker,payload):
            self.sent.append((worker,payload))
            assert worker==ext
            assert payload["type"]=="native.page.windows.start"
            assert payload["conversation_url"]==history
            async def reply():
                receiver=app.state.native_page_windows_ingest
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
    app.state.telemetry=SimpleNamespace(get=lambda rid:None)
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
        ver=client.get(f"/api/admin/worker-tools/native-page/workers/{ext}/version",headers=auth)
        assert ver.status_code==200
        assert ver.json()["tool"]["version"]=="windows-dom-1.0.0"
        update=client.post(f"/api/admin/worker-tools/native-page/workers/{ext}/update",headers=auth)
        assert update.status_code==409
        not_owner=client.post("/api/admin/worker-tools/native-page/jobs",headers=auth,
            json={"target":history,"worker_id":"ext_mac777777"})
        assert not_owner.status_code==409

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
        assert len(registry.sent)==1
        exported=client.get("/api/admin/worker-tools/native-page/jobs/"+job["job_id"]+"/download",
                            headers=auth)
        assert exported.status_code==200
        with ZipFile(io.BytesIO(exported.content)) as archive:
            assert json.loads(archive.read("assistant_dom.json"))==report
            manifest=json.loads(archive.read("manifest.json"))
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
