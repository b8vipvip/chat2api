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

from .private_storage import (
    ensure_private_directory, open_private_regular_file,
    private_file_lock, write_private_bytes_atomic,
)

ID = re.compile(r"^(?:req|imgreq|resp)_[A-Za-z0-9_-]{6,100}$")
TAGS = set("reply #text div section article p span h1 h2 h3 h4 h5 h6 strong em ul ol li table thead tbody tr td th pre code figure figcaption img blockquote a hr br".split())
SOURCE_TYPES = {"blob", "data", "https", "other"}
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_NODES = 30000
MAX_CHARS = 192_000
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
    # The current Windows-confirmed request-v6 assistant ID route emits
    # verified_native_assistant_selection. That is a verified *single-message*
    # scope, unlike candidate_only; rejecting it silently discards the very
    # forensic tree used to debug real native rich requests.
    trusted_scopes = {"roleless_reply_siblings", "verified_reply_container",
                      "verified_native_assistant_selection"}
    if value.get("scope") not in trusted_scopes | {"candidate_only"}:
        raise ValueError("Unknown reply scope")
    if value.get("complete") is True and value.get("scope") not in trusted_scopes:
        raise ValueError("Partial candidate claimed complete")
    if type(value.get("complete")) is not bool or not isinstance(value.get("boundary"), str) or len(value["boundary"]) > 100:
        raise ValueError("Invalid evidence status")
    if not isinstance(value.get("captured_at"), str) or len(value["captured_at"]) > 48:
        raise ValueError("Invalid capture time")
    counts=value.get("counts")
    base_keys={"nodes", "text_chars", "images", "grids", "flex",
               "tables", "headings", "links"}
    # Formal content_native_dom_evidence_v1.js now emits these four graphics
    # counters on EVERY capture, even if all values are zero. Old diagnostic
    # records contain only base_keys. Admit both exact schemas, not arbitrary
    # partial or extra data, and keep strictly bounded integer facts.
    graphics_keys={"svg", "svg_large", "canvas", "canvas_large"}
    opaque_keys={"opaque_large", "opaque_large_max_w", "opaque_large_max_h"}
    # Legacy captures remain readable. A new Worker can report bounded
    # geometry-only candidates for rendered diagrams hidden behind a closed
    # shadow/embed, without exporting pixels or trusting them as real charts.
    schemas=(base_keys, base_keys | graphics_keys,
             base_keys | graphics_keys | opaque_keys)
    if (not isinstance(counts,dict)
        or set(counts) not in schemas
        or any(type(v) is not int or v<0 or v>150000 for v in counts.values())):
        raise ValueError("Invalid counts")
    if counts["nodes"]>MAX_NODES or counts["text_chars"]>MAX_CHARS:
        raise ValueError("Capture exceeds node/text limits")
    if graphics_keys <= set(counts):
        if (counts["svg_large"]>counts["svg"]
            or counts["canvas_large"]>counts["canvas"]
            or counts["svg"]>counts["nodes"]
            or counts["canvas"]>counts["nodes"]):
            raise ValueError("Inconsistent graphics counts")
    if opaque_keys <= set(counts):
        if (counts["opaque_large"] > 40
            or counts["opaque_large"] > counts["nodes"]
            or counts["opaque_large_max_w"] > 100000
            or counts["opaque_large_max_h"] > 100000
            or (counts["opaque_large"] == 0
                and (counts["opaque_large_max_w"] or counts["opaque_large_max_h"]))
            or (counts["opaque_large"] > 0
                and (counts["opaque_large_max_w"] < 300
                     or counts["opaque_large_max_h"] < 240))):
            raise ValueError("Inconsistent opaque visual evidence")
    raw=json.dumps(value,ensure_ascii=False,separators=(",",":")).encode("utf8")
    if len(raw)>MAX_JSON_BYTES:
        raise ValueError("Capture exceeds byte limit")
    if value.get("root") is None:
        if value["complete"] is True:
            raise ValueError("Complete evidence must have a tree")
        return value
    state={"nodes":0,"chars":0}
    def check(node,depth):
        if not isinstance(node,dict) or depth>66 or set(node)-{"tag","text","children","layout","image","link"}:
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
        if not isinstance(children,list) or len(children)>MAX_NODES or (tag=="img" and children):
            raise ValueError("Invalid children")
        for child in children:
            if not check(child,depth+1):raise ValueError("Text budget")
        return True
    check(value["root"],0)
    return value


class NativeDomEvidenceStore:
    def __init__(self, data_dir: Path):
        self.root=Path(data_dir)/"native_dom_evidence"
        ensure_private_directory(self.root)
        self._write_count=0

    def _regular_files(self):
        files=[]
        for path in self.root.glob("*.json"):
            try:
                fd=open_private_regular_file(path,os.O_RDONLY)
            except (OSError,RuntimeError):
                continue
            try:
                metadata=os.fstat(fd)
                files.append((metadata.st_mtime,path))
            finally:
                os.close(fd)
        files.sort(key=lambda item:item[0])
        return files

    def _prune(self):
        # Bounded retention: old evidence must not consume the Worker's disk
        # indefinitely when no administrator manually exports past requests.
        now=time.time()
        files=self._regular_files()
        for modified,path in files:
            if now-modified>RETENTION:
                path.unlink(missing_ok=True)
        remaining=self._regular_files()
        for _,path in remaining[:-128]:
            path.unlink(missing_ok=True)

    def _path(self,request_id):
        if not ID.fullmatch(request_id):raise ValueError("Invalid request ID")
        return self.root/(request_id+".json")
    def save(self,request_id,owner,report):
        if not owner:return False
        data=validate_evidence(report,request_id)
        path=self._path(request_id)
        payload=json.dumps(
            {"owner":owner,"report":data},ensure_ascii=False,separators=(",",":")
        ).encode("utf8")
        # Owner check and atomic replacement are serialized across processes so
        # two callers cannot race to claim the same request-bound evidence file.
        with private_file_lock(self.root/".native_dom_evidence.lock"):
            try:
                fd=open_private_regular_file(path,os.O_RDONLY)
            except FileNotFoundError:
                fd=None
            except (OSError,RuntimeError):
                return False
            if fd is not None:
                try:
                    with os.fdopen(fd,"r",encoding="utf8") as handle:
                        old=json.load(handle)
                except (OSError,ValueError,TypeError):
                    return False
                if old.get("owner")!=owner:return False
            write_private_bytes_atomic(path,payload)
            self._write_count+=1
            if self._write_count%16==0:
                try:
                    self._prune()
                except (OSError,RuntimeError):
                    pass
        return True
    def get(self,request_id,owner):
        path=self._path(request_id)
        try:
            fd=open_private_regular_file(path,os.O_RDONLY)
        except (OSError,RuntimeError):
            return None
        try:
            with os.fdopen(fd,"r",encoding="utf8") as handle:
                if time.time()-os.fstat(handle.fileno()).st_mtime>RETENTION:
                    expired=True
                    content=None
                else:
                    expired=False
                    content=json.load(handle)
            if expired:
                path.unlink(missing_ok=True)
                return None
            if content.get("owner")!=owner:return None
            return validate_evidence(content.get("report"),request_id)
        except (OSError,ValueError,TypeError,KeyError,RuntimeError):
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
