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
import sqlite3
import sys
import time
from urllib.parse import urlsplit, quote
from urllib.request import urlopen, Request
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = "1.2.1"
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
const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
const hasUser=e=>!!e.querySelector('[data-message-author-role="user"],[data-testid="user-message"]');
let scope='',roots=[];
const tagged=document.querySelector('[data-chat2api-native-request-id="'+expected+'"]');
const assistant=[...document.querySelectorAll('[data-message-author-role="assistant"]')].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&!e.parentElement?.closest('[data-message-author-role="assistant"]'));
if(assistant.length){
  scope='assistant-role';
  const root=tagged?.closest('[data-message-author-role="assistant"]');
  roots=[root&&visible(root)&&!hasUser(root)?root:assistant[assistant.length-1]];
}
else {
 // For historical ChatGPT replies, a roleless "ChatGPT said" heading may
 // live INSIDE a verified turn container, not beside the answer body.
 const h=[...document.querySelectorAll('main h4,main h5,main h6,[role="main"] h4,[role="main"] h5,[role="main"] h6,#thread h4,#thread h5')].filter(e=>e.isConnected&&/^ChatGPT said\s*[:：]?$/.test(norm(e.textContent)));
 if(!h.length)return {ok:false,error:'no-verified-assistant-boundary'};
 const heading=h[h.length-1];
 const turn=heading.closest('[data-testid="conversation-turn"],article,[data-testid="assistant-message"]');
 if(turn&&visible(turn)&&!hasUser(turn)&&
    ![...turn.querySelectorAll('h4,h5,h6')].some(e=>/^You said\s*[:：]?$/.test(norm(e.textContent)))){
   roots=[turn];scope='verified-roleless-turn';
 }else{
   scope='roleless-heading-siblings';
   let at=heading.nextElementSibling;
   if(!at && heading.parentElement && !hasUser(heading.parentElement))
     at=heading.parentElement.nextElementSibling;
   while(at&&roots.length<4096){
     if(at.matches(forbidden)||/^(You said|ChatGPT said)\s*[:：]?$/.test(norm(at.textContent))&&/^H[456]$/.test(at.tagName)||hasUser(at))break;
     roots.push(at);at=at.nextElementSibling;
   }
   if(at&&roots.length>=4096)return {ok:false,error:'reply-sibling-limit'};
 }
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
    const val=String(css[prop]||'').trim();if(val.length>0&&val.length<=100&&!/url\s*\(|var\s*\(|[;{}<>]/i.test(val))style[prop]=val;
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
// A content script runs in an isolated Chrome world, invisible to the
// default DevTools Runtime.evaluate context. Trust only the DOM marker placed
// by request-v6 for the exact request. Never infer it from page similarity.
return {ok:!reason,reason,version:1,scope,request_marker:tagged?expected:null,
 counts:counters,rect:{x:Math.max(0,Math.floor(left)),y:Math.max(0,Math.floor(top)),w:Math.ceil(right-left),h:Math.ceil(bottom-top)},
 image_rects:imageRects,root:{tag:'assistant-reply',children},
 generating:!!document.querySelector('button[aria-label*="Stop generating"],button[aria-label*="Stop response"]')};
})"""

PAGE_PROBE = r"""(function(){
'use strict';
// Fixed structural signals only. No body text, page title, URLs, prompts,
// account identifiers, cookies, storage or screenshots of auth pages.
const heading=[...document.querySelectorAll('main h4,main h5,main h6,[role="main"] h4,[role="main"] h5,[role="main"] h6,#thread h4,#thread h5')]
 .filter(e=>/^ChatGPT said\\s*[:：]?$/.test(String(e.textContent||'').replace(/\\s+/g,' ').trim())).length;
const path=String(location.pathname||'');
const route=/^\\/c\\/[A-Za-z0-9-]{12,90}\\/?$/.test(path)?'conversation':
 /login|auth|signin|challenge|captcha|error/i.test(path)?'access-gate':'other';
return {ready_state:String(document.readyState||'unknown').slice(0,16),
  route, assistant_roles:document.querySelectorAll('[data-message-author-role="assistant"]').length,
  user_roles:document.querySelectorAll('[data-message-author-role="user"]').length,
  conversation_turns:document.querySelectorAll('[data-testid="conversation-turn"]').length,
  verified_roleless_headings:heading,
  main_landmarks:document.querySelectorAll('main,[role="main"]').length,
  password_fields:document.querySelectorAll('input[type="password"]').length>0,
  challenge_frames:document.querySelectorAll('iframe[src*="challenge"],iframe[src*="captcha"]').length>0,
  loading_skeletons:document.querySelectorAll('[data-testid*="skeleton"],[aria-busy="true"]').length>0};
})()"""

def json_get(port, path):
    with urlopen(f"http://127.0.0.1:{port}{path}",timeout=3) as f:
        return json.load(f)

def safe_url(raw):
    if not raw: return ""
    p=urlsplit(raw)
    if p.scheme!="https" or p.hostname not in HOSTS or p.port not in (None,443) or not CONVERSATION.fullmatch(p.path) or p.username or p.password or p.query or p.fragment:
        raise ValueError("conversation_url_invalid")
    return f"https://{p.hostname}{p.path}"

def recovery_hints():
    """Read only time + SHA digests from the authenticated Worker command."""
    try: obj=json.loads(os.environ.get("CHAT2API_NATIVE_RECOVERY_HINTS","{}"))
    except (TypeError,ValueError):obj={}
    hashes=obj.get("hashes") or []
    if not isinstance(hashes,list): hashes=[]
    hashes=[x for x in hashes[:8] if isinstance(x,str)
            and re.fullmatch(r"[a-f0-9]{64}",x)]
    start=obj.get("start") if type(obj.get("start")) is int else 0
    end=obj.get("end") if type(obj.get("end")) is int else 0
    if not start or end<=start or end-start>7200:
        start=end=0
    return {"hashes":hashes,"start":start,"end":end}


def chrome_history_candidates(port,hints,history_file=None):
    """Read only ChatGPT conversation visits in this request's time window.

    SQL never selects unrelated URLs. It does not read cookies, message text
    or any visit outside the bounded time interval. A candidate MUST pass
    independent generated-reply content matching before use.
    """
    if not hints["start"] or len(hints["hashes"])<2:return []
    slot=port-9221
    path=Path(history_file) if history_file is not None else Path(
        f"/home/chat2api/.config/chat2api-chrome-worker-{slot:02d}/Default/History")
    if not path.is_file():return []
    origin=11644473600
    lower=int((hints["start"]+origin)*1000000)
    upper=int((hints["end"]+origin)*1000000)
    candidates=[]
    try:
        with sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=2) as db:
            rows=db.execute("""
                SELECT urls.url,visits.visit_time
                FROM visits JOIN urls ON visits.url=urls.id
                WHERE visits.visit_time BETWEEN ? AND ?
                  AND (urls.url LIKE 'https://chatgpt.com/c/%'
                    OR urls.url LIKE 'https://www.chatgpt.com/c/%'
                    OR urls.url LIKE 'https://chat.openai.com/c/%')
                ORDER BY visits.visit_time DESC LIMIT 160
            """,(lower,upper)).fetchall()
        for raw,_ in rows:
            try:url=safe_url(str(raw).split("?",1)[0].split("#",1)[0])
            except (ValueError,TypeError):continue
            if url not in candidates:candidates.append(url)
            if len(candidates)>6:break
    except (sqlite3.Error,OSError):return []
    return candidates


def reference_matches(report,hashes):
    """Compare assistant-only normalized text by digest; export no snippets."""
    if not isinstance(report,dict) or not report.get("ok") or len(hashes)<2:return 0
    pieces=[]
    def visit(value):
        if isinstance(value,dict):
            if value.get("tag")=="#text":
                pieces.append(str(value.get("text") or ""))
            else:
                for item in value.get("children") or []:visit(item)
    visit(report.get("root") or {})
    text="".join(ch.lower() for ch in "".join(pieces) if ch.isalnum())[:600000]
    if len(text)<48:return 0
    requested=set(hashes)
    found=set()
    for i in range(len(text)-47):
        if hashlib.sha256(text[i:i+48].encode()).hexdigest() in requested:
            found.add(hashlib.sha256(text[i:i+48].encode()).hexdigest())
            if len(found)>=len(requested):break
    return len(found)


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
    # A bounded, versioned operational timeline travels with the diagnostic ZIP.
    # Never append page URLs, Chrome credentials, request text or image bytes.
    try:
        with (folder/"diagnostic.log").open("a",encoding="utf8") as stream:
            stream.write(json.dumps({key:data.get(key) for key in (
                "tool_version","status","stage","percent","updated_at","error",
                "screenshots")},ensure_ascii=False)+"\n")
    except OSError: pass

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
    binding="live-request-marker"
    hints=recovery_hints()
    async def open_history_page(url):
        """Open a new diagnostic-only tab, never touch the active request tab."""
        response=json_get(args.cdp_port,"/json/list")
        existing=next((t for t in response if target(t) and
            str(t.get("url") or "").split("?",1)[0]==url),None)
        made=False
        if existing is None:
            with urlopen(Request(f"http://127.0.0.1:{args.cdp_port}/json/new?"+
                    quote(url,safe=""),method="PUT"),timeout=8) as response:
                existing=json.load(response)
            made=True
        tab_id=str(existing.get("id") or "") if made else ""
        page=existing
        try:
            # The ChatGPT history app may initialize slowly; do not declare
            # missing reply based on one early empty DOM.
            for i in range(24):
                await asyncio.sleep(1 if i else 0.3)
                targets=[t for t in json_get(args.cdp_port,"/json/list") if target(t)]
                if made:
                    page=next((t for t in targets if t.get("id")==tab_id),None)
                else:
                    page=next((t for t in targets if
                         str(t.get("url") or "").split("?",1)[0]==url),None)
                if not page:continue
                endpoint=ws_endpoint(page["webSocketDebuggerUrl"],args.cdp_port)
                async with websockets.connect(endpoint,open_timeout=5,max_size=24*1024*1024) as conn:
                    cdp=CDP(conn);await cdp.call("Runtime.enable")
                    result=await cdp.call("Runtime.evaluate",{
                        "expression":CAPTURE+"("+json.dumps(args.request_id)+")",
                        "returnByValue":True,"silent":True})
                    obj=(result.get("result") or {}).get("value")
                    if isinstance(obj,dict) and obj.get("ok") and obj.get("root",{}).get("children"):
                        return page,obj,tab_id
        except Exception:
            if not made:raise
        return None,None,tab_id

    if not picked and target_url:
        status(folder,"opening-history",18)
        picked,report,_=await open_history_page(target_url)
        binding="saved-or-manual-conversation-url-unverified-turn"
    if not picked and not target_url:
        candidates=chrome_history_candidates(args.cdp_port,hints)
        status(folder,"searching-chrome-history",16,
               history_candidate_count=len(candidates),open_page_count=len(targets))
        if len(candidates)>6:
            raise RuntimeError("history_multiple_candidates; supply exact ChatGPT conversation URL")
        if candidates and len(hints["hashes"])>=2:
            for idx,candidate in enumerate(candidates):
                status(folder,"verifying-history-content",
                       20+int(20*(idx+1)/len(candidates)),
                       history_candidate_count=len(candidates))
                page,probe,owned_tab=await open_history_page(candidate)
                if page and reference_matches(probe,hints["hashes"])>=2:
                    picked,report=page,probe
                    binding="chrome-history-content-sha256-verified"
                    break
                # Close only a tab created by this diagnosis, not any tab
                # belonging to active requests or remote administration.
                if owned_tab:
                    try:
                        with urlopen(f"http://127.0.0.1:{args.cdp_port}/json/close/"+
                             quote(owned_tab,safe=""),timeout=3):pass
                    except OSError:pass
        if not picked:
            raise RuntimeError("historical_request_page_not_recovered; no unique content-verified history match; supply ChatGPT /c/ URL")
    if not picked or not report or not report.get("ok"):
        raise RuntimeError("history_reply_unavailable; verify ChatGPT sign-in and conversation URL")
    if not target_url and binding=="live-request-marker" and report.get("request_marker")!=args.request_id:
        raise RuntimeError("request_marker_unverified")
    if report.get("generating"):raise RuntimeError("reply_still_generating")
    if not report.get("root",{}).get("children"):raise RuntimeError("no_assistant_reply_evidence")
    verification=("url-only-unbound" if args.request_id.startswith("req_diag_")
        else "live-request-marker" if report.get("request_marker")==args.request_id
        else binding)
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
                "source":"existing-chatgpt-page","conversation_url_included":False,
                "request_binding":verification},ensure_ascii=False))
            if (folder/"diagnostic.log").is_file():
                archive.write(folder/"diagnostic.log","diagnostic.log")
    if path.stat().st_size>MAX_ZIP:raise RuntimeError("evidence_zip_size_limit")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    status(folder,"complete",100,size=path.stat().st_size,sha256=digest,
           screenshots=screens,counts=report["counts"],tool_version=VERSION)
    data=json.loads((folder/"status.json").read_text());data["status"]="completed"
    (folder/"status.json").write_text(json.dumps(data))
    with (folder/"diagnostic.log").open("a",encoding="utf8") as stream:
        stream.write(json.dumps({"tool_version":VERSION,"status":"completed",
            "stage":"complete","percent":100,"updated_at":int(time.time())})+"\n")

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
