#!/usr/bin/env python3
"""Versioned, read-only CDP evidence collector for existing ChatGPT replies.

An independent Worker tool: it neither submits prompts nor changes chat2api
request status. Does not read cookies, storage, network bodies or signed URLs.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, quote
from urllib.request import urlopen, Request
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = "1.0.0"
MAX_ZIP = 18 * 1024 * 1024
MAX_NODES = 30000
REQUEST = re.compile(r"^(?:req|resp|imgreq)_[A-Za-z0-9_-]{6,100}$")
CONVERSATION = re.compile(r"^/c/[A-Za-z0-9-]{12,90}/?$")
HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"}
CAPTURE = r"""(function(expected){
'use strict';
const allowed=new Set('div section article p span strong em b i h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br a sup sub'.split(' '));
const forbidden='script,style,form,nav,aside,header,footer,input,textarea,iframe,canvas,svg,[role="menu"],[role="dialog"],[contenteditable="true"],[data-message-author-role="user"],[data-testid="user-message"]';
const visible=e=>{try{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}catch(_){return false;}};
const norm=s=>String(s||'').replace(/\\s+/g,' ').trim();
const hasUser=e=>!!e.querySelector('[data-message-author-role="user"],[data-testid="user-message"]');
let scope='',roots=[];
const assistant=[...document.querySelectorAll('[data-message-author-role="assistant"]')].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&!e.parentElement?.closest('[data-message-author-role="assistant"]'));
if(assistant.length){scope='assistant-role';roots=[assistant[assistant.length-1]];}
else {
 const h=[...document.querySelectorAll('main h4,[role="main"] h4,#thread h4')].filter(e=>e.isConnected&&visible(e)&&/^ChatGPT said\\s*[:：]?$/.test(norm(e.textContent)));
 if(!h.length)return {ok:false,error:'no-verified-assistant-boundary'};
 const heading=h[h.length-1];let at=heading.nextElementSibling;scope='roleless-heading';
 while(at&&roots.length<4096){
   if(at.matches(forbidden)||at.tagName==='H4'&&/^(You said|ChatGPT said)\\s*[:：]?$/.test(norm(at.textContent))||hasUser(at))break;
   roots.push(at);at=at.nextElementSibling;
 }
 if(at&&roots.length>=4096)return {ok:false,error:'reply-sibling-limit'};
}
if(!roots.length)return {ok:false,error:'empty-verified-reply'};
const counters={nodes:0,text_chars:0,tables:0,images:0,max_children:0,headings:0,grids:0,flex:0};
let reason='';
const first=roots[0].getBoundingClientRect();
let bottom=0,left=Infinity,right=0,top=Infinity;
for(const e of roots){const r=e.getBoundingClientRect();top=Math.min(top,r.top+scrollY);left=Math.min(left,r.left+scrollX);bottom=Math.max(bottom,r.bottom+scrollY);right=Math.max(right,r.right+scrollX);}
const imageRects=[];
function walk(node,depth){
 if(reason)return null;
 if(depth>55||counters.nodes>=30000){reason=depth>55?'max-depth':'max-nodes';return null;}
 if(node.nodeType===Node.TEXT_NODE){const t=String(node.nodeValue||'');if(!t.trim())return null;if(counters.text_chars+t.length>500000){reason='max-text';return null;}counters.nodes++;counters.text_chars+=t.length;return {tag:'#text',text:t};}
 if(!(node instanceof Element)||!visible(node)||node.matches(forbidden)||node.closest('nav,aside,header,footer,[data-message-author-role="user"],[data-testid="user-message"]'))return null;
 const tag=node.tagName.toLowerCase();
 if(tag==='button'){
   const imgs=[...node.querySelectorAll('img')].filter(img=>visible(img)&&img.getBoundingClientRect().width>=40&&img.getBoundingClientRect().height>=40);
   if(!imgs.length)return null;
   return {tag:'figure',children:imgs.map(img=>walk(img,depth+1)).filter(Boolean)};
 }
 const box=node.getBoundingClientRect(),css=getComputedStyle(node);
 const rect={x:Math.round(box.left+scrollX),y:Math.round(box.top+scrollY),w:Math.round(box.width),h:Math.round(box.height)};
 const style={};
 for(const prop of ['display','gridTemplateColumns','flexDirection','flexWrap','justifyContent','alignItems','gap','padding','marginTop','marginBottom','width','maxWidth','fontSize','fontWeight','lineHeight','color','backgroundColor','borderColor','borderRadius','textAlign','whiteSpace','objectFit']){
    const val=String(css[prop]||'').trim();if(val.length>0&&val.length<=100&&!/url\\s*\\(|var\\s*\\(|[;{}<>]/i.test(val))style[prop]=val;
 }
 const out={tag:allowed.has(tag)?tag:'div',rect,style};counters.nodes++;
 if(tag==='table')counters.tables++;
 if(tag==='img'){
   counters.images++;
   const source=String(node.currentSrc||'');
   out.image={index:counters.images-1,natural_width:node.naturalWidth||0,natural_height:node.naturalHeight||0,loaded:node.complete===true,source_type:/^blob:/i.test(source)?'blob':/^data:/i.test(source)?'data':/^https:/i.test(source)?'https':'other'};
   if(imageRects.length<40)imageRects.push({...rect,index:counters.images-1});
   return out;
 }
 if(/^h[1-6]$/.test(tag))counters.headings++;
 if(css.display==='grid')counters.grids++;
 if(css.display==='flex')counters.flex++;
 counters.max_children=Math.max(counters.max_children,node.childNodes.length);
 const children=[];
 for(const child of node.childNodes){const part=walk(child,depth+1);if(part)children.push(part);if(reason)break;}
 if(children.length)out.children=children;
 return out;
}
const children=[];for(const e of roots){const n=walk(e,0);if(n)children.push(n);if(reason)break;}
const active=String(globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active?.requestId||'');
return {ok:!reason,reason,version:1,scope,request_marker:active===expected?expected:null,
 counts:counters,rect:{x:Math.max(0,Math.floor(left)),y:Math.max(0,Math.floor(top)),w:Math.ceil(right-left),h:Math.ceil(bottom-top)},
 image_rects:imageRects,root:{tag:'assistant-reply',children},
 generating:!!document.querySelector('button[aria-label*="Stop generating"],button[aria-label*="Stop response"]')};
})"""

def json_get(port, path):
    with urlopen(f"http://127.0.0.1:{port}{path}",timeout=3) as f:
        return json.load(f)

def safe_url(raw):
    if not raw: return ""
    p=urlsplit(raw)
    if p.scheme!="https" or p.hostname not in HOSTS or p.port not in (None,443) or not CONVERSATION.fullmatch(p.path) or p.username or p.password or p.query or p.fragment:
        raise ValueError("conversation_url_invalid")
    return f"https://{p.hostname}{p.path}"

def ws_endpoint(raw,port):
    u=urlsplit(raw)
    if u.scheme!="ws" or u.hostname not in ("127.0.0.1","localhost","::1") or u.port!=port:
        raise ValueError("non_loopback_cdp_endpoint")
    return raw

class CDP:
    def __init__(self,websocket): self.ws=websocket;self.seq=0
    async def call(self,method,params=None,timeout=35):
        self.seq+=1;seq=self.seq
        await self.ws.send(json.dumps({"id":seq,"method":method,"params":params or {}}))
        while True:
            obj=json.loads(await asyncio.wait_for(self.ws.recv(),timeout=timeout))
            if obj.get("id")!=seq:continue
            if obj.get("error"):raise RuntimeError("cdp_"+method.replace(".","_")+"_failed")
            return obj.get("result") or {}

def status(folder,stage,percent,error=None,**meta):
    data={"tool_version":VERSION,"status":"failed" if error else "running","stage":stage,
      "percent":percent,"updated_at":int(time.time()),"error":error,**meta}
    tmp=folder/"status.tmp";tmp.write_text(json.dumps(data));tmp.replace(folder/"status.json")

async def capture(args):
    import websockets
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    status(folder,"discovering",8)
    target_url=safe_url(args.conversation_url)
    targets=json_get(args.cdp_port,"/json/list")
    def target(t):
        u=urlsplit(str(t.get("url") or ""))
        return t.get("type")=="page" and u.hostname in HOSTS and u.scheme=="https" and t.get("webSocketDebuggerUrl")
    targets=[t for t in targets if target(t)]
    picked=None;report=None;ws=None
    for t in targets[:60]:
        if target_url and str(t.get("url") or "").split("?")[0]!=target_url:continue
        endpoint=ws_endpoint(t["webSocketDebuggerUrl"],args.cdp_port)
        async with websockets.connect(endpoint,open_timeout=5,max_size=24*1024*1024) as conn:
            cdp=CDP(conn)
            await cdp.call("Runtime.enable")
            result=await cdp.call("Runtime.evaluate",{"expression":CAPTURE+"("+json.dumps(args.request_id)+")","returnByValue":True,"silent":True})
            value=(result.get("result") or {}).get("value")
            if not isinstance(value,dict) or not value.get("ok"):continue
            if not target_url and value.get("request_marker")!=args.request_id:continue
            picked=t;report=value;break
    if not picked and target_url:
        status(folder,"opening-history",18)
        # The caller supplied a strict chatgpt.com/c/UUID URL. Creates a
        # separate tab only; never navigates an active Worker request tab.
        from urllib.request import urlopen as _open
        _open(Request(f"http://127.0.0.1:{args.cdp_port}/json/new?"+quote(target_url,safe=""),method="PUT"),timeout=5).close()
        for attempt in range(8):
            await asyncio.sleep(1)
            targets=[t for t in json_get(args.cdp_port,"/json/list") if target(t)]
            picked=next((t for t in targets if str(t.get("url") or "").split("?")[0]==target_url),None)
            if picked:break
        if picked:
            endpoint=ws_endpoint(picked["webSocketDebuggerUrl"],args.cdp_port)
            async with websockets.connect(endpoint,open_timeout=5,max_size=24*1024*1024) as conn:
                cdp=CDP(conn);await cdp.call("Runtime.enable")
                for attempt in range(10):
                    result=await cdp.call("Runtime.evaluate",{"expression":CAPTURE+"("+json.dumps(args.request_id)+")","returnByValue":True,"silent":True})
                    report=(result.get("result") or {}).get("value")
                    if isinstance(report,dict) and report.get("ok"):break
                    await asyncio.sleep(1)
    if not picked or not report or not report.get("ok"):
        raise RuntimeError("matching_page_unavailable; supply exact ChatGPT history URL when prior tab is closed")
    if report.get("generating"):raise RuntimeError("reply_still_generating")
    if not report.get("root",{}).get("children"):raise RuntimeError("no_assistant_reply_evidence")
    status(folder,"capturing-screenshots",45,counts=report["counts"])
    path=folder/"native-page-evidence.zip"
    async with websockets.connect(ws_endpoint(picked["webSocketDebuggerUrl"],args.cdp_port),open_timeout=5,max_size=24*1024*1024) as conn:
        cdp=CDP(conn)
        await cdp.call("Page.enable")
        with ZipFile(path,"w",ZIP_DEFLATED,compresslevel=6) as archive:
            archive.writestr("assistant_dom.json",json.dumps(report,ensure_ascii=False))
            rect=report["rect"];screens=0
            # Bounded screenshot strips of assistant reply, no sidebar or user prompt.
            if rect["w"]>0 and rect["h"]>0:
                for y in range(rect["y"],rect["y"]+min(rect["h"],1600*24),1600):
                    if screens>=24:break
                    clip={"x":rect["x"],"y":y,"width":min(3000,rect["w"]),
                          "height":min(1600,rect["y"]+rect["h"]-y),"scale":1}
                    result=await cdp.call("Page.captureScreenshot",{"format":"png","fromSurface":True,"captureBeyondViewport":True,"clip":clip})
                    archive.writestr(f"screenshots/assistant_{screens:02d}.png",base64.b64decode(result["data"]))
                    screens+=1
                    status(folder,"capturing-screenshots",45+int(40*screens/min(24,max(1,(rect["h"]+1599)//1600))),screenshots=screens)
                    if path.stat().st_size>MAX_ZIP:raise RuntimeError("evidence_zip_size_limit")
            archive.writestr("manifest.json",json.dumps({"tool":"native-page-diagnostic",
                "version":VERSION,"request_id":args.request_id,"worker_id":args.worker_id,
                "scope":report["scope"],"counts":report["counts"],
                "screenshot_count":screens,"truncated":bool(report.get("reason")),
                "source":"existing-chatgpt-page","conversation_url_included":False},ensure_ascii=False))
    if path.stat().st_size>MAX_ZIP:raise RuntimeError("evidence_zip_size_limit")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    status(folder,"complete",100,size=path.stat().st_size,sha256=digest,
           screenshots=screens,counts=report["counts"],tool_version=VERSION)
    data=json.loads((folder/"status.json").read_text());data["status"]="completed"
    (folder/"status.json").write_text(json.dumps(data))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--version",action="store_true")
    parser.add_argument("--output");parser.add_argument("--request-id")
    parser.add_argument("--worker-id");parser.add_argument("--cdp-port",type=int,default=9222)
    parser.add_argument("--conversation-url",default="")
    args=parser.parse_args()
    if args.version:print(VERSION);return
    if not REQUEST.fullmatch(args.request_id or "") or not re.fullmatch(r"wrk_[A-Za-z0-9_-]{6,100}",args.worker_id or "") or args.cdp_port not in range(9222,9254):
        raise SystemExit("invalid_request_or_worker")
    folder=Path(args.output)
    try:asyncio.run(capture(args))
    except Exception as exc:
        status(folder,"failed",100,error=str(exc)[:180])
        raise SystemExit(1)

if __name__=="__main__":main()
