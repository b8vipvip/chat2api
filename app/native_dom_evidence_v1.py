"""Private, request-bound visual DOM evidence. Observational, not terminal authority.

Data minimization: only sanitized *assistant reply* text/layout from the
request-owned Worker; no DOM HTML, URLs, cookies, source attributes or image
bytes. Export requires an administrator credential; records expire after 7 days.
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path

ID = re.compile(r"^(?:req|imgreq|resp)_[A-Za-z0-9_-]{6,100}$")
TAGS = set("reply #text div section article p span h1 h2 h3 h4 h5 h6 strong em ul ol li table thead tbody tr td th pre code figure figcaption img blockquote a hr br".split())
SOURCE_TYPES = {"blob", "data", "https", "other"}
MAX_JSON_BYTES = 350_000
MAX_NODES = 1400
MAX_CHARS = 120_000
RETENTION = 7 * 86400


def validate_evidence(value: object, expected_request_id: str) -> dict:
    if not ID.fullmatch(expected_request_id):
        raise ValueError("Invalid request ID")
    if not isinstance(value, dict) or set(value) - {
        "schema", "request_id", "captured_at", "scope", "boundary",
        "complete", "counts", "root", "reason",
    }:
        raise ValueError("Unknown evidence fields")
    if value.get("schema") != "request-native-dom-evidence-v1" or value.get("request_id") != expected_request_id:
        raise ValueError("Mismatched evidence request")
    if value.get("scope") not in ("roleless_reply_siblings", "candidate_only"):
        raise ValueError("Unknown reply scope")
    if value.get("complete") is True and value.get("scope") != "roleless_reply_siblings":
        raise ValueError("Partial candidate claimed complete")
    if type(value.get("complete")) is not bool or not isinstance(value.get("boundary"), str) or len(value["boundary"]) > 100:
        raise ValueError("Invalid evidence status")
    if not isinstance(value.get("captured_at"), str) or len(value["captured_at"]) > 48:
        raise ValueError("Invalid capture time")
    counts=value.get("counts")
    keys={"nodes", "text_chars", "images", "grids", "flex", "tables", "headings", "links"}
    if not isinstance(counts,dict) or set(counts)!=keys or any(
        type(v) is not int or v<0 or v>150000 for v in counts.values()
    ):
        raise ValueError("Invalid counts")
    if counts["nodes"]>MAX_NODES or counts["text_chars"]>MAX_CHARS:
        raise ValueError("Capture exceeds node/text limits")
    raw=json.dumps(value,ensure_ascii=False,separators=(",",":")).encode("utf8")
    if len(raw)>MAX_JSON_BYTES:
        raise ValueError("Capture exceeds byte limit")
    if value.get("root") is None:
        if value["complete"] is True:
            raise ValueError("Complete evidence must have a tree")
        return value
    state={"nodes":0,"chars":0}
    def check(node,depth):
        if not isinstance(node,dict) or depth>26 or set(node)-{"tag","text","children","layout","image","link"}:
            raise ValueError("Unsafe tree node")
        tag=node.get("tag")
        if tag not in TAGS:
            raise ValueError("Unsafe tag")
        state["nodes"]+=1
        if state["nodes"]>MAX_NODES+1:
            raise ValueError("Unsafe node count")
        if tag=="#text":
            if set(node)!={"tag","text"} or not isinstance(node["text"],str):
                raise ValueError("Unsafe text")
            state["chars"]+=len(node["text"])
            if state["chars"]>MAX_CHARS:return False
            return True
        if "text" in node:
            raise ValueError("Text not allowed outside text node")
        if "layout" in node:
            box=node["layout"]
            if not isinstance(box,dict) or set(box)!={"x","y","w","h","display","flow","columns","gap","font","weight","align"}:
                raise ValueError("Invalid layout")
            if any(type(box[k]) is not int or abs(box[k])>100000 for k in ("x","y","w","h","gap","font","weight")):
                raise ValueError("Invalid geometry")
            if box["display"] not in ("block","inline","inline-block","flex","inline-flex","grid","inline-grid","table","table-row","table-cell","list-item","other"):
                raise ValueError("Unsafe display")
            if any(not isinstance(box[k],str) or len(box[k])>limit for k,limit in (("flow",16),("columns",160),("align",16))):
                raise ValueError("Unsafe style string")
            # Never accept CSS functions/url() or custom selectors in layouts.
            if any(c in box["columns"].lower() for c in ("url(","var(",";","{","}")):
                raise ValueError("Unsafe columns")
        elif tag!="reply":
            raise ValueError("Missing layout")
        if "image" in node:
            data=node["image"]
            if tag!="img" or not isinstance(data,dict) or set(data)!={"natural_width","natural_height","loaded","source_type"} or type(data["loaded"]) is not bool or data["source_type"] not in SOURCE_TYPES or any(type(data[k]) is not int or data[k]<0 or data[k]>100000 for k in ("natural_width","natural_height")):
                raise ValueError("Invalid image facts")
        if "link" in node and (tag!="a" or node["link"] is not True):
            raise ValueError("Invalid link facts")
        children=node.get("children",[])
        if not isinstance(children,list) or len(children)>1400 or (tag=="img" and children):
            raise ValueError("Invalid children")
        for child in children:
            if not check(child,depth+1):raise ValueError("Text budget")
        return True
    check(value["root"],0)
    return value


class NativeDomEvidenceStore:
    def __init__(self, data_dir: Path):
        self.root=Path(data_dir)/"native_dom_evidence"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root,0o700)
        self._write_count=0

    def _prune(self):
        # Bounded retention: old evidence must not consume the Worker's disk
        # indefinitely when no administrator manually exports past requests.
        paths=sorted(self.root.glob("req_*.json"),key=lambda p:p.stat().st_mtime)
        now=time.time()
        for file in paths:
            try:
                if now-file.stat().st_mtime>RETENTION:
                    file.unlink(missing_ok=True)
            except OSError:
                pass
        files=sorted(self.root.glob("req_*.json"),key=lambda p:p.stat().st_mtime)
        for file in files[:-512]:
            file.unlink(missing_ok=True)

    def _path(self,request_id):
        if not ID.fullmatch(request_id):raise ValueError("Invalid request ID")
        return self.root/(request_id+".json")
    def save(self,request_id,owner,report):
        if not owner:return False
        data=validate_evidence(report,request_id)
        path=self._path(request_id)
        if path.exists():
            try:
                old=json.loads(path.read_text(encoding="utf8"))
                if old.get("owner")!=owner:return False
            except (OSError, ValueError):return False
        from uuid import uuid4
        tmp=path.with_name("."+uuid4().hex+".tmp")
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,"w",encoding="utf8") as handle:
                json.dump({"owner":owner,"report":data},handle,ensure_ascii=False)
                handle.flush();os.fsync(handle.fileno())
            os.replace(tmp,path)
        finally:
            tmp.unlink(missing_ok=True)
        self._write_count+=1
        if self._write_count%16==0:
            try:
                self._prune()
            except OSError:
                pass
        return True
    def get(self,request_id,owner):
        path=self._path(request_id)
        try:
            if time.time()-path.stat().st_mtime>RETENTION:
                path.unlink(missing_ok=True);return None
            content=json.loads(path.read_text(encoding="utf8"))
            if content.get("owner")!=owner:return None
            return validate_evidence(content.get("report"),request_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None
    def summary(self,request_id,owner):
        # Older retained request rows and fixtures can have noncanonical IDs.
        # Reporting absence must not break request list/ZIP export.
        try:
            value=self.get(request_id,owner)
        except (ValueError,TypeError):
            value=None
        return {"available":bool(value),"scope":value.get("scope") if value else None,
                "complete":value.get("complete") if value else False,
                "counts":value.get("counts") if value else None,
                "reason":value.get("reason") if value else None}
