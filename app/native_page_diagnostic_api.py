"""Administrator-owned, request-bound native page diagnosis jobs.

Uses the existing authenticated Linux Worker controller command channel. The
diagnosis has independent lifecycle and cannot mutate chat2api request status.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit
from zipfile import ZipFile, BadZipFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE

REQUEST_ID = re.compile(r"^(?:req|resp|imgreq)_[A-Za-z0-9_-]{6,100}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
WORKER_ID = re.compile(r"^wrk_[A-Za-z0-9_-]{6,100}$")
MAX_ARCHIVE = 18 * 1024 * 1024
POLL_SECONDS = 2

def install_native_page_diagnostics(app: FastAPI) -> FastAPI:
    if getattr(app.state,"native_page_diagnostics_installed",False):return app
    app.state.native_page_diagnostics_installed=True
    root=Path(app.state.settings.data_dir)/"native_page_diagnostics"
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    jobs:dict[str,asyncio.Task]={}
    locks:dict[str,str]={}

    def authorize(request:Request):
        token=str(request.headers.get("authorization") or "")
        master=str(app.state.settings.api_key or "")
        if master and secrets.compare_digest(token,"Bearer "+master):return
        sessions=getattr(app.state,"admin_sessions",None)
        if sessions and sessions.authenticate(request.cookies.get(SESSION_COOKIE)):return
        raise HTTPException(401,"Administrator authentication required")

    def jobpath(job_id):
        if not JOB_ID.fullmatch(job_id):raise HTTPException(400,"Invalid diagnosis job ID")
        return root/(job_id+".json")

    def read(job_id):
        path=jobpath(job_id)
        if not path.is_file():raise HTTPException(404,"Diagnosis job not found")
        return json.loads(path.read_text(encoding="utf8"))

    def write(job):
        path=jobpath(job["job_id"])
        tmp=path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job,ensure_ascii=False),encoding="utf8")
        tmp.replace(path)

    def public(job):
        return {key:job.get(key) for key in ("job_id","request_id","worker_id",
            "status","stage","percent","error","tool_version","counts",
            "screenshots","size","created_at","updated_at")}

    def check_worker(worker):
        if not WORKER_ID.fullmatch(worker):raise HTTPException(400,"Invalid Worker")
        source=app.state.linux_workers.data.get("workers",{}).get(worker)
        if not source or source.get("revoked_at"):
            raise HTTPException(404,"Worker does not exist")
        return source

    async def command(worker,action,arguments,timeout=30):
        response=await app.state.send_linux_worker_command(
            worker,action,arguments,wait=True,timeout=timeout)
        item=response.get("result") or {}
        if item.get("ok") is not True:
            raise RuntimeError("worker_"+str(item.get("error") or "command_failed")[:90])
        return item

    async def run(job_id,url):
        record=read(job_id)
        worker=record["worker_id"]
        try:
            result=await command(worker,"native_page_start",{
                "job_id":job_id,"request_id":record["request_id"],
                "conversation_url":url,
            },timeout=20)
            record["tool_version"]=result.get("tool_version")
            write(record)
            for turn in range(180):
                await asyncio.sleep(POLL_SECONDS)
                state=await command(worker,"native_page_status",{"job_id":job_id},timeout=20)
                record.update({key:state.get(key) for key in (
                    "stage","percent","error","tool_version","counts",
                    "screenshots","size") if key in state})
                record["updated_at"]=int(time.time())
                if state.get("status")=="failed":
                    raise RuntimeError(str(state.get("error") or "capture_failed")[:170])
                if state.get("status")=="completed":
                    break
                write(record)
            else:
                raise RuntimeError("capture_poll_timeout")
            size=int(record.get("size") or 0)
            if not 0<size<=MAX_ARCHIVE:
                raise RuntimeError("invalid_evidence_archive_size")
            record.update({"status":"running","stage":"transfer","percent":85})
            write(record)
            total=(size+65535)//65536
            data=bytearray()
            expected=None
            for i in range(total):
                packet=await command(worker,"native_page_chunk",{"job_id":job_id,"index":i},timeout=25)
                if packet.get("index")!=i or packet.get("count")!=total or packet.get("byte_length")!=size:
                    raise RuntimeError("evidence_chunk_metadata_mismatch")
                if expected is None:expected=packet.get("sha256")
                if packet.get("sha256")!=expected:
                    raise RuntimeError("evidence_chunk_digest_changed")
                piece=base64.b64decode(packet["data"],validate=True)
                if len(piece)!=min(65536,size-i*65536):
                    raise RuntimeError("evidence_chunk_length_mismatch")
                data.extend(piece)
                if i%5==0 or i+1==total:
                    record.update({"percent":85+int(14*(i+1)/total),"updated_at":int(time.time())})
                    write(record)
            if len(data)!=size or hashlib.sha256(data).hexdigest()!=expected:
                raise RuntimeError("evidence_sha256_failed")
            with ZipFile(io.BytesIO(data)) as archive:
                names=set(archive.namelist())
                if not {"assistant_dom.json","manifest.json"}.issubset(names):
                    raise RuntimeError("evidence_archive_missing_manifest")
                manifest=json.loads(archive.read("manifest.json"))
                if manifest.get("request_id")!=record["request_id"] or manifest.get("worker_id")!=worker:
                    raise RuntimeError("evidence_archive_owner_mismatch")
            out=root/(job_id+".zip")
            out.write_bytes(data);out.chmod(0o600)
            record.update({"status":"completed","stage":"complete","percent":100,
                "size":size,"sha256":expected,"error":None,"updated_at":int(time.time())})
            write(record)
        except Exception as exc:
            record.update({"status":"failed","stage":"failed","percent":100,
                "error":str(exc)[:180],"updated_at":int(time.time())})
            write(record)
        finally:
            locks.pop(worker,None)
            jobs.pop(job_id,None)

    @app.get("/bootstrap/native-page-diagnostic.py",include_in_schema=False)
    async def tool_source():
        file=Path(__file__).parents[1]/"scripts"/"native_page_diagnostic_v1.py"
        return Response(file.read_bytes(),media_type="text/x-python",
                        headers={"Cache-Control":"no-store"})

    @app.get("/bootstrap/native-page-diagnostic.json",include_in_schema=False)
    async def tool_manifest():
        data=(Path(__file__).parents[1]/"scripts"/"native_page_diagnostic_v1.py").read_bytes()
        return {"version":"1.0.0","sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}

    @app.get("/api/admin/worker-tools/native-page/jobs")
    async def list_jobs(request:Request,limit:int=40):
        authorize(request)
        records=[]
        for p in sorted(root.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:min(100,max(1,limit))]:
            try:records.append(public(json.loads(p.read_text(encoding="utf8"))))
            except (ValueError,OSError,KeyError):pass
        return {"data":records,"tool":"native-page-diagnostic","version":"1.0.0"}

    @app.get("/api/admin/worker-tools/native-page/request-worker/{request_id}")
    async def request_worker(request_id:str,request:Request):
        authorize(request)
        if not REQUEST_ID.fullmatch(request_id):raise HTTPException(422,"Invalid request ID")
        row=app.state.telemetry.get(request_id)
        if not row:raise HTTPException(404,"Request not found in server records")
        return {"request_id":request_id,"worker_id":str(row.get("linux_worker_id") or ""),
                "worker_slot":row.get("worker_slot"),
                "conversation_link_saved":bool((row.get("diagnostics") or {}).get("conversation_url"))}

    @app.post("/api/admin/worker-tools/native-page/jobs")
    async def create(request:Request):
        authorize(request)
        body=await request.json()
        request_id=str(body.get("request_id") or "").strip()
        url=str(body.get("conversation_url") or "").strip()
        if not REQUEST_ID.fullmatch(request_id):raise HTTPException(422,"Invalid request ID")
        row=app.state.telemetry.get(request_id)
        if not row:raise HTTPException(404,"Request not found in server records")
        original_worker=str(row.get("linux_worker_id") or "")
        worker=str(body.get("worker_id") or original_worker).strip()
        if original_worker and worker!=original_worker:
            raise HTTPException(409,"Chosen Worker does not own this request")
        check_worker(worker)
        if url:
            parsed=urlsplit(url)
            if parsed.scheme!="https" or parsed.hostname not in (
                "chatgpt.com","www.chatgpt.com","chat.openai.com") or parsed.query or parsed.fragment or not re.fullmatch(r"/c/[A-Za-z0-9-]{12,90}/?",parsed.path):
                raise HTTPException(422,"Only exact ChatGPT /c/ conversation URLs allowed")
        if worker in locks:raise HTTPException(409,"This Worker already has a diagnosis running")
        if worker not in app.state.worker_sockets and not any(
            str((v.get("metadata") or {}).get("controller_worker_id")) in app.state.worker_sockets
            for k,v in app.state.linux_workers.data["workers"].items() if k==worker):
            raise HTTPException(409,"Worker device controller is offline")
        job_id=uuid.uuid4().hex
        record={"job_id":job_id,"request_id":request_id,"worker_id":worker,
                "status":"running","stage":"queued","percent":0,
                "tool_version":None,"created_at":int(time.time()),"updated_at":int(time.time())}
        write(record);locks[worker]=job_id
        jobs[job_id]=asyncio.create_task(run(job_id,url))
        return {"job":public(record)}

    @app.get("/api/admin/worker-tools/native-page/jobs/{job_id}")
    async def detail(job_id:str,request:Request):
        authorize(request);return {"job":public(read(job_id))}

    @app.get("/api/admin/worker-tools/native-page/jobs/{job_id}/download")
    async def download(job_id:str,request:Request):
        authorize(request)
        job=read(job_id)
        path=root/(job_id+".zip")
        if job.get("status")!="completed" or not path.is_file():
            raise HTTPException(409,"Diagnosis archive not ready")
        return Response(path.read_bytes(),media_type="application/zip",
            headers={"Cache-Control":"no-store","Content-Disposition":
                f'attachment; filename="native-page-{job["request_id"]}-{job_id[:8]}-v{job["tool_version"]}.zip"'})

    @app.post("/api/admin/worker-tools/native-page/workers/{worker_id}/update")
    async def update(worker_id:str,request:Request):
        authorize(request);check_worker(worker_id)
        result=await command(worker_id,"native_page_update",{},timeout=60)
        return {"worker_id":worker_id,"tool":result}

    @app.get("/api/admin/worker-tools/native-page/workers/{worker_id}/version")
    async def version(worker_id:str,request:Request):
        authorize(request);check_worker(worker_id)
        result=await command(worker_id,"native_page_version",{},timeout=15)
        return {"worker_id":worker_id,"tool":result}
    return app
