"""Capture the ACTUAL authenticated admin native-preview DOM as request-bound PNG strips.

Requires an already-open Chrome tab showing the request in native preview
mode. Never navigates, authenticates, executes page-provided code, opens another
request or collects screenshots of other page regions.
Usage:
 python scripts/native_visual_capture_candidate_v299.py --request-id req_... --output native.zip --cdp-port 9222
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image
try:
    from scripts.native_page_diagnostic_v1 import CDP, json_get, ws_endpoint
except ModuleNotFoundError:
    # Direct invocation: python scripts/native_visual_capture_candidate_v299.py
    from native_page_diagnostic_v1 import CDP, json_get, ws_endpoint

ALLOWED_HOSTS = frozenset({"chat2api.mv3.cn"})
REQUEST = re.compile(r"^req_(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$")
PROBE = r"""(function(requestId,surface){
  "use strict";
  const node=surface==="web-chat"
    ? [...document.querySelectorAll("#messages .message.assistant .message-body[data-chat2api-native-render-request-id]")].find(el=>el.getAttribute("data-chat2api-native-render-request-id")===requestId)
    : document.getElementById("requestConversationNative");
  if(!node || node.getAttribute("data-chat2api-native-render-request-id")!==requestId)
    return {ok:false,reason:"not-verified-current-request"};
  if(node.getAttribute("data-chat2api-native-render-finality")!=="verified-final")
    return {ok:false,reason:"native-final-unverified"};
  if(surface!=="web-chat" && node.getAttribute("data-chat2api-native-media-sha-verified")!=="true")
    return {ok:false,reason:"media-byte-identity-unverified"};
  const content=surface==="web-chat" ? node.querySelector(".native-rich-root") : node;
  if(!content) return {ok:false,reason:"native-content-not-rendered"};
  // The Web Chat path cannot assert image byte identity merely from a URL.
  // Require separate verified media evidence before enabling visual claims.
  if(surface==="web-chat" && node.querySelector("img,video,audio,.native-generated-file"))
    return {ok:false,reason:"web-chat-media-proof-unavailable"};
  const r=content.getBoundingClientRect(), style=getComputedStyle(content);
  if(style.display==="none"||style.visibility==="hidden"||
     r.width<200||r.height<80||r.width>3000||r.height>38400)
    return {ok:false,reason:"native-preview-not-visible-or-too-large"};
  // A scroll-clipped container would silently omit offscreen table/code rows.
  // Never call such a screenshot "full native reply".
  if([content,...content.querySelectorAll("*")].some(el=>
      el.clientWidth>0 && el.clientHeight>0 &&
      ((["auto","scroll","hidden","clip"].includes(getComputedStyle(el).overflowX) && el.scrollWidth>el.clientWidth+2) ||
       (["auto","scroll","hidden","clip"].includes(getComputedStyle(el).overflowY) && el.scrollHeight>el.clientHeight+2))))
    return {ok:false,reason:"native-preview-scroll-clipped"};
  // captureBeyondViewport cannot expose an inner scroll pane. Fail
  // closed until scroll-and-stitch is implemented.
  for(let ancestor=content.parentElement;ancestor && ancestor!==document.body && ancestor!==document.documentElement;ancestor=ancestor.parentElement){
    const a=ancestor.getBoundingClientRect(), s=getComputedStyle(ancestor);
    if((["auto","scroll","hidden","clip"].includes(s.overflowX) &&
        (r.left<a.left-2 || r.right>a.right+2)) ||
       (["auto","scroll","hidden","clip"].includes(s.overflowY) &&
        (r.top<a.top-2 || r.bottom>a.bottom+2)))
      return {ok:false,reason:"native-preview-ancestor-clipped"};
  }
  if(surface==="web-chat"){
    if(r.left<0 || r.top<0 || r.right>innerWidth || r.bottom>innerHeight)
      return {ok:false,reason:"web-chat-reply-not-fully-in-viewport"};
    for(const fx of [0.02,0.5,0.98]) for(const fy of [0.02,0.5,0.98]){
      const top=document.elementFromPoint(r.left+r.width*fx,r.top+r.height*fy);
      if(!top || !content.contains(top))
        return {ok:false,reason:"web-chat-reply-obscured"};
    }
  }
  const x=window.scrollX + r.left, y=window.scrollY + r.top;
  const images=[...node.querySelectorAll("img")];
  if(images.some(img=>!img.complete || img.naturalWidth<=0))
    return {ok:false,reason:"native-images-not-loaded"};
  return {ok:true,rect:{x,y,w:r.width,h:r.height},
    images:images.length,tables:node.querySelectorAll("table").length,
    code_blocks:content.querySelectorAll("pre code").length,
    revision:node.getAttribute("data-chat2api-native-render-revision")||""};
})"""


async def capture(request_id: str, path: Path, port: int, *, surface: str = "admin",
                  origin: str | None = None) -> dict:
    import websockets
    if not REQUEST.fullmatch(request_id):
        raise ValueError("invalid-request-id")
    if not (1024 <= port <= 65535):
        raise ValueError("unsafe-cdp-port")
    if surface not in {"admin", "web-chat"}:
        raise ValueError("invalid-capture-surface")
    expected = urlsplit(origin or "")
    if surface == "web-chat" and (expected.scheme != "https" or not expected.hostname
            or expected.username or expected.password or expected.path not in ("", "/")
            or expected.query or expected.fragment):
        raise ValueError("explicit-https-web-chat-origin-required")
    targets = json_get(port, "/json/list")
    found = None
    report = None
    for item in targets[:40]:
        if item.get("type") != "page":
            continue
        url=urlsplit(str(item.get("url") or ""))
        matched = (url.scheme == expected.scheme and url.netloc == expected.netloc
                   and url.path.rstrip("/") == "/chat") if surface == "web-chat" else url.hostname in ALLOWED_HOSTS
        if (url.scheme!="https" or not matched
                or url.username or url.password or not item.get("webSocketDebuggerUrl")):
            continue
        socket=ws_endpoint(item["webSocketDebuggerUrl"],port)
        async with websockets.connect(socket,open_timeout=5,max_size=24*1024*1024) as connection:
            cdp=CDP(connection)
            await cdp.call("Runtime.enable")
            raw=await cdp.call("Runtime.evaluate",{
                "expression":PROBE+"("+json.dumps(request_id)+","+json.dumps(surface)+")",
                "returnByValue":True,"silent":True,
            })
            value=(raw.get("result") or {}).get("value")
            if not isinstance(value,dict) or not value.get("ok"):
                continue
            found=item
            report=value
            break
    if not found or not report:
        raise ValueError("verified-native-preview-not-found")
    rect=report["rect"]
    clips=math.ceil(rect["h"]/1600)
    if not 1 <= clips <= 24:
        raise ValueError("native-preview-oversize")
    screenshot_count=0
    async with websockets.connect(
        ws_endpoint(found["webSocketDebuggerUrl"],port),
        open_timeout=5,max_size=24*1024*1024
    ) as connection:
        cdp=CDP(connection)
        await cdp.call("Page.enable")
        # Recheck the live request after reconnecting and after every strip.
        async def check_binding():
            raw = await cdp.call("Runtime.evaluate",{
                "expression":PROBE+"("+json.dumps(request_id)+","+json.dumps(surface)+")",
                "returnByValue":True,"silent":True,
            })
            value = (raw.get("result") or {}).get("value")
            if not isinstance(value,dict) or not value.get("ok") or value != report:
                raise ValueError("native-capture-changed-during-screenshot")
        await check_binding()
        with ZipFile(path,"w",ZIP_DEFLATED,compresslevel=6) as archive:
            for index in range(clips):
                y=rect["y"]+index*1600
                clip={"x":rect["x"],"y":y,"width":rect["w"],
                      "height":min(1600,rect["y"]+rect["h"]-y),"scale":1}
                payload=await cdp.call("Page.captureScreenshot",{
                    "format":"png","fromSurface":True,
                    "captureBeyondViewport":True,"clip":clip,
                })
                data=base64.b64decode(payload.get("data") or "")
                with Image.open(io.BytesIO(data)) as screenshot:
                    screenshot.verify()
                await check_binding()
                archive.writestr(f"screenshots/native_{index:02d}.png",data)
                screenshot_count+=1
            archive.writestr("manifest.json",json.dumps({
                "request_id":request_id,"source":"native-public-renderer",
                "request_binding":"owner-message-id-verified",
                "capture_surface":surface,
                "native_revision":report["revision"],
                "native_finality":"verified-final",
                "media_sha256_status":"verified-sha256",
                "media_assets":report["images"],
                "capture_rect":{"width":rect["w"],"height":rect["h"]},
                "native_tables":report["tables"],
                "native_code_blocks":report["code_blocks"],
                "screenshot_count":screenshot_count,
                "full_reply_screenshot":screenshot_count==clips,
                "truncated":False,
            },ensure_ascii=False))
    if path.stat().st_size > 40*1024*1024:
        path.unlink(missing_ok=True)
        raise ValueError("native-screenshot-zip-too-large")
    return {"request_id":request_id,"screenshots":screenshot_count,
            "size":path.stat().st_size,"status":"captured-not-compared"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-id",required=True)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--cdp-port",type=int,default=9222)
    parser.add_argument("--surface",choices=["admin","web-chat"],default="admin")
    parser.add_argument("--origin",help="Exact HTTPS Web Chat origin when --surface web-chat")
    args=parser.parse_args()
    print(json.dumps(asyncio.run(capture(args.request_id,args.output,args.cdp_port,
                                      surface=args.surface,origin=args.origin))))


if __name__=="__main__":
    main()
