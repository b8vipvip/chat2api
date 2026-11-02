"""Strict command allowlist for the independently versioned native page tool.

Imported by the authenticated outbound-only Worker device controller. No
arbitrary shell, user-supplied code, ports, filesystem paths or external URLs.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from urllib.parse import urlsplit

TOOL = Path(__file__).with_name("native_page_diagnostic_v1.py")
ROOT = Path("/var/lib/chat2api-worker/controller/native_pages")
INSTALLED = ROOT.parent / "native_page_diagnostic_v1.py"
RID = re.compile(r"^(?:req|resp|imgreq)_[A-Za-z0-9_-]{6,100}$")
WID = re.compile(r"^wrk_[A-Za-z0-9_-]{6,100}$")
JOB = re.compile(r"^[a-f0-9]{32}$")
MAX_ZIP = 18 * 1024 * 1024
CHUNK = 65536

def _version():
    path = INSTALLED if INSTALLED.is_file() else TOOL
    if not path.is_file():return {"installed":False,"version":None}
    try:
        p=subprocess.run([sys.executable,str(path),"--version"],capture_output=True,text=True,timeout=4)
        return {"installed":p.returncode==0,"version":p.stdout.strip()[:30] if p.returncode==0 else None,
                "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    except (OSError,subprocess.TimeoutExpired):return {"installed":False,"version":None}

def _folder(job):
    if not JOB.fullmatch(str(job or "")):raise ValueError("invalid_job_id")
    return ROOT/job

def _record(job,worker):
    folder=_folder(job)
    item=json.loads((folder/"task.json").read_text())
    if item.get("worker_id")!=worker:raise ValueError("worker_mismatch")
    return folder,item

def execute(command,arguments,worker_id,slot,server):
    if not WID.fullmatch(str(worker_id)):return {"ok":False,"error":"worker_id_invalid"}
    if command=="native_page_version":return {"ok":True,**_version()}
    if command=="native_page_update":
        # The controller is connected to this trusted server via its
        # authenticated Worker identity. The source never comes from the UI.
        try:
            origin=urlsplit(server)
            if origin.scheme!="https" and not (origin.scheme=="http" and origin.hostname in ("127.0.0.1","localhost")):
                return {"ok":False,"error":"insecure_tool_origin"}
            manifest=json.load(urlopen(server.rstrip("/")+"/bootstrap/native-page-diagnostic.json",timeout=15))
            data=urlopen(server.rstrip("/")+"/bootstrap/native-page-diagnostic.py",timeout=25).read(250000)
            digest=hashlib.sha256(data).hexdigest()
            if len(data)>200000 or digest!=manifest.get("sha256"):raise ValueError("tool_sha256_mismatch")
            ROOT.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,name=tempfile.mkstemp(prefix=".native-",dir=ROOT.parent)
            try:
                os.write(fd,data);os.fchmod(fd,0o600);os.close(fd);fd=-1
                os.replace(name,INSTALLED)
            finally:
                if fd!=-1:os.close(fd)
                if os.path.exists(name):os.unlink(name)
            return {"ok":True,**_version(),"updated":True}
        except Exception:
            return {"ok":False,"error":"verified_tool_update_failed"}
    try:
        if command=="native_page_start":
            job=str(arguments.get("job_id") or "")
            req=str(arguments.get("request_id") or "")
            url=str(arguments.get("conversation_url") or "")
            if not JOB.fullmatch(job) or not RID.fullmatch(req) or len(url)>240:
                return {"ok":False,"error":"invalid_job_or_request"}
            # Parse/validate URL in the tool, but never let untrusted paths be
            # interpreted as a command argument or process option.
            script=INSTALLED if INSTALLED.is_file() else TOOL
            if not script.is_file():return {"ok":False,"error":"tool_not_installed"}
            ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
            folder=_folder(job)
            folder.mkdir(mode=0o700,exist_ok=False)
            (folder/"task.json").write_text(json.dumps({"worker_id":worker_id,"request_id":req}))
            (folder/"status.json").write_text(json.dumps({"status":"running","stage":"queued","percent":1,"tool_version":_version().get("version")}))
            with (folder/"collector.log").open("wb") as log:
                process=subprocess.Popen([sys.executable,str(script),"--output",str(folder),
                    "--worker-id",worker_id,"--request-id",req,"--cdp-port",str(9221+int(slot)),
                    "--conversation-url",url],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                    start_new_session=True,close_fds=True)
            (folder/"pid").write_text(str(process.pid))
            return {"ok":True,"job_id":job,"pid_started":True,"tool_version":_version().get("version")}
        if command in {"native_page_status","native_page_chunk"}:
            job=str(arguments.get("job_id") or "")
            folder,item=_record(job,worker_id)
            state=json.loads((folder/"status.json").read_text())
            if command=="native_page_status":
                return {"ok":True,"job_id":job,**{k:state.get(k) for k in (
                   "status","stage","percent","error","tool_version","updated_at",
                   "size","sha256","counts","screenshots")}}
            if state.get("status")!="completed":return {"ok":False,"error":"not_completed"}
            archive=folder/"native-page-evidence.zip"
            length=archive.stat().st_size
            if length>MAX_ZIP:return {"ok":False,"error":"oversized_evidence"}
            index=arguments.get("index")
            if type(index) is not int or index<0 or index>=math.ceil(length/CHUNK):
                return {"ok":False,"error":"invalid_chunk_index"}
            with archive.open("rb") as f:
                f.seek(index*CHUNK)
                part=f.read(CHUNK)
            return {"ok":True,"index":index,"count":math.ceil(length/CHUNK),
                "byte_length":length,"sha256":state.get("sha256"),
                "data":base64.b64encode(part).decode()}
        return {"ok":False,"error":"native_page_command_not_allowed"}
    except FileExistsError:
        return {"ok":False,"error":"job_already_exists"}
    except (ValueError,KeyError,TypeError,FileNotFoundError):
        return {"ok":False,"error":"invalid_or_missing_job"}
    except (OSError,subprocess.SubprocessError):
        return {"ok":False,"error":"tool_execution_failed"}
