"""Administrator-owned, request-bound native page diagnosis jobs.

Uses the existing authenticated Linux Worker controller command channel. The
diagnosis has independent lifecycle and cannot mutate chat2api request status.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
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
from .native_page_windows_bridge import WindowsNativePageBridge
from .native_page_tool_release import (
    DiagnosticReleaseSource, DiagnosticReleaseUnavailable,
    RELEASE_BRANCH,
)

REQUEST_ID = re.compile(r"^(?:req|resp|imgreq)_[A-Za-z0-9_-]{6,100}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
WORKER_ID = re.compile(r"^wrk_[A-Za-z0-9_-]{6,100}$")
EXT_WORKER_ID = re.compile(r"^ext_[A-Za-z0-9_-]{5,120}$")
MAX_ARCHIVE = 18 * 1024 * 1024
POLL_SECONDS = 2

def install_native_page_diagnostics(app: FastAPI) -> FastAPI:
    if getattr(app.state,"native_page_diagnostics_installed",False):return app
    app.state.native_page_diagnostics_installed=True
    root=Path(app.state.settings.data_dir)/"native_page_diagnostics"
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    root.chmod(0o700)
    jobs:dict[str,asyncio.Task]={}
    locks:dict[str,str]={}
    releases = DiagnosticReleaseSource()
    windows_bridge=WindowsNativePageBridge(app.state.registry)
    app.state.native_page_windows_ingest=windows_bridge.ingest

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
        visible={key:job.get(key) for key in ("job_id","worker_id","target_type",
            "target_label","status","stage","percent","error","tool_version",
            "counts","screenshots","size","created_at","updated_at","page_probe")}
        # URL-only tasks use a private internal request key for authenticated
        # Worker ZIP ownership. Never present it as a real API request ID.
        visible["worker_id"]=job.get("display_worker_id") or job.get("worker_id")
        visible["worker_platform"]=job.get("worker_platform") or "linux"
        visible["request_id"]=job.get("original_request_id",
            job.get("request_id") if job.get("target_type") != "conversation_url" else "")
        return visible

    def sanitized_chatgpt_url(value):
        raw=str(value or "").strip()
        if not raw or len(raw)>240:return ""
        try:
            parsed=urlsplit(raw)
            if (parsed.scheme=="https" and parsed.hostname in
                ("chatgpt.com","www.chatgpt.com","chat.openai.com") and
                parsed.port in (None,443) and not parsed.username and
                not parsed.password and not parsed.query and not parsed.fragment and
                re.fullmatch(r"/c/[A-Za-z0-9-]{12,90}/?",parsed.path)):
                return "https://"+parsed.hostname+parsed.path
        except ValueError:pass
        return ""

    def linux_client(worker):
        entry=app.state.linux_workers.data.get("workers",{}).get(worker) or {}
        return str(entry.get("extension_client_id") or "").strip()

    def normalize_worker(worker):
        if WORKER_ID.fullmatch(worker):
            entry=app.state.linux_workers.data.get("workers",{}).get(worker)
            if entry and not entry.get("revoked_at"):
                return worker,"linux",linux_client(worker) or worker
            raise HTTPException(404,"Worker does not exist")
        if not EXT_WORKER_ID.fullmatch(worker):
            raise HTTPException(400,"Invalid Worker")
        client=app.state.registry.clients.get(worker)
        if not client or not client.connection_enabled:
            raise HTTPException(404,"Worker does not exist or is disabled")
        for wid,entry in app.state.linux_workers.data.get("workers",{}).items():
            if not entry.get("revoked_at") and entry.get("extension_client_id")==worker:
                return wid,"linux",worker
        meta=client.metadata if isinstance(client.metadata,dict) else {}
        if meta.get("linux_worker_id") or meta.get("controller_worker_id"):
            raise HTTPException(409,"Linux bridge is not bound")
        return worker,"windows",worker

    def check_worker(worker):
        canonical,platform,_=normalize_worker(worker)
        if platform=="windows":
            if canonical not in app.state.registry.sockets:
                raise HTTPException(409,"Windows extension is offline")
            client=app.state.registry.clients.get(canonical)
            meta=client.metadata if isinstance(client.metadata,dict) else {}
            if meta.get("windows_native_page_diagnostic_version")!="windows-dom-1.0.0":
                raise HTTPException(409,"Windows Worker extension needs native diagnosis upgrade")
            return {"worker_id":canonical}
        return app.state.linux_workers.data["workers"][canonical]

    async def command(worker,action,arguments,timeout=30):
        response=await app.state.send_linux_worker_command(
            worker,action,arguments,wait=True,timeout=timeout)
        item=response.get("result") or {}
        if item.get("ok") is not True:
            raise RuntimeError("worker_"+str(item.get("error") or "command_failed")[:90])
        return item

    def history_match_hints(row):
        """Only hashes of generated assistant text, never the prompt or raw text."""
        raw=str(row.get("response_text") or "")
        compact="".join(ch.lower() for ch in raw if ch.isalnum())[:200000]
        digests=[]
        if len(compact)>=240:
            for frac in (0.07,0.23,0.41,0.59,0.77,0.91):
                at=min(len(compact)-48,int(len(compact)*frac))
                sha=hashlib.sha256(compact[at:at+48].encode()).hexdigest()
                if sha not in digests:digests.append(sha)
        stamps=[]
        for field in ("created_at","recorded_at","updated_at"):
            value=str(row.get(field) or "")
            try:
                epoch=datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
                if epoch>1600000000 and epoch<time.time()+86400:stamps.append(epoch)
            except ValueError:pass
        duration=float((row.get("timings") or {}).get("total_ms") or 0)/1000
        if not stamps:return {"reference_hashes":digests,"history_start":0,"history_end":0}
        earliest=min(stamps)
        # Older records often stamp the terminal, not request admission.
        # Permit its recorded total duration plus a small clock-skew margin.
        start=int(earliest-max(duration,0)-180)
        end=int(max(stamps)+120)
        if end-start>7200:
            start=end-7200
        return {"reference_hashes":digests,"history_start":start,"history_end":end}

    async def run(job_id,url,hints=None):
        record=read(job_id)
        worker=record["worker_id"]
        if record.get("worker_platform")=="windows":
            return await run_windows(job_id,url)
        try:
            result=await command(worker,"native_page_start",{
                "job_id":job_id,"request_id":record["request_id"],
                "conversation_url":url,
                **(hints or {}),
            },timeout=20)
            record["tool_version"]=result.get("tool_version")
            write(record)
            for turn in range(180):
                await asyncio.sleep(POLL_SECONDS)
                state=await command(worker,"native_page_status",{"job_id":job_id},timeout=20)
                record.update({key:state.get(key) for key in (
                    "stage","percent","error","tool_version","counts",
                    "screenshots","size") if key in state})
                # Older Worker controllers may return page_probe=None even
                # after a newer collector observed the page. Never erase a
                # previously valid probe with null during progress polling.
                if isinstance(state.get("page_probe"),dict):
                    record["page_probe"]=state["page_probe"]
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
                if (record.get("target_type")=="conversation_url" and
                    manifest.get("request_binding")!="url-only-unbound"):
                    raise RuntimeError("url_only_evidence_provenance_invalid")
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

    async def run_windows(job_id,url):
        record=read(job_id);worker=record["worker_id"]
        try:
            record.update({"stage":"windows-collecting","percent":15,
                "tool_version":"windows-dom-1.0.0"})
            write(record)
            report,raw=await windows_bridge.collect(worker,job_id,url,timeout=155)
            manifest={"schema":"chat2api-native-page-manifest-v1",
                "request_id":record["request_id"],"worker_id":worker,
                "worker_platform":"windows","tool_version":"windows-dom-1.0.0",
                "request_binding":"url-only-unbound" if record["target_type"]=="conversation_url"
                    else "history-link-request-unverified",
                "boundary_verified":report.get("boundary_verified") is True,
                "completeness_verified":report.get("complete") is True,
                "capture_mode":"windows-extension-dom-only",
                "limitations":report.get("limitations") or []}
            buffer=io.BytesIO()
            from zipfile import ZIP_DEFLATED
            with ZipFile(buffer,"w",compression=ZIP_DEFLATED,compresslevel=6) as archive:
                archive.writestr("assistant_dom.json",raw)
                archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False))
                archive.writestr("README.txt",
                    "Windows extension DOM-only diagnosis. No complete screenshot, "
                    "no source image bytes, no live API request binding.\n")
            data=buffer.getvalue()
            if not 0<len(data)<=MAX_ARCHIVE:raise RuntimeError("windows_evidence_zip_budget")
            out=root/(job_id+".zip");out.write_bytes(data);out.chmod(0o600)
            record.update({"status":"completed","stage":"complete","percent":100,
                "size":len(data),"sha256":hashlib.sha256(data).hexdigest(),
                "counts":report.get("counts") or {},"page_probe":report.get("probe") or {},
                "error":None,"updated_at":int(time.time())})
            write(record)
        except Exception as exc:
            record.update({"status":"failed","stage":"failed","percent":100,
                "error":str(exc)[:180],"updated_at":int(time.time())})
            write(record)
        finally:
            locks.pop(worker,None);jobs.pop(job_id,None)

    async def latest_release():
        try:
            return await releases.load()
        except DiagnosticReleaseUnavailable as exc:
            # Never pretend an obsolete image-bundled script is the latest
            # independent release, and never run an arbitrary fallback URL.
            raise HTTPException(503, str(exc)) from None

    @app.get("/bootstrap/native-page-diagnostic.py",include_in_schema=False)
    async def tool_source(sha256:str=""):
        data, manifest = await latest_release()
        if sha256 and sha256 != manifest["sha256"]:
            raise HTTPException(409, "diagnostic_release_changed_retry")
        return Response(data,media_type="text/x-python",
                        headers={"Cache-Control":"no-store",
                                 "X-Content-SHA256": manifest["sha256"]})

    @app.get("/bootstrap/native-page-diagnostic.json",include_in_schema=False)
    async def tool_manifest():
        _, manifest = await latest_release()
        return manifest

    @app.get("/api/admin/worker-tools/native-page/latest")
    async def latest_version(request:Request):
        authorize(request)
        _,manifest=await latest_release()
        return {"tool":manifest}

    @app.get("/api/admin/worker-tools/native-page/jobs")
    async def list_jobs(request:Request,limit:int=40):
        authorize(request)
        records=[]
        for p in sorted(root.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:min(100,max(1,limit))]:
            try:
                item=json.loads(p.read_text(encoding="utf8"))
                # A server restart cancels in-process pollers. Do not leave
                # stale tasks eternally appearing as running.
                if (item.get("status")=="running" and item.get("job_id") not in jobs
                    and time.time()-int(item.get("updated_at") or 0)>60):
                    item.update({"status":"interrupted","stage":"server-restarted",
                        "error":"diagnosis_poll_interrupted_restart",
                        "updated_at":int(time.time())})
                    write(item)
                records.append(public(item))
            except (ValueError,OSError,KeyError):pass
        data=(Path(__file__).parents[1]/"scripts"/"native_page_diagnostic_v1.py").read_bytes()
        match=re.search(rb'(?m)^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',data)
        version=match.group(1).decode("ascii") if match else "unknown"
        return {"data":records,"tool":"native-page-diagnostic","version":version}

    @app.get("/api/admin/worker-tools/native-page/workers")
    async def eligible_workers(request:Request):
        authorize(request)
        rows=[];linux_clients=set()
        for item in app.state.linux_workers.list_public():
            if item.get("revoked_at"):continue
            wid=str(item.get("worker_id") or "")
            if not WORKER_ID.fullmatch(wid):continue
            client=linux_client(wid)
            if not EXT_WORKER_ID.fullmatch(client):continue
            linux_clients.add(client)
            controller=str((item.get("metadata") or {}).get("controller_worker_id") or wid)
            rows.append({"worker_id":client,"platform":"linux",
                         "name":str(item.get("name") or "")[:80],
                         "online":controller in app.state.worker_sockets,
                         "status":str(item.get("status") or "")[:32],
                         "worker_slot":(item.get("metadata") or {}).get("worker_slot")})
        for cid,client in app.state.registry.clients.items():
            if cid in linux_clients or not EXT_WORKER_ID.fullmatch(str(cid)):
                continue
            meta=client.metadata if isinstance(client.metadata,dict) else {}
            if meta.get("linux_worker_id") or meta.get("controller_worker_id"):
                continue
            if not client.connection_enabled:continue
            rows.append({"worker_id":cid,"platform":"windows",
                "name":str(client.name or "")[:80],
                "online":cid in app.state.registry.sockets,
                "status":"online" if cid in app.state.registry.sockets else "offline",
                "diagnostic_supported":meta.get("windows_native_page_diagnostic_version")=="windows-dom-1.0.0",
                "worker_slot":meta.get("worker_slot")})
        return {"data":rows}

    @app.get("/api/admin/worker-tools/native-page/request-worker/{request_id}")
    async def request_worker(request_id:str,request:Request):
        authorize(request)
        if not REQUEST_ID.fullmatch(request_id):raise HTTPException(422,"Invalid request ID")
        row=app.state.telemetry.get(request_id)
        if not row:raise HTTPException(404,"Request not found in server records")
        # The only eligible historical URL was sanitized at the Worker
        # terminal. Never expose query strings, tokens or arbitrary origin.
        saved=str((row.get("diagnostics") or {}).get("native_chatgpt_conversation_url") or "")
        try:
            p=urlsplit(saved)
            if (p.scheme!="https" or p.hostname not in (
                "chatgpt.com","www.chatgpt.com","chat.openai.com") or
                p.port not in (None,443) or p.username or p.password or
                p.query or p.fragment or not re.fullmatch(
                    r"/c/[A-Za-z0-9-]{12,90}/?",p.path)):
                saved=""
        except ValueError:saved=""
        owner=str(row.get("linux_worker_id") or row.get("worker_client_id")
                  or row.get("client_id") or "")
        if WORKER_ID.fullmatch(owner):
            owner=linux_client(owner) or ""
        return {"request_id":request_id,"worker_id":owner,
                "worker_slot":row.get("worker_slot"),
                "conversation_url":saved,
                "conversation_link_saved":bool(saved)}

    @app.post("/api/admin/worker-tools/native-page/jobs")
    async def create(request:Request):
        authorize(request)
        body=await request.json()
        # New UI sends one input (target). Old API clients can still provide
        # request_id plus optional conversation_url for compatibility.
        target=str(body.get("target") or body.get("request_id") or "").strip()
        explicit=str(body.get("conversation_url") or "").strip()
        is_url=target.startswith(("https://","http://"))
        if is_url:
            url=sanitized_chatgpt_url(target)
            if not url or explicit:
                raise HTTPException(422,"Only exact HTTPS ChatGPT /c/ URLs allowed")
            row=None
            original_request_id=""
            worker=str(body.get("worker_id") or "").strip()
        else:
            if not REQUEST_ID.fullmatch(target):
                raise HTTPException(422,"Enter a complete request ID or ChatGPT history URL")
            row=app.state.telemetry.get(target)
            if not row:raise HTTPException(404,"Request not found in server records")
            original_request_id=target
            saved=sanitized_chatgpt_url(
                (row.get("diagnostics") or {}).get("native_chatgpt_conversation_url"))
            if explicit and not sanitized_chatgpt_url(explicit):
                raise HTTPException(422,"Only exact HTTPS ChatGPT /c/ URLs allowed")
            url=sanitized_chatgpt_url(explicit) if explicit else saved
            original_worker=str(row.get("linux_worker_id") or
                row.get("worker_client_id") or row.get("client_id") or "")
            worker=str(body.get("worker_id") or original_worker).strip()
            if original_worker and normalize_worker(worker)[0]!=normalize_worker(original_worker)[0]:
                raise HTTPException(409,"Chosen Worker does not own this request")
        worker,platform,display=normalize_worker(worker)
        check_worker(worker)
        if worker in locks:raise HTTPException(409,"This Worker already has a diagnosis running")
        if platform=="linux" and worker not in app.state.worker_sockets and not any(
            str((v.get("metadata") or {}).get("controller_worker_id")) in app.state.worker_sockets
            for k,v in app.state.linux_workers.data["workers"].items() if k==worker):
            raise HTTPException(409,"Worker device controller is offline")
        job_id=uuid.uuid4().hex
        # Required by the existing fixed-schema Worker command and ZIP owner
        # validator. The synthetic key is never claimed as a real request.
        internal_request_id=original_request_id or ("req_diag_"+job_id)
        record={"job_id":job_id,"request_id":internal_request_id,
                "original_request_id":original_request_id,
                "target_type":"conversation_url" if is_url else "request_id",
                "target_label":url if is_url else original_request_id,
                "worker_id":worker,"display_worker_id":display,"worker_platform":platform,
                "status":"running","stage":"queued",
                "percent":0,"tool_version":None,
                "created_at":int(time.time()),"updated_at":int(time.time())}
        write(record);locks[worker]=job_id
        jobs[job_id]=asyncio.create_task(run(
            job_id,url,history_match_hints(row) if row else None))
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
                f'attachment; filename="native-page-{job.get("original_request_id") or "history-url"}-{job_id[:8]}-v{job["tool_version"]}.zip"'})

    @app.get("/api/admin/worker-tools/native-page/jobs/{job_id}/log")
    async def download_log(job_id:str,request:Request):
        authorize(request)
        record=read(job_id)
        safe={key:record.get(key) for key in (
            "job_id","original_request_id","target_type","target_label","worker_id","status","stage","percent",
            "error","tool_version","counts","screenshots","size",
            "created_at","updated_at","page_probe")}
        safe["worker_id"]=record.get("display_worker_id") or record.get("worker_id")
        safe["worker_platform"]=record.get("worker_platform") or "linux"
        return Response(json.dumps({"schema":"chat2api-native-page-diagnostic-log-v1",
            "record":safe},ensure_ascii=False,indent=2),
            media_type="application/json",
            headers={"Cache-Control":"no-store","Content-Disposition":
                f'attachment; filename="native-page-{job_id[:8]}-diagnostic.json"'})

    @app.post("/api/admin/worker-tools/native-page/workers/{worker_id}/update")
    async def update(worker_id:str,request:Request):
        authorize(request)
        actual,platform,display=normalize_worker(worker_id)
        if platform=="windows":
            raise HTTPException(409,"Windows diagnostic is bundled with its Chrome extension; update the extension")
        check_worker(actual)
        _,manifest=await latest_release()
        result=await command(actual,"native_page_update",{},timeout=60)
        if result.get("sha256")!=manifest["sha256"]:
            raise HTTPException(409,"worker_installed_release_sha_mismatch_retry")
        return {"worker_id":display,"tool":result,"latest":manifest}

    @app.get("/api/admin/worker-tools/native-page/workers/{worker_id}/version")
    async def version(worker_id:str,request:Request):
        authorize(request)
        actual,platform,display=normalize_worker(worker_id)
        if platform=="windows":
            client=app.state.registry.clients.get(actual)
            meta=client.metadata if client and isinstance(client.metadata,dict) else {}
            current=str(meta.get("windows_native_page_diagnostic_version") or "")
            return {"worker_id":display,"tool":{"version":current or "未安装",
                "installed":current=="windows-dom-1.0.0",
                "capture_mode":"extension-dom-only",
                "note":"Windows diagnostic code is bundled with the Chrome extension"},
                "latest":{"version":"windows-dom-1.0.0"}}
        check_worker(actual)
        result=await command(actual,"native_page_version",{},timeout=15)
        try:
            _,manifest=await latest_release()
            return {"worker_id":display,"tool":result,"latest":manifest}
        except HTTPException as exc:
            return {"worker_id":display,"tool":result,
                    "latest_error":str(exc.detail)[:100]}
    return app
