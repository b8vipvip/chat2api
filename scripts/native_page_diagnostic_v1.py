#!/usr/bin/env python3
"""Versioned, read-only CDP evidence collector for existing ChatGPT replies.

An independent Worker tool: it neither submits prompts nor changes chat2api
request status. Does not export cookies, storage or signed URLs. Candidate validation reads
only selected-reply cached image bytes and narrowly armed native downloads.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import binascii
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from urllib.parse import urlsplit, quote
from urllib.request import urlopen, Request
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = "1.3.11"
MAX_ZIP = 18 * 1024 * 1024
MAX_SCREENSHOTS = 64  # 40,960 CSS px, subject to original ZIP cap
# Historical HTML readyState and the first candidate DOM are NOT hydration
# completion signals. Wait for a sustained unchanged assistant subtree.
HISTORY_LOAD_MAX_SECONDS = 240
HISTORY_LOAD_POLL_SECONDS = 2
HISTORY_LOAD_STABLE_SECONDS = 12
HISTORY_LOAD_MIN_SECONDS = 18
HISTORY_LOAD_MIN_SAMPLES = 4
MAX_NODES = 30000
REQUEST = re.compile(r"^(?:req|resp|imgreq)_[A-Za-z0-9_-]{6,100}$")
CONVERSATION = re.compile(r"^/c/[A-Za-z0-9-]{12,90}/?$")
HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"}
CAPTURE = r"""(function(expected){
'use strict';
const allowed=new Set('div section article p span strong em b i h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br a sup sub'.split(' '));
const forbidden='script,style,form,nav,aside,header,footer,input,textarea,iframe,canvas,svg,[role="menu"],[role="dialog"],[contenteditable="true"],[data-message-author-role="user"],[data-conversation-role="user"],[data-testid="user-message"]';
const visible=e=>{try{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}catch(_){return false;}};
const norm=s=>String(s||'').replace(/\s+/g,' ').trim();
const hasUser=e=>!!e.querySelector('[data-message-author-role="user"],[data-conversation-role="user"],[data-testid="user-message"]');
let scope='',roots=[],boundaryVerified=true;
const tagged=document.querySelector('[data-chat2api-native-request-id="'+expected+'"]');
const nativeSelection=sel=>{
 if(!sel||!visible(sel))return null;
 const id=sel.getAttribute('data-chatgpt-selection-message-id');
 const wrap=sel.closest('[data-chatgpt-search-message-ids]');
 if(!id||!wrap||!(wrap.getAttribute('data-chatgpt-search-message-ids')||'').split(/\s+/).includes(id))return null;
 if(!wrap.querySelector('h4[data-conversation-role="assistant"]'))return null;
 const md=sel.querySelector('[data-markdown-text-style="assistant-message"]');
 if(!md||!visible(md))return null;
 const dil=md.querySelector('[data-dil-message-id]'),src=md.querySelector('[data-dil-source-message-id]');
 if(dil&&dil.getAttribute('data-dil-message-id')!==id||src&&src.getAttribute('data-dil-source-message-id')!==id)return null;
 if(sel.querySelector('[data-conversation-role="user"],[data-message-author-role="user"],[data-testid="user-message"]'))return null;
 return sel;
};
const nativeAssistant=[...document.querySelectorAll('h4[data-conversation-role="assistant"]')].map(h=>{
 const wrap=h.closest('[data-chatgpt-search-message-ids]');
 if(!wrap)return null;
 for(const sel of wrap.querySelectorAll('[data-chatgpt-selection-message-id]')){
  const match=nativeSelection(sel);if(match)return match;
 }
 return null;
}).filter(Boolean);
const taggedNative=nativeSelection(tagged?.closest?.('[data-chatgpt-selection-message-id]'));
const assistant=[...document.querySelectorAll('[data-message-author-role="assistant"]')].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&!e.parentElement?.closest('[data-message-author-role="assistant"]'));
if(taggedNative){scope='native-assistant-message-id';roots=[taggedNative];}
else if(nativeAssistant.length){scope='native-assistant-message-id';roots=[nativeAssistant[nativeAssistant.length-1]];}
else if(assistant.length){
  scope='assistant-role';
  const root=tagged?.closest('[data-message-author-role="assistant"]');
  roots=[root&&visible(root)&&!hasUser(root)?root:assistant[assistant.length-1]];
}
else if(([...document.querySelectorAll(
  'main [data-testid="assistant-message"],main [data-testid="assistant-turn"],[role="main"] [data-testid="assistant-message"]'
)].filter(e=>e.isConnected&&visible(e)&&!hasUser(e))).length){
  scope='verified-assistant-testid';
  roots=[...document.querySelectorAll(
    'main [data-testid="assistant-message"],main [data-testid="assistant-turn"],[role="main"] [data-testid="assistant-message"]'
  )].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)).slice(-1);
}
else if(([...document.querySelectorAll(
 'main article[data-testid^="conversation-turn-"] .agent-turn,main article .agent-turn,main [data-testid^="conversation-turn-"].agent-turn'
)].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&
 !e.closest('[data-message-author-role="user"],[data-testid="user-message"]'))).length){
 // Newer ChatGPT renders the assistant as a turn with .agent-turn and
 // This is an explicit assistant marker, never a generic last article.
 scope='verified-agent-turn';
 roots=[...document.querySelectorAll(
  'main article[data-testid^="conversation-turn-"] .agent-turn,main article .agent-turn,main [data-testid^="conversation-turn-"].agent-turn'
 )].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&
 !e.closest('[data-message-author-role="user"],[data-testid="user-message"]')).slice(-1);
}
else {
 const h=[...document.querySelectorAll('main h4,main h5,main h6,[role="main"] h4,[role="main"] h5,[role="main"] h6,#thread h4,#thread h5')].filter(e=>e.isConnected&&/^ChatGPT said\s*[:：]?$/.test(norm(e.textContent)));
 if(!h.length){
  // The explicit URL-only req_diag_ path permits bounded STRUCTURAL
  // evidence, never a verified assistant reply or API delivery result.
  if(!/^req_diag_[A-Za-z0-9_-]{6,100}$/.test(expected))
    return {ok:false,error:'no-verified-assistant-boundary'};
  const main=document.querySelector('main,[role="main"]');
  if(!main)return {ok:false,error:'no-verified-assistant-boundary'};


  const sel='p,pre,blockquote,table,[role="table"],figure,img,h1,h2,h3,h4,h5,h6';
  const seeds=[...main.querySelectorAll(sel)].slice(0,1500);
  const ancestors=new Set();
  for(const seed of seeds){
   if(!visible(seed))continue;
   let depth=0;
   for(let e=seed.parentElement;e&&e!==main&&depth++<55;e=e.parentElement)
    if(/^(DIV|ARTICLE|SECTION)$/.test(e.tagName))ancestors.add(e);
  }
  const danger='nav,aside,header,footer,[role="dialog"],[data-message-author-role="user"],[data-testid="user-message"]';
  const editor='form,textarea,input,[contenteditable="true"],[role="textbox"]';
  let best=null;
  for(const el of ancestors){
   if(!el.isConnected||!visible(el)||el.closest(danger)||el.querySelector(danger))continue;
   const rect=el.getBoundingClientRect();
   if(rect.width<100||rect.height<60||rect.height>60000)continue;
   const editors=el.querySelectorAll(editor).length;
   if(editors>2)continue;
   const blocks=el.querySelectorAll(sel);
   if(blocks.length<2||blocks.length>15000)continue;
   const p=el.querySelectorAll('p,pre,blockquote').length;
   const h=el.querySelectorAll('h1,h2,h3,h4,h5,h6').length;
   const tables=el.querySelectorAll('table,[role="table"]').length;
   const images=el.querySelectorAll('figure,img').length;
   const chars=String(el.innerText||'').trim().length;
   if(chars>250000||chars<200&&images===0)continue;
   if(p<2&&h<1&&tables<1&&images<1)continue;
   if([...el.querySelectorAll('h4,h5,h6')].some(n=>/^(You said|你说|你发送)\s*[:：]?$/i.test(norm(n.textContent))))continue;
   const score=chars+blocks.length*8+h*40+tables*80+Math.min(images,20)*35-editors*300;
   if(!best||score>best.score||score===best.score&&rect.height<best.height)
    best={el,score,height:rect.height};
  }
  if(!best)return {ok:false,error:'no-verified-assistant-boundary'};
  roots=[best.el];scope='inferred-rich-content-candidate';boundaryVerified=false;
 }else{
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
 if(!(node instanceof Element)||!visible(node)||node.matches(forbidden)||node.closest('nav,aside,header,footer,[data-message-author-role="user"],[data-conversation-role="user"],[data-testid="user-message"]'))return null;
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

// by request-v6 for the exact request. Never infer it from page similarity.
return {ok:!reason,reason,version:1,scope,boundary_verified:boundaryVerified,
 boundary_evidence:scope==='inferred-rich-content-candidate'?{
  table_count:roots[0].querySelectorAll('table,[role="table"]').length,
  heading_count:roots[0].querySelectorAll('h1,h2,h3,h4,h5,h6').length,
  paragraph_count:roots[0].querySelectorAll('p').length,
  excluded_composer:true,excluded_user_markers:true,manual_url_only:true
 }:scope==='native-assistant-message-id'?{
  assistant_role_marker:true,selection_message_id:true,wrapper_message_id_match:true,
  dil_message_id_match:!!roots[0].querySelector('[data-dil-message-id]'),
  source_message_id_match:!!roots[0].querySelector('[data-dil-source-message-id]')
 }:null,
 request_marker:tagged?expected:null,
 counts:counters,rect:{x:Math.max(0,Math.floor(left)),y:Math.max(0,Math.floor(top)),w:Math.ceil(right-left),h:Math.ceil(bottom-top)},
 image_rects:imageRects,root:{tag:'assistant-reply',children},
 generating:!!document.querySelector('button[aria-label*="Stop generating"],button[aria-label*="Stop response"]')};
})"""


# This helper runs only in a tab CREATED by the diagnostic. The normal
# complete native DOM can be read from existing tabs without scrolling them.

# then returns a viewport-only clip verified by elementFromPoint. Never send
# text, URLs, outerHTML, cookies or scripts from the page to the worker log.
SCREENSHOT_PREP = r"""(async function(expected,offset,limit){
 'use strict';
 const fail=(reason)=>({ok:false,reason});
 if(!/^req_diag_[A-Za-z0-9_-]{6,100}$/.test(expected))
   return fail('not-url-only');
 if(!Number.isInteger(offset)||offset<0||offset>40000||
    !Number.isInteger(limit)||limit<1||limit>800)return fail('bad-offset');
 const main=document.querySelector('main,[role="main"]');
 if(!main)return fail('missing-main');
 const visible=e=>{try{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
  return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}
  catch(_){return false;}};
 const tables=[...document.querySelectorAll('main table,main [role="table"],[role="main"] table')]
   .filter(e=>e.isConnected&&visible(e)).slice(0,200);
 if(tables.length<3)return fail('tables-missing');
 let candidate=tables[0].parentElement;
 while(candidate&&tables.some(t=>!candidate.contains(t)))candidate=candidate.parentElement;
 let ancestor=candidate,depth=0;
 while(ancestor&&ancestor!==main&&depth<55){ancestor=ancestor.parentElement;depth++;}
 const unsafe='form,textarea,input,[contenteditable="true"],[role="textbox"],nav,aside,header,footer,[role="dialog"],[data-message-author-role="user"],[data-testid="user-message"]';
 const text=String(candidate?.innerText||'');
 const headingList=candidate?[...candidate.querySelectorAll('h1,h2,h3,h4,h5,h6')]:[];
 // A composer may be omitted from unverified DOM evidence, but its
 // browser pixels could reveal an unsent draft. Never screenshot that scope.
 if(candidate?.querySelector('form,textarea,input,[contenteditable="true"],[role="textbox"]'))
   return fail('editor-overlap-no-screenshot');
 if(!candidate||candidate===main||ancestor!==main||depth<3||depth>=55||
    candidate.matches(unsafe)||candidate.querySelector(unsafe)||
    candidate.closest('nav,aside,header,footer,[role="dialog"],[data-message-author-role="user"]')||
    headingList.some(e=>/^(?:You said|你说|你发送)\s*[:：]?$/i.test(String(e.textContent||'').trim()))||
    headingList.length<2||candidate.querySelectorAll('p').length<10||
    candidate.children.length<4||text.length<600||text.length>250000||
    candidate.getBoundingClientRect().height<500)return fail('boundary-changed');
 let scrollable=null;
 for(let parent=candidate.parentElement;parent;parent=parent.parentElement){
   const css=getComputedStyle(parent);
   if(/auto|scroll|overlay/.test(css.overflowY)&&
      parent.scrollHeight>parent.clientHeight+12){scrollable=parent;break;}
 }
 const rootScroller=document.scrollingElement;
 const scroller=scrollable||rootScroller;
 if(!scroller)return fail('no-scroller');
 const isRoot=scroller===rootScroller;
 const initial=candidate.getBoundingClientRect();
 const scrollerRect=isRoot?{top:0}:scroller.getBoundingClientRect();
 const initialScroll=isRoot?(window.scrollY||rootScroller.scrollTop||0):scroller.scrollTop;
 const desired=initialScroll+initial.top-scrollerRect.top+offset-12;
 if(isRoot)window.scrollTo(0,Math.max(0,desired));
 else scroller.scrollTop=Math.max(0,desired);
 await new Promise(resolve=>setTimeout(resolve,180));
 const r=candidate.getBoundingClientRect();
 const vw=Math.floor(window.innerWidth),vh=Math.floor(window.innerHeight);
 const x=Math.max(0,Math.ceil(r.left)),y=Math.ceil(r.top+offset);
 const width=Math.floor(Math.min(vw,r.right)-x);
 // Browser layout uses fractional CSS pixels. Flooring the last viewport
 // segment discarded a real final pixel in two independent live captures.
 // Round up only where the final pixel is confirmed inside the candidate.
 const fractionalHeight=Math.min(limit,r.bottom-y,vh-y-4);
 const height=fractionalHeight>0?
   Math.min(limit,Math.ceil(fractionalHeight)):0;
 // Clip is viewport-relative. Never fall back to a negative/document-space
 // coordinate or to a blank clip beyond the viewport.
 if(x<0||y<0||width<100||height<1||y>=vh||x>=vw||
    r.height<500||Math.abs(r.height-initial.height)>20)return fail('offscreen');
 const sampleRows=height<18?
   [y+0.5,y+Math.floor(height/2),y+height-0.5]:
   [y+0.5,y+8,y+Math.floor(height/2),y+height-8,
    y+height-0.5];
 for(const sy of sampleRows){
   const hit=document.elementFromPoint(Math.min(vw-2,x+Math.floor(width/2)),sy);
   if(!hit||!(hit===candidate||candidate.contains(hit)))
     return fail('outside-candidate');
 }
 // Even when the history DOM is stable, offscreen images may load only
 // after scrolling into view. Wait for this *clip's* visible IMG elements
 // before screenshots (never navigate/fetch the original asset ourselves).
 const frameImages=[...candidate.querySelectorAll('img')].slice(0,40)
   .filter(visible).filter(img=>{
     const box=img.getBoundingClientRect();
     return box.bottom>y&&box.top<y+height&&box.right>x&&box.left<x+width;
   });
 const imageWaitStart=performance.now();
 while(frameImages.some(img=>!img.complete)&&
       performance.now()-imageWaitStart<6000){
   await new Promise(resolve=>setTimeout(resolve,200));
 }
 if(frameImages.some(img=>!img.complete))
   return fail('visible-image-loading-timeout');
 if(frameImages.some(img=>img.complete&&img.naturalWidth<1))
   return fail('visible-image-load-failed');
 const settled=candidate.getBoundingClientRect();
 if(Math.abs(settled.height-r.height)>2||
    Math.abs(settled.top-r.top)>2||
    Math.abs(settled.left-r.left)>2)
   return fail('layout-changed-during-image-load');
 for(const sy of sampleRows){
   const hit=document.elementFromPoint(Math.min(vw-2,x+Math.floor(width/2)),sy);
   if(!hit||!(hit===candidate||candidate.contains(hit)))
     return fail('clip-changed-during-image-load');
 }
 // Snapshot only image source *hashes*, never full URLs or request contents.
 const observedImages=[];
 const imgs=[...candidate.querySelectorAll('img')].filter(visible).slice(0,40);
 for(let index=0;index<imgs.length;index++){
   const img=imgs[index],ir=img.getBoundingClientRect();
   if(ir.bottom<=y||ir.top>=y+height||ir.right<=x||ir.left>=x+width)
     continue;
   if(!img.complete||img.naturalWidth<1||img.naturalHeight<1)continue;
   let url='';
   try{url=String(img.currentSrc||img.src||'');}catch(_){}
   if(!url||url.length>4096||!crypto?.subtle)continue;
   try {
     const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(url));
     const hash=[...new Uint8Array(digest)]
       .map(v=>v.toString(16).padStart(2,'0')).join('');
     observedImages.push({index,source_url_sha256:hash,
       natural_width:img.naturalWidth,natural_height:img.naturalHeight});
   }catch(_){}
 }
 return {ok:true,clip:{x,y,width,height,scale:1},offset,height,
  observed_images:observedImages};
})"""


# These probes return only one-way SHA-256 of a displayed image source URL,
# fixed metadata and safe viewport crop coordinates. They do NOT fetch any
# original media URLs, retrieve cookies, or send source URLs to the worker.
MEDIA_SOURCE_PROBE = r"""(async function(expected){
 'use strict';
 const fail=reason=>({ok:false,reason});
 if(!/^req_diag_[A-Za-z0-9_-]{6,100}$/.test(expected))
   return fail('not-url-only');
 const main=document.querySelector('main,[role="main"]');
 if(!main||!crypto?.subtle)return fail('no-crypto-or-main');
 const visible=e=>{try{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
  return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
 }catch(_){return false;}};
 const tables=[...main.querySelectorAll('table,[role="table"]')].filter(visible);
 if(tables.length<3)return fail('tables-missing');
 let candidate=tables[0].parentElement;
 while(candidate&&tables.some(e=>!candidate.contains(e)))
   candidate=candidate.parentElement;
 let ancestor=candidate,depth=0;
 while(ancestor&&ancestor!==main&&depth<55){ancestor=ancestor.parentElement;depth++;}
 const unsafe='form,textarea,input,[contenteditable="true"],[role="textbox"],[data-message-author-role="user"],[data-testid="user-message"],nav,aside,header,footer,[role="dialog"]';
 if(!candidate||candidate===main||ancestor!==main||depth<3||depth>=55||
   candidate.matches(unsafe)||candidate.querySelector(unsafe)||
   candidate.querySelectorAll('h1,h2,h3,h4,h5,h6').length<2||
   candidate.querySelectorAll('p').length<10)return fail('candidate-unsafe');
 const imgs=[...candidate.querySelectorAll('img')].filter(visible);
 if(!imgs.length||imgs.length>40)return fail('image-count-outside-bounds');
 const images=[];
 for(let index=0;index<imgs.length;index++){
   const img=imgs[index],url=String(img.currentSrc||img.src||'');
   if(!url||url.length>4096||!img.complete||img.naturalWidth<1||img.naturalHeight<1)
     return fail('image-source-unavailable');
   try{
     const bytes=new TextEncoder().encode(url);
     const digest=await crypto.subtle.digest('SHA-256',bytes);
     const fingerprint=[...new Uint8Array(digest)]
       .map(x=>x.toString(16).padStart(2,'0')).join('');
     images.push({index,source_url_sha256:fingerprint,
       natural_width:img.naturalWidth,natural_height:img.naturalHeight});
   }catch(_){return fail('digest-unavailable');}
 }
 return {ok:true,images};
})"""

MEDIA_CROP_PREP = r"""(function(expected,index){
 'use strict';
 const fail=reason=>({ok:false,reason});
 if(!/^req_diag_[A-Za-z0-9_-]{6,100}$/.test(expected)||
   !Number.isInteger(index)||index<0||index>39)return fail('bad-identity');
 const main=document.querySelector('main,[role="main"]');
 if(!main)return fail('missing-main');
 const visible=e=>{try{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
  return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
 }catch(_){return false;}};
 const tables=[...main.querySelectorAll('table,[role="table"]')].filter(visible);
 if(tables.length<3)return fail('tables-missing');
 let candidate=tables[0].parentElement;
 while(candidate&&tables.some(e=>!candidate.contains(e)))
   candidate=candidate.parentElement;
 let at=candidate,depth=0;
 while(at&&at!==main&&depth<55){at=at.parentElement;depth++;}
 const unsafe='form,textarea,input,[contenteditable="true"],[role="textbox"],[data-message-author-role="user"],[data-testid="user-message"],nav,aside,header,footer,[role="dialog"]';
 if(!candidate||candidate===main||at!==main||depth<3||depth>=55||
   candidate.matches(unsafe)||candidate.querySelector(unsafe)||
   candidate.querySelectorAll('h1,h2,h3,h4,h5,h6').length<2||
   candidate.querySelectorAll('p').length<10)return fail('candidate-unsafe');
 const imgs=[...candidate.querySelectorAll('img')].filter(visible);
 if(index>=imgs.length||imgs.length>40)return fail('image-missing');
 const img=imgs[index];
 if(!img.complete||img.naturalWidth<1||img.naturalHeight<1)return fail('not-loaded');
 let scrollable=null;
 for(let p=img.parentElement;p;p=p.parentElement){
   const cs=getComputedStyle(p);
   if(/auto|scroll|overlay/.test(cs.overflowY)&&p.scrollHeight>p.clientHeight+12){
     scrollable=p;break;
   }
 }
 const root=document.scrollingElement,scroller=scrollable||root;
 if(!scroller)return fail('no-scroller');
 const isRoot=scroller===root;
 const before=img.getBoundingClientRect();
 const parentTop=isRoot?0:scroller.getBoundingClientRect().top;
 const current=isRoot?(window.scrollY||root.scrollTop||0):scroller.scrollTop;
 const desired=current+before.top-parentTop-72;
 if(isRoot)window.scrollTo(0,Math.max(0,desired));
 else scroller.scrollTop=Math.max(0,desired);
 const r=img.getBoundingClientRect();
 const vw=Math.floor(window.innerWidth),vh=Math.floor(window.innerHeight);
 const x=Math.ceil(r.left),y=Math.ceil(r.top);
 const width=Math.floor(r.width),height=Math.floor(r.height);
 if(x<0||y<0||width<64||height<64||
    x+width>vw||y+height>vh||
    width>1600||height>1600)return fail('image-offscreen');
 for(const py of [y+8,y+Math.floor(height/2),y+height-8]){
   const point=document.elementFromPoint(x+Math.floor(width/2),py);
   if(point!==img&&!img.contains(point))return fail('image-obscured');
 }
 return {ok:true,clip:{x,y,width,height,scale:1},
  index,natural_width:img.naturalWidth,natural_height:img.naturalHeight};
})"""


# Records counts of browser failures without reading messages, stacks, URLs,
# HTTP contents, cookies, storage, headers or user data.
NEW_DOCUMENT_ERRORS = r"""(function(){
'use strict';
if(window.__chat2apiNativeDiagCounts)return;
const counts={js_exception_count:0,unhandled_rejection_count:0,
 resource_error_count:0,js_type_error_count:0,js_reference_error_count:0,
 js_syntax_error_count:0,js_range_error_count:0,
 js_other_typed_error_count:0,js_unclassified_error_count:0,
 js_error_before_complete_count:0,js_error_after_complete_count:0,
 js_first_party_error_count:0,js_other_origin_error_count:0,
 js_origin_unknown_error_count:0};
Object.defineProperty(window,'__chat2apiNativeDiagCounts',{
 value:counts,configurable:false,enumerable:false});
window.addEventListener('error',function(event){
 const ownWindow=event.target===window;
 const key=ownWindow?'js_exception_count':'resource_error_count';
 counts[key]=Math.min(9999,counts[key]+1);
 if(!ownWindow)return;
 // Emit only predefined counters. Never export message, stack, filename,
 // URL, response contents, user prompt or browser credentials.
 let type='';
 try{type=String(event.error?.name||'');}catch(_){}
 const bucket=type==='TypeError'?'js_type_error_count':
   type==='ReferenceError'?'js_reference_error_count':
   type==='SyntaxError'?'js_syntax_error_count':
   type==='RangeError'?'js_range_error_count':
   type?'js_other_typed_error_count':'js_unclassified_error_count';
 counts[bucket]=Math.min(9999,counts[bucket]+1);
 const phase=document.readyState==='complete'?
   'js_error_after_complete_count':'js_error_before_complete_count';
 counts[phase]=Math.min(9999,counts[phase]+1);
 let originType='js_origin_unknown_error_count';
 try{
   const file=String(event.filename||'');
   if(file.startsWith(location.origin+'/'))
     originType='js_first_party_error_count';
   else if(/^https?:\/\//i.test(file))
     originType='js_other_origin_error_count';
 }catch(_){}
 counts[originType]=Math.min(9999,counts[originType]+1);
},true);
window.addEventListener('unhandledrejection',function(){
 counts.unhandled_rejection_count=Math.min(9999,counts.unhandled_rejection_count+1);
},true);
})()"""

PAGE_PROBE = r"""(function(){
'use strict';
// Structural signals and numeric HTTP response classes ONLY.
// Do not read resource names/URLs, body text, titles, cookies, headers,
// account identifiers, request payloads or page storage.
const headings=[...document.querySelectorAll('main h4,main h5,main h6,[role="main"] h4,[role="main"] h5,[role="main"] h6,#thread h4,#thread h5')];
const heading=headings.filter(e=>/^ChatGPT said\s*[:：]?$/.test(
 String(e.textContent||'').replace(/\s+/g,' ').trim())).length;
const path=String(location.pathname||'');
const route=/^\/c\/[A-Za-z0-9-]{12,90}\/?$/.test(path)?'conversation':
 /login|auth|signin|challenge|captcha|error/i.test(path)?'access-gate':'other';
const perf={fetch_total:0,fetch_http_2xx:0,fetch_http_401_403:0,
 fetch_http_404:0,fetch_http_429:0,fetch_http_recent_429:0,
 fetch_http_429_age_unknown:0,fetch_http_other_4xx:0,
 fetch_http_5xx:0,fetch_status_unavailable:0,
 conversation_api_observed:0,conversation_api_2xx:0,
 conversation_api_4xx:0,conversation_api_5xx:0,
 conversation_api_unknown_status:0,conversation_api_429:0,
 conversation_api_recent_429:0,conversation_api_last_429_age_ms:0,
 conversation_api_decoded_bytes:0,conversation_api_transfer_bytes:0,
 conversation_api_decoded_nonzero:0,conversation_api_size_unavailable:0,
 other_api_observed:0,
 service_worker_controller:0};
try {
 const items=performance.getEntriesByType('resource').slice(-300);
 for(const entry of items){
  if(entry.initiatorType!=='fetch'&&entry.initiatorType!=='xmlhttprequest')continue;
  perf.fetch_total++;
  // responseStatus may be 0 or unavailable when timing is restricted.
  const code=Number(entry.responseStatus||0);
  // Resource Timing records the full page history. A stale HTTP 429 must
  // not be mistaken for a rate limit active during this diagnostic job.
  const end=Number(entry.responseEnd||0);
  const age=(end>0&&end<=performance.now())?
      Math.max(0,Math.round(performance.now()-end)):-1;
  if(code===429){
    if(age>=0&&age<=60000)perf.fetch_http_recent_429++;
    if(age<0)perf.fetch_http_429_age_unknown++;
  }
  if(code>=200&&code<300)perf.fetch_http_2xx++;
  else if(code===401||code===403)perf.fetch_http_401_403++;
  else if(code===404)perf.fetch_http_404++;
  else if(code===429)perf.fetch_http_429++;
  else if(code>=400&&code<500)perf.fetch_http_other_4xx++;
  else if(code>=500&&code<600)perf.fetch_http_5xx++;
  else perf.fetch_status_unavailable++;
  // Exact resource names are NEVER emitted or stored. Classify paths in
  // page memory only, then retain aggregate counts and HTTP status class.
  let routeType='other';
  try {
    const u=new URL(entry.name,location.origin);
    if(u.origin===location.origin){
      if(/\/(?:backend-api|api)\/(?:conversation|conversations)(?:\/|$)/i.test(u.pathname)
        || /\/api\/chat(?:\/|$)/i.test(u.pathname))routeType='conversation';
      else if(/\/(?:backend-api|api)\//i.test(u.pathname))routeType='other-api';
    }
  }catch(_){}
  if(routeType==='conversation'){
    perf.conversation_api_observed++;
    // Timing metadata only, never response bodies, URLs or headers.
    // A zero size may mean cached or hidden by Timing-Allow-Origin.
    const decoded=Number(entry.decodedBodySize||0);
    const transferred=Number(entry.transferSize||0);
    if(decoded>0&&Number.isFinite(decoded)){
      perf.conversation_api_decoded_nonzero++;
      perf.conversation_api_decoded_bytes=Math.min(100000000,
        perf.conversation_api_decoded_bytes+decoded);
    }else perf.conversation_api_size_unavailable++;
    if(transferred>0&&Number.isFinite(transferred))
      perf.conversation_api_transfer_bytes=Math.min(100000000,
        perf.conversation_api_transfer_bytes+transferred);
    if(code>=200&&code<300)perf.conversation_api_2xx++;
    else if(code>=400&&code<500)perf.conversation_api_4xx++;
    else if(code>=500&&code<600)perf.conversation_api_5xx++;
    else perf.conversation_api_unknown_status++;
    // The 429 subset is orthogonal to the 4xx status-class counter.
    if(code===429){
      perf.conversation_api_429++;
      if(age>=0&&age<=60000)perf.conversation_api_recent_429++;
      if(age>=0)perf.conversation_api_last_429_age_ms=Math.min(3600000,age);
    }
  }else if(routeType==='other-api')perf.other_api_observed++;
 }
}catch(_){}
try{perf.service_worker_controller=navigator.serviceWorker?.controller?1:0;}catch(_){}
let last_finished_age_ms=-1;
try {
 const entries=performance.getEntriesByType('resource');
 if(entries.length){
  const latest=entries[entries.length-1];
  const end=Number(latest.responseEnd || 0);
  if(end>0)last_finished_age_ms=Math.max(0,
   Math.min(3600000,Math.round(performance.now()-end)));
 }
}catch(_){}
let jsErrors={error_hooks_installed:false};
try{
 const observed=window.__chat2apiNativeDiagCounts;
 if(observed){
  // Only whitelisted numeric counters cross the CDP boundary.
  for(const key of [
    'js_exception_count','unhandled_rejection_count','resource_error_count',
    'js_type_error_count','js_reference_error_count','js_syntax_error_count',
    'js_range_error_count','js_other_typed_error_count',
    'js_unclassified_error_count','js_error_before_complete_count',
    'js_error_after_complete_count','js_first_party_error_count',
    'js_other_origin_error_count','js_origin_unknown_error_count']){
     const v=Number(observed[key]||0);
     jsErrors[key]=Number.isFinite(v)?Math.min(9999,Math.max(0,Math.floor(v))):0;
  }
  jsErrors.error_hooks_installed=true;
 }
}catch(_){}
const main=document.querySelector('main,[role="main"]');
// Main may be a shell while the actual reply is rendered elsewhere. Export
// structure only, never the text or the outerHTML of any element.
const visible=e=>{try{
 const r=e.getBoundingClientRect(),cs=getComputedStyle(e);
 return r.width>8&&r.height>8&&cs.display!=='none'&&cs.visibility!=='hidden';
}catch(_){return false;}};
const allTables=[...document.querySelectorAll('table,[role="table"]')].slice(0,500);
const visibleTables=allTables.filter(visible);
const middle=document.elementFromPoint(Math.floor(innerWidth*0.66),
 Math.floor(innerHeight*0.5));
const allBodyArticles=document.querySelectorAll('article,[role="article"]');
const allBodyMarkdown=document.querySelectorAll('.markdown,.prose,[data-testid*="markdown"]');
// Recover structural ancestry of rendered tables without exporting content.

const mainTables=visibleTables.filter(e=>main?.contains(e));
let tableRegion=mainTables[0]?.parentElement||null;
while(tableRegion&&mainTables.some(e=>!tableRegion.contains(e)))
 tableRegion=tableRegion.parentElement;
const scope=tableRegion&&main?.contains(tableRegion)?tableRegion:null;
const scopeText=scope?String(scope.innerText||''):'';
let scopeDepth=0,scopeChildIndex=-1,scopeAt=scope;
while(scopeAt&&scopeAt!==main&&scopeDepth<50){
 const parent=scopeAt.parentElement;
 if(parent===main)scopeChildIndex=[...main.children].indexOf(scopeAt);
 scopeDepth++;scopeAt=parent;
}
// A second conversation can contain substantial prose and just ONE table.
// Do not force the old 3-table capture heuristic onto it; report only
// aggregate topology of the longest visible main child for later triage.
const mainChildren=main?[...main.children].slice(0,40):[];
const childEvidence=mainChildren.map((e,i)=>({
 index:i,chars:Math.min(500000,String(e.innerText||'').length),
 tables:e.querySelectorAll('table,[role="table"]').length,
 headings:e.querySelectorAll('h1,h2,h3,h4,h5,h6').length,
 paragraphs:e.querySelectorAll('p').length,
 pre:e.querySelectorAll('pre').length,
 code:e.querySelectorAll('code').length,
 composer:e.querySelectorAll('form,textarea,[contenteditable="true"],[role="textbox"]').length,
 user:e.querySelectorAll('[data-message-author-role="user"],[data-testid="user-message"]').length
}));
childEvidence.sort((a,b)=>b.chars-a.chars);
const longestChild=childEvidence[0]||{index:-1,chars:0,tables:0,
 headings:0,paragraphs:0,pre:0,code:0,composer:0,user:0};
const scopeRect=scope?.getBoundingClientRect();
const scopeComposer=scope?scope.querySelectorAll('form,textarea,[contenteditable="true"],[role="textbox"]').length:0;
const scopeUserMarkers=scope?scope.querySelectorAll('[data-message-author-role="user"],[data-testid="user-message"]').length:0;
const scopeAssistantMarkers=scope?scope.querySelectorAll('[data-message-author-role="assistant"],[data-testid="assistant-message"]').length:0;
// Heading/p/section counts, not heading contents, class names or raw DOM.
const tableScopeStats=scope?{
  table_scope_tag: ['main','section','article','div'].includes(scope.tagName.toLowerCase())?scope.tagName.toLowerCase():'other',
  table_scope_depth:scopeDepth,
  table_scope_main_child_index:scopeChildIndex,
  table_scope_is_main:scope===main,
  table_scope_direct_children:scope.children.length,
  table_scope_visible_text_chars:Math.min(500000,scopeText.length),
  table_scope_table_count:mainTables.length,
  table_scope_heading_count:scope.querySelectorAll('h1,h2,h3,h4,h5,h6').length,
  table_scope_paragraph_count:scope.querySelectorAll('p').length,
  table_scope_image_count:scope.querySelectorAll('img').length,
  table_scope_button_count:scope.querySelectorAll('button').length,
  table_scope_composer_count:scopeComposer,
  table_scope_user_markers:scopeUserMarkers,
  table_scope_assistant_markers:scopeAssistantMarkers,
  table_scope_height_px:scopeRect?Math.min(100000,Math.round(scopeRect.height)):0,
  table_scope_top_y_px:scopeRect?Math.max(-100000,Math.min(100000,Math.round(scopeRect.top+scrollY))):0
}:{table_scope_tag:'none'};
return {ready_state:String(document.readyState||'unknown').slice(0,16),
  route, visibility_state:String(document.visibilityState||'unknown').slice(0,16),
  has_focus:typeof document.hasFocus==='function'?Boolean(document.hasFocus()):false,
  online_hint:typeof navigator!=='undefined' ? Boolean(navigator.onLine) : null,
  assistant_roles:document.querySelectorAll('[data-message-author-role="assistant"]').length,
  user_roles:document.querySelectorAll('[data-message-author-role="user"]').length,
  native_assistant_roles:document.querySelectorAll('h4[data-conversation-role="assistant"]').length,
  native_selections:document.querySelectorAll('[data-chatgpt-selection-message-id]').length,
  native_dil:document.querySelectorAll('[data-dil-message-id]').length,
  native_dil_sources:document.querySelectorAll('[data-dil-source-message-id]').length,
  conversation_turns:document.querySelectorAll('[data-testid="conversation-turn"],[data-testid^="conversation-turn-"]').length,
  numbered_conversation_turns:document.querySelectorAll('[data-testid^="conversation-turn-"]').length,
  agent_turns:document.querySelectorAll('main article .agent-turn,main [data-testid^="conversation-turn-"].agent-turn').length,
  agent_markdown_blocks:document.querySelectorAll('main .agent-turn .markdown').length,
  assistant_testids:document.querySelectorAll('[data-testid="assistant-message"],[data-testid="assistant-turn"]').length,
  verified_roleless_headings:heading,
  main_landmarks:document.querySelectorAll('main,[role="main"]').length,
  main_child_elements:main?main.children.length:0,
  main_element_count:main?Math.min(main.querySelectorAll('*').length,100000):0,
  // Only aggregate lengths/counts. Never export message text or resource URL.
  main_text_chars:main?Math.min(String(main.textContent||'').length,500000):0,
  // innerText count is visible/rendered text only; never export its contents.
  main_visible_text_chars:main?Math.min(String(main.innerText||'').length,500000):0,
  main_thread_candidates:main?main.querySelectorAll('#thread,[data-testid="conversation-turns"],[data-testid="conversation-panel"],[role="log"]').length:0,
  main_busy_regions:main?main.querySelectorAll('[aria-busy="true"]').length:0,
  page_busy_regions:document.querySelectorAll('[aria-busy="true"]').length,
  main_visible_images:main?[...main.querySelectorAll('img')].filter(e=>e.getBoundingClientRect().width>100&&e.getBoundingClientRect().height>100).length:0,
  body_text_chars:document.body?Math.min(String(document.body.textContent||'').length,500000):0,
  body_visible_text_chars:document.body?Math.min(String(document.body.innerText||'').length,500000):0,
  body_article_count:allBodyArticles.length,
  body_markdown_blocks:allBodyMarkdown.length,
  body_visible_tables:visibleTables.length,
  main_visible_tables:mainTables.length,
  main_longest_child_index:longestChild.index,
  main_longest_child_visible_text_chars:longestChild.chars,
  main_longest_child_tables:longestChild.tables,
  main_longest_child_headings:longestChild.headings,
  main_longest_child_paragraphs:longestChild.paragraphs,
  main_longest_child_pre:longestChild.pre,
  main_longest_child_code:longestChild.code,
  main_longest_child_composers:longestChild.composer,
  main_longest_child_user_markers:longestChild.user,
  ...tableScopeStats,
  body_visible_table_rows:Math.min(100000,visibleTables.reduce((n,e)=>
   n+e.querySelectorAll('tr,[role="row"]').length,0)),
  body_visible_tables_outside_main:visibleTables.filter(e=>!e.closest('main,[role="main"]')).length,
  viewport_center_in_main:!!middle?.closest('main,[role="main"]'),
  viewport_center_in_table:!!middle?.closest('table,[role="table"]'),
  viewport_center_in_article:!!middle?.closest('article,[role="article"]'),
  main_markdown_blocks:document.querySelectorAll('main .markdown,main .prose,#thread .markdown').length,
  main_article_count:main?main.querySelectorAll('article,[role="article"]').length:0,
  main_message_like_count:main?main.querySelectorAll('[data-testid*="message"],[data-testid*="turn"],[data-message-id],[data-turn-id]').length:0,
  main_editable_count:main?main.querySelectorAll('[contenteditable="true"],textarea').length:0,
  main_button_count:main?main.querySelectorAll('button').length:0,
  iframe_count:document.querySelectorAll('iframe').length,
  shadow_root_count:[...document.querySelectorAll('*')].slice(0,5000).filter(e=>!!e.shadowRoot).length,
  app_roots:document.querySelectorAll('#__next,#root,[data-nextjs-root]').length,
  skeleton_count:document.querySelectorAll('[data-testid*="skeleton"]').length,
  aria_busy_count:document.querySelectorAll('[aria-busy="true"]').length,
  last_finished_age_ms,
  alert_count:document.querySelectorAll('[role="alert"],[aria-live="assertive"]').length,
  error_testid_count:document.querySelectorAll('[data-testid*="error"],[data-testid*="failed"]').length,
  ...jsErrors,
  password_fields:document.querySelectorAll('input[type="password"]').length>0,
  challenge_frames:document.querySelectorAll('iframe[src*="challenge"],iframe[src*="captcha"]').length>0,
  loading_skeletons:document.querySelectorAll('[data-testid*="skeleton"],[aria-busy="true"]').length>0,
  ...perf};
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


def history_content_signature(report):
    """Privacy-safe in-memory structural fingerprint; never exported."""
    if not isinstance(report,dict) or not report.get("ok"):
        return None
    if report.get("reason") or report.get("generating"):
        return None
    count=report.get("counts") or {}
    rect=report.get("rect") or {}
    if (not (report.get("root") or {}).get("children")
        or not count.get("nodes") or not count.get("text_chars")):
        return None

    keys=("nodes","text_chars","tables","images","headings",
          "max_children","grids","flex")
    dimensions=("w","h")
    return (report.get("scope"),native_text_digest(report),
        tuple(count.get(k) for k in keys),
        tuple(rect.get(k) for k in dimensions))


def native_text_digest(report):
    """Local-only integrity fingerprint; never export source text/hash."""
    digest=hashlib.sha256()
    def scan(node):
        if not isinstance(node,dict):
            return
        if node.get("tag")=="#text":
            digest.update(str(node.get("text") or "").encode("utf-8"))
        else:
            for child in node.get("children") or ():
                scan(child)
    scan(report.get("root") or {})
    return digest.digest()


def image_loaded_count(report):
    """Loaded image count within the already selected reply candidate."""
    count=0
    def scan(node):
        nonlocal count
        if not isinstance(node,dict):
            return
        if isinstance(node.get("image"),dict) and node["image"].get("loaded"):
            count+=1
        for child in node.get("children") or ():
            scan(child)
    scan(report.get("root") or {})
    return count


def image_dimensions_sequence(report):
    """Exact DOM traversal image index and size ordering, no URL exports."""
    result=[]
    def walk(node):
        if not isinstance(node,dict):return
        image=node.get("image")
        if isinstance(image,dict):
            result.append((image.get("index"),
                image.get("natural_width"),image.get("natural_height")))
        for child in node.get("children") or ():
            walk(child)
    walk(report.get("root") or {})
    return result



def native_png_crop_rows(payload,x,y,w,h):
    """Decode bounded non-interlaced Chrome RGB/RGBA PNG screenshot region.

    No Pillow or external fetch. Fail closed on unknown PNG layout, bad
    checksum, overlarge chunks/dimensions or invalid crop bounds.
    """
    if (not isinstance(payload,bytes) or len(payload)>8*1024*1024
        or not payload.startswith(b"\x89PNG\r\n\x1a\n")):
        raise ValueError("unsupported_png")
    pos=8; width=height=channels=0;parts=[];compressed_size=0
    while pos+12<=len(payload):
        count=struct.unpack(">I",payload[pos:pos+4])[0]
        kind=payload[pos+4:pos+8]
        if count>8*1024*1024 or pos+12+count>len(payload):
            raise ValueError("bad_png_chunk")
        chunk=payload[pos+8:pos+8+count]
        crc=struct.unpack(">I",payload[pos+8+count:pos+12+count])[0]
        if (binascii.crc32(kind+chunk)&0xffffffff)!=crc:
            raise ValueError("bad_png_crc")
        if kind==b"IHDR":
            if count!=13 or width:
                raise ValueError("bad_png_header")
            width,height,depth,color,comp,filter_type,interlace=struct.unpack(
                ">IIBBBBB",chunk)
            if (not 0<width<=3000 or not 0<height<=2000 or depth!=8
                or color not in (2,6) or comp or filter_type or interlace):
                raise ValueError("unsupported_png_layout")
            channels=3 if color==2 else 4
        elif kind==b"IDAT":
            compressed_size+=count
            if compressed_size>8*1024*1024:
                raise ValueError("png_idat_limit")
            parts.append(chunk)
        elif kind==b"IEND":
            break
        pos+=12+count
    if (not width or not parts or x<0 or y<0 or w<1 or h<1
        or x+w>width or y+h>height):
        raise ValueError("crop_outside_png")
    rowbytes=width*channels
    expected=(rowbytes+1)*height
    if expected>30*1024*1024:
        raise ValueError("png_decode_limit")
    inflater=zlib.decompressobj()
    raw=inflater.decompress(b"".join(parts),expected+1)
    if inflater.unconsumed_tail or not inflater.eof or len(raw)!=expected:
        raise ValueError("invalid_png_stream")
    previous=bytearray(rowbytes)
    extracted=[];at=0
    for line in range(height):
        predictor=raw[at];at+=1
        row=bytearray(raw[at:at+rowbytes]);at+=rowbytes
        if predictor==1:
            for k in range(channels,rowbytes):
                row[k]=(row[k]+row[k-channels])&255
        elif predictor==2:
            for k in range(rowbytes):
                row[k]=(row[k]+previous[k])&255
        elif predictor==3:
            for k in range(rowbytes):
                left=row[k-channels] if k>=channels else 0
                row[k]=(row[k]+(left+previous[k])//2)&255
        elif predictor==4:
            for k in range(rowbytes):
                left=row[k-channels] if k>=channels else 0
                above=previous[k]
                upperleft=previous[k-channels] if k>=channels else 0
                midpoint=left+above-upperleft
                da=abs(midpoint-left)
                db=abs(midpoint-above)
                dc=abs(midpoint-upperleft)
                nearest=left if da<=db and da<=dc else (above if db<=dc else upperleft)
                row[k]=(row[k]+nearest)&255
        elif predictor!=0:
            raise ValueError("unknown_png_filter")
        if y<=line<y+h:
            extracted.append(bytes(row[x*channels:(x+w)*channels]))
        previous=row
    return color,extracted


def native_png_pack_rows(width,height,color,rows):
    if color not in (2,6) or len(rows)!=height:
        raise ValueError("png_pack_invalid")
    bytes_per_pixel=3 if color==2 else 4
    if any(len(row)!=width*bytes_per_pixel for row in rows):
        raise ValueError("png_pack_row_invalid")
    def chunk(kind,value):
        return (struct.pack(">I",len(value))+kind+value+
                struct.pack(">I",binascii.crc32(kind+value)&0xffffffff))
    header=struct.pack(">IIBBBBB",width,height,8,color,0,0,0)
    body=zlib.compress(b"".join(b"\0"+r for r in rows),6)
    return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",header)+
            chunk(b"IDAT",body)+chunk(b"IEND",b""))


def screenshot_image_mapping(report,strips,image_fingerprints,archive):
    """Associate DOM image rectangles to *already captured* screenshot strips.

    No network access or extra browser interaction. Pillow is optional; the
    bounded standard-library PNG RGB/RGBA fallback generates visible-size PNG
    crops from Chrome screenshots. Neither is original media.
    """
    result=[]
    try:
        from PIL import Image
    except ImportError:
        Image=None
    root=(report.get("root") or {}).get("children") or []
    if len(root)!=1 or not isinstance(root[0],dict):
        return result
    base=root[0].get("rect") or {}
    source={i.get("image_index"):i for i in image_fingerprints
            if isinstance(i,dict) and type(i.get("image_index")) is int}
    rects=report.get("image_rects") or []
    if not strips or not 0<len(rects)<=40:
        return result
    for rect in rects:
        if not isinstance(rect,dict):
            continue
        idx=rect.get("index")
        if type(idx) is not int or idx<0 or idx>=40 or idx not in source:
            continue
        if any(type(rect.get(k)) is not int for k in ("x","y","w","h")):
            continue
        x=rect["x"]-int(base.get("x") or 0)
        y=rect["y"]-int(base.get("y") or 0)
        width,height=rect["w"],rect["h"]
        if (x<0 or y<0 or width<1 or height<1
            or width>2000 or height>2000
            or x+width>strips[0][3]):
            continue
        coverage=0
        segments=[]
        for filename,offset,data,sw,sh in strips:
            start=max(y,offset)
            end=min(y+height,offset+sh)
            if end<=start:continue
            if start!=y+coverage or x+width>sw:
                break
            segments.append((filename,offset,data,x,start-offset,
                             width,end-start,coverage))
            coverage+=end-start
            if coverage==height:break
        if coverage!=height:
            continue
        fact=source[idx]
        record={
            "image_index":idx,
            "source_url_sha256":fact["source_url_sha256"],
            "natural_width":fact["natural_width"],
            "natural_height":fact["natural_height"],
            "rendered_width":width,"rendered_height":height,
            "original_bytes_captured":False,
            "source":"diagnostic-screenshot-pixels",
            "segments":[{
                "screenshot":filename,"source_x":cx,"source_y":cy,
                "width":cw,"height":ch,"target_y":target
            } for filename,_,_,cx,cy,cw,ch,target in segments]
        }
        try:
            if Image is not None:
                canvas=Image.new("RGB",(width,height))
                for _,_,png,cx,cy,cw,ch,target in segments:
                    with Image.open(BytesIO(png)) as screen:
                        if screen.size[0]<cx+cw or screen.size[1]<cy+ch:
                            raise ValueError("screenshot_geometry_changed")
                        canvas.paste(screen.convert("RGB").crop(
                            (cx,cy,cx+cw,cy+ch)),(0,target))
                output=BytesIO()
                canvas.save(output,format="PNG")
                payload=output.getvalue()
                record["crop_decoder"]="Pillow"
            else:
                rows=[None]*height
                color=None
                for _,_,png,cx,cy,cw,ch,target in segments:
                    part_color,part=native_png_crop_rows(png,cx,cy,cw,ch)
                    if color is not None and color!=part_color:
                        raise ValueError("mixed_png_color_type")
                    color=part_color
                    rows[target:target+ch]=part
                if any(row is None for row in rows):
                    raise ValueError("png_crop_incomplete")
                payload=native_png_pack_rows(width,height,color,rows)
                record["crop_decoder"]="stdlib-png"
            if len(payload)<=4*1024*1024:
                name=f"media/screenshot_image_{idx:02d}.png"
                archive.writestr(name,payload)
                record["rendered_png_file"]=name
                record["rendered_png_sha256"]=hashlib.sha256(payload).hexdigest()
        except (OSError,ValueError,TypeError,zlib.error,struct.error):
            record["crop_decoder"]="not-supported"
        result.append(record)
    return result


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

_PROBE_KEYS = {"ready_state", "route", "visibility_state", "has_focus",
    "online_hint", "assistant_roles", "user_roles", "conversation_turns",
    "numbered_conversation_turns", "agent_turns", "agent_markdown_blocks",
    "assistant_testids", "verified_roleless_headings", "main_landmarks",
    "main_child_elements", "main_element_count", "main_text_chars",
    "main_visible_text_chars", "main_thread_candidates",
    "main_busy_regions", "page_busy_regions", "main_visible_images",
    "body_text_chars", "body_visible_text_chars",
    "body_article_count", "body_markdown_blocks",
    "body_visible_tables", "main_visible_tables", "body_visible_table_rows",
    "main_longest_child_index", "main_longest_child_visible_text_chars",
    "main_longest_child_tables", "main_longest_child_headings",
    "main_longest_child_paragraphs", "main_longest_child_pre",
    "main_longest_child_code", "main_longest_child_composers",
    "main_longest_child_user_markers",
    "table_scope_tag", "table_scope_depth", "table_scope_main_child_index",
    "table_scope_is_main", "table_scope_direct_children",
    "table_scope_visible_text_chars", "table_scope_table_count",
    "table_scope_heading_count", "table_scope_paragraph_count",
    "table_scope_image_count", "table_scope_button_count",
    "table_scope_composer_count", "table_scope_user_markers",
    "table_scope_assistant_markers", "table_scope_height_px",
    "table_scope_top_y_px",
    "body_visible_tables_outside_main", "viewport_center_in_main",
    "viewport_center_in_table", "viewport_center_in_article",
    "main_markdown_blocks", "main_article_count",
    "main_message_like_count", "main_editable_count", "main_button_count",
    "iframe_count",
    "shadow_root_count", "app_roots",
    "skeleton_count", "aria_busy_count", "last_finished_age_ms",
    "password_fields", "challenge_frames", "loading_skeletons",
    "fetch_total", "fetch_http_2xx",
    "fetch_http_401_403", "fetch_http_404", "fetch_http_429",
    "fetch_http_recent_429", "fetch_http_429_age_unknown",
    "fetch_http_other_4xx", "fetch_http_5xx", "fetch_status_unavailable",
    "conversation_api_observed", "conversation_api_2xx", "conversation_api_4xx",
    "conversation_api_5xx", "conversation_api_unknown_status",
    "conversation_api_429", "conversation_api_recent_429",
    "conversation_api_last_429_age_ms",
    "conversation_api_decoded_bytes", "conversation_api_transfer_bytes",
    "conversation_api_decoded_nonzero", "conversation_api_size_unavailable",
    "other_api_observed", "service_worker_controller",
    "js_exception_count", "unhandled_rejection_count", "resource_error_count",
    "js_type_error_count", "js_reference_error_count",
    "js_syntax_error_count", "js_range_error_count",
    "js_other_typed_error_count", "js_unclassified_error_count",
    "js_error_before_complete_count", "js_error_after_complete_count",
    "js_first_party_error_count", "js_other_origin_error_count",
    "js_origin_unknown_error_count",
    "error_hooks_installed", "alert_count", "error_testid_count",
    "capture_error"}

async def inspect_page(cdp, request_id):
    """Evaluate assistant DOM and a separate privacy-filtered status probe.

    An exception in one evaluator must not prevent gathering evidence from
    the other. Do not export exception text/stack, page contents or URLs.
    """
    report=None
    capture_error="none"
    try:
        result=await cdp.call("Runtime.evaluate",{
            "expression":CAPTURE+"("+json.dumps(request_id)+")",
            "returnByValue":True,"silent":True})
        if result.get("exceptionDetails"):
            capture_error="capture-js-exception"
        else:
            candidate=(result.get("result") or {}).get("value")
            if isinstance(candidate,dict):
                if candidate.get("ok"):report=candidate
                else:
                    code=str(candidate.get("error") or "capture-rejected")
                    capture_error=code if code in (
                        "no-verified-assistant-boundary","reply-sibling-limit",
                        "empty-verified-reply","capture-rejected") else "capture-rejected"
            else:capture_error="capture-empty-result"
    except Exception:
        capture_error="capture-cdp-error"
    probe={}
    try:
        result=await cdp.call("Runtime.evaluate",{
            "expression":PAGE_PROBE,"returnByValue":True,"silent":True})
        if not result.get("exceptionDetails"):
            raw=(result.get("result") or {}).get("value")
            if isinstance(raw,dict):
                for key in _PROBE_KEYS-{"capture_error"}:
                    val=raw.get(key)
                    if isinstance(val,bool):probe[key]=val
                    elif isinstance(val,int):
                        limit=100000000 if key in ("conversation_api_decoded_bytes",
                            "conversation_api_transfer_bytes") else (
                            3600000 if key in ("conversation_api_last_429_age_ms",
                            "last_finished_age_ms") else 100000)
                        probe[key]=min(max(val,0),limit)
                    elif isinstance(val,str) and key in (
                        "ready_state","route","visibility_state","table_scope_tag"):
                        if key!="table_scope_tag" or val in (
                            "none","main","section","article","div","other"):
                            probe[key]=val[:24]
    except Exception:
        pass
    probe["capture_error"]=capture_error
    return report,probe


def failure_reason(probe):
    """Fixed error code, never based on raw page text or exception messages."""
    if probe.get("navigation_route")=="target-not-visible":
        return "history_diagnostic_tab_disappeared"
    if (probe.get("capture_error")=="no-verified-assistant-boundary"
        and probe.get("body_visible_tables",0)>=3
        and probe.get("table_scope_composer_count",0)>0):
        return "history_rich_content_boundary_overlaps_editor"
    if probe.get("history_content_visible_unassigned"):
        return "history_visible_content_without_safe_reply_boundary"
    if probe.get("history_load_timeout"):
        return "history_load_stability_timeout_no_complete_evidence"
    if probe.get("history_existing_content_not_stable"):
        return "history_existing_content_not_stable"
    if (probe.get("body_visible_tables") or
        probe.get("body_markdown_blocks") or
        probe.get("existing_tab_visible_content_candidates")) and not (
        probe.get("assistant_roles") or probe.get("conversation_turns")
         or probe.get("native_assistant_roles")):
        return "history_visible_content_unrecognized_by_assistant_selectors"
    if probe.get("navigation_route")=="navigation-error":
        return "history_browser_navigation_failed"
    if probe.get("navigation_route")=="unexpected-redirect":
        return "history_unexpected_redirect"
    if probe.get("navigation_route")=="target-not-visible":
        return "history_diagnostic_tab_missing"
    if probe.get("navigation_route")=="access-gate":
        return "history_access_gate_detected"
    if probe.get("challenge_frames"):return "history_challenge_detected"
    if probe.get("password_fields") or probe.get("route")=="access-gate":
        return "history_access_gate_detected"
    # A document whose load event finished but whose conversation remains

    if (probe.get("route")=="conversation"
        and probe.get("ready_state")=="complete"
        and probe.get("loading_skeletons")
        and not (probe.get("assistant_roles") or probe.get("assistant_testids")
                 or probe.get("conversation_turns") or probe.get("user_roles")
                 or probe.get("verified_roleless_headings"))):
        if (probe.get("conversation_api_recent_429")
            or probe.get("existing_tab_recent_429")):
            return "history_conversation_recent_conversation_api_429"
        if probe.get("fetch_http_recent_429"):
            return "history_conversation_recent_other_http_429"
        # Old HTTP 429 in a *different* old tab is contextual evidence,
        # not the proximate failure reason in a currently healthy page.
        if probe.get("recovery_fresh_attempted"):
            return "history_fresh_navigation_hydration_stalled"
        if probe.get("recovery_cooldown_remaining_sec"):
            return "history_hydration_stalled_recovery_cooldown"
        if (probe.get("fetch_http_401_403")
            or probe.get("fetch_http_other_4xx")):
            return "history_conversation_stalled_with_http_4xx"
        if probe.get("fetch_http_5xx"):
            return "history_conversation_stalled_with_http_5xx"
        if probe.get("visibility_state")=="hidden":
            return "history_conversation_stalled_background_tab"
        if probe.get("reload_attempted"):
            return "history_conversation_hydration_stalled_after_reload"
        return "history_conversation_hydration_stalled"
    if probe.get("ready_state")=="loading" or probe.get("loading_skeletons"):
        return "history_page_still_loading"
    if probe.get("capture_error") in (
        "capture-js-exception","capture-cdp-error",
        "cdp-tab-connection-error","cdp-endpoint-unavailable"):
        return "history_capture_runtime_failed"
    if (not probe.get("assistant_roles") and not probe.get("assistant_testids")
        and not probe.get("agent_turns")
        and not probe.get("verified_roleless_headings")):
        return "history_assistant_boundary_not_found"
    return "history_assistant_capture_rejected"


def status(folder,stage,percent,error=None,**meta):
    data={"tool_version":VERSION,"status":"failed" if error else "running","stage":stage,
      "percent":percent,"updated_at":int(time.time()),"error":error,**meta}
    if "page_probe" not in data:
        try:
            previous=json.loads((folder/"status.json").read_text())
            if isinstance(previous.get("page_probe"),dict):
                data["page_probe"]=previous["page_probe"]
        except (ValueError,OSError,TypeError):pass
    tmp=folder/"status.tmp";tmp.write_text(json.dumps(data));tmp.replace(folder/"status.json")

    # Never append page URLs, Chrome credentials, request text or image bytes.
    try:
        with (folder/"diagnostic.log").open("a",encoding="utf8") as stream:
            stream.write(json.dumps({key:data.get(key) for key in (
                "tool_version","status","stage","percent","updated_at","error",
                "screenshots")},ensure_ascii=False)+"\n")
    except OSError: pass

def fresh_navigation_gate(folder, worker_id, cdp_port, url, cooldown=600):
    """Rate-limit extra diagnostic navigations across independent jobs.

    The marker stores only the attempt timestamp and its filename is a digest
    of the Worker, CDP slot, and exact history URL; no raw URL or messages.
    Fail closed if the local marker cannot be safely read or locked.
    """
    digest=hashlib.sha256((worker_id+"|"+str(cdp_port)+"|"+url).encode()).hexdigest()
    gate=folder.parent/(".history-recovery-"+digest[:32])
    flags=os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0)
    try:
        fd=os.open(gate,flags,0o600)
        with os.fdopen(fd,"r+",encoding="ascii") as stream:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            raw=stream.read(32).strip()
            previous=int(raw) if raw.isdecimal() else 0
            now=int(time.time())
            if previous and 0<=now-previous<cooldown:
                return False,cooldown-(now-previous),"cooldown"
            stream.seek(0)
            stream.truncate()
            stream.write(str(now))
            stream.flush()
            os.fsync(stream.fileno())
            return True,0,"granted"
    except (OSError,ValueError,UnicodeError):
        return False,cooldown,"gate-unavailable"


def desktop_chrome_affinity(cdp_port):
    """Compare X11 visible Chrome PIDs with the selected CDP debug port.

    Controller slot 1: DISPLAY :99, CDP 9222; slot N uses :98+N
    and CDP 9221+N. Never export PIDs, process args or desktop titles.
    A zero match is inconclusive if command lines were unreadable.
    """
    evidence={"desktop_affinity_checked":False,
        "desktop_chrome_windows":0,"desktop_chrome_cdp_port_matches":0,
        "desktop_chrome_other_ports":0,"desktop_chrome_port_unknown":0,
        "desktop_x11_query_responded":False,
        "desktop_x11_visible_window_candidates":0}
    env=dict(os.environ)
    env["DISPLAY"]=":"+str(int(cdp_port)-9123)
    try:
        # Broader X11 census distinguishes "no Chrome window matched"
        # from "DISPLAY is unavailable". Never export titles/IDs.
        census=subprocess.run(
            ["xdotool","search","--onlyvisible","--name",".*"],
            capture_output=True,text=True,timeout=3,check=False,env=env)
        evidence["desktop_x11_query_responded"]=census.returncode in (0,1)
        if evidence["desktop_x11_query_responded"]:
            evidence["desktop_x11_visible_window_candidates"]=min(1000,
                sum(1 for w in census.stdout.splitlines() if w.isdecimal()))
        found=subprocess.run(
            ["xdotool","search","--onlyvisible","--class",".*[Cc]hrome.*"],
            capture_output=True,text=True,timeout=3,check=False,env=env)
        if found.returncode not in (0,1):return evidence
        window_ids=[w for w in found.stdout.splitlines() if w.isdecimal()][:12]
        evidence["desktop_affinity_checked"]=True
        evidence["desktop_chrome_windows"]=len(window_ids)
        port_flag=("--remote-debugging-port="+str(int(cdp_port))).encode()
        for window in window_ids:
            try:
                pid_result=subprocess.run(
                    ["xdotool","getwindowpid",window],
                    capture_output=True,text=True,timeout=2,check=False,env=env)
                raw_pid=pid_result.stdout.strip()
                if pid_result.returncode!=0 or not raw_pid.isdecimal():
                    evidence["desktop_chrome_port_unknown"]+=1
                    continue
                args=(Path("/proc")/raw_pid/"cmdline").read_bytes().split(b"\0")
                if port_flag in args:
                    evidence["desktop_chrome_cdp_port_matches"]+=1
                elif any(part.startswith(b"--remote-debugging-port=")
                         for part in args):
                    evidence["desktop_chrome_other_ports"]+=1
                else:
                    evidence["desktop_chrome_port_unknown"]+=1
            except (OSError,subprocess.TimeoutExpired,ValueError):
                evidence["desktop_chrome_port_unknown"]+=1
    except (OSError,subprocess.TimeoutExpired,ValueError):
        pass
    return evidence


async def capture(args):
    """Always close only the CDP tabs this tool created, including on failure."""
    owned_tabs=[]
    try:
        await _capture_inner(args,owned_tabs)
    finally:
        for tab_id in owned_tabs:
            try:
                with urlopen(f"http://127.0.0.1:{args.cdp_port}/json/close/"+
                     quote(tab_id,safe=""),timeout=3):pass
            except (OSError,ValueError,TimeoutError):
                pass

async def _capture_inner(args,owned_tabs):
    import websockets
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    status(folder,"discovering",8)
    target_url=safe_url(args.conversation_url)
    desktop_evidence=desktop_chrome_affinity(args.cdp_port)
    targets=json_get(args.cdp_port,"/json/list")
    def target(t):
        u=urlsplit(str(t.get("url") or ""))
        return t.get("type")=="page" and u.hostname in HOSTS and u.scheme=="https" and t.get("webSocketDebuggerUrl")
    targets=[t for t in targets if target(t)]
    picked=None;report=None;ws=None;last_probe={}
    # URL-targeted diagnosis always uses its OWN fresh tab, even when an
    # identical history page is already open in a live request's tab.
    for t in (targets[:60] if not target_url else []):
        endpoint=ws_endpoint(t["webSocketDebuggerUrl"],args.cdp_port)
        async with websockets.connect(endpoint,open_timeout=5,max_size=24*1024*1024) as conn:
            cdp=CDP(conn)
            await cdp.call("Runtime.enable")
            value,last_probe=await inspect_page(cdp,args.request_id)
            if not value:continue
            if not target_url and value.get("request_marker")!=args.request_id:continue
            picked=t;report=value;break
    binding="live-request-marker"
    tab_origin="dedicated-worker-tab"
    history_readiness={}
    hints=recovery_hints()
    existing_stability={}

    async def read_existing_history_tab(url,diagnostic_tab_id):
        """Read-only exact-conversation fallback; never navigate an existing tab.

        Only uniquely verified, non-generating reply boundaries can be used.
        The caller supplied the exact history URL; binding stays unverified.
        """
        nonlocal tab_origin,last_probe
        matches=[]
        try:
            for tab in json_get(args.cdp_port,"/json/list"):
                if (tab.get("type")!="page"
                    or str(tab.get("id") or "")==diagnostic_tab_id
                    or not tab.get("webSocketDebuggerUrl")):
                    continue
                actual=urlsplit(str(tab.get("url") or ""))
                expected=urlsplit(url)
                if (actual.scheme,actual.hostname,actual.port,actual.path)!=(
                    "https",expected.hostname,expected.port,expected.path):
                    continue
                if actual.username or actual.password or actual.hostname not in HOSTS:
                    continue
                matches.append(tab)
                if len(matches)>=8:break
        except (OSError,ValueError,TypeError):
            pass
        verified=[]
        max_text_chars=0
        max_markdown=0
        max_articles=0
        max_message_like=0
        busy_tabs=0
        rate_limited_tabs=0
        recent_429_tabs=0
        historical_429_tabs=0
        inspected_tabs=0
        captured_errors=0
        sample_probe=None
        content_pending=0
        sample_rank=(-1,-1,-1,-1,-1,-1,-1)
        structural_fingerprints=set()
        max_body_tables=0
        max_body_rows=0
        max_body_markdown=0
        max_table_scope_text=0
        max_table_scope_headings=0
        max_table_scope_paragraphs=0
        max_table_scope_composers=0
        max_table_scope_user_markers=0
        min_table_scope_depth=100000
        table_scopes_main=0
        max_visible_text=0
        focused_tabs=0
        visible_content_candidates=0
        for tab in matches:
            try:
                endpoint=ws_endpoint(tab["webSocketDebuggerUrl"],args.cdp_port)
                async with websockets.connect(endpoint,open_timeout=5,
                        max_size=24*1024*1024) as connection:
                    cdp=CDP(connection)
                    await cdp.call("Runtime.enable")
                    result,probe=await inspect_page(cdp,args.request_id)
                    inspected_tabs+=1
                    chars=int(probe.get("main_text_chars") or 0)
                    rank=(int(probe.get("assistant_roles") or 0),
                          int(probe.get("body_visible_tables") or 0),
                          int(probe.get("body_markdown_blocks") or 0),
                          int(probe.get("main_message_like_count") or 0),
                          int(probe.get("main_article_count") or 0),
                          int(probe.get("main_markdown_blocks") or 0),chars)
                    if rank>sample_rank:
                        sample_probe=probe
                        sample_rank=rank
                    structural_fingerprints.add((
                        int(probe.get("main_element_count") or 0),chars,
                        int(probe.get("main_article_count") or 0),
                        int(probe.get("main_message_like_count") or 0),
                        int(probe.get("body_visible_tables") or 0),
                        int(probe.get("body_markdown_blocks") or 0),
                        int(probe.get("aria_busy_count") or 0)))
                    max_body_tables=max(max_body_tables,int(probe.get("body_visible_tables") or 0))
                    max_table_scope_text=max(max_table_scope_text,
                        int(probe.get("table_scope_visible_text_chars") or 0))
                    max_table_scope_headings=max(max_table_scope_headings,
                        int(probe.get("table_scope_heading_count") or 0))
                    max_table_scope_paragraphs=max(max_table_scope_paragraphs,
                        int(probe.get("table_scope_paragraph_count") or 0))
                    max_table_scope_composers=max(max_table_scope_composers,
                        int(probe.get("table_scope_composer_count") or 0))
                    max_table_scope_user_markers=max(max_table_scope_user_markers,
                        int(probe.get("table_scope_user_markers") or 0))
                    min_table_scope_depth=min(min_table_scope_depth,
                        int(probe.get("table_scope_depth") or 0))
                    table_scopes_main+=int(bool(probe.get("table_scope_is_main")))
                    max_body_rows=max(max_body_rows,int(probe.get("body_visible_table_rows") or 0))
                    max_body_markdown=max(max_body_markdown,int(probe.get("body_markdown_blocks") or 0))
                    max_visible_text=max(max_visible_text,int(probe.get("body_visible_text_chars") or 0))
                    focused_tabs+=int(bool(probe.get("has_focus")))
                    visible_content_candidates+=int(bool(
                        probe.get("body_visible_tables") or
                        probe.get("body_markdown_blocks")))
                    max_text_chars=max(max_text_chars,chars)
                    max_markdown=max(max_markdown,int(probe.get("main_markdown_blocks") or 0))
                    max_articles=max(max_articles,int(probe.get("main_article_count") or 0))
                    max_message_like=max(max_message_like,int(probe.get("main_message_like_count") or 0))
                    busy_tabs+=int(bool(probe.get("loading_skeletons")))
                    rate_limited_tabs+=int(bool(probe.get("fetch_http_429") or
                        probe.get("conversation_api_429")))
                    recent_429_tabs+=int(bool(probe.get("fetch_http_recent_429") or
                        probe.get("conversation_api_recent_429")))
                    historical_429_tabs+=int(bool(
                        (probe.get("fetch_http_429") or probe.get("conversation_api_429"))
                        and not (probe.get("fetch_http_recent_429")
                            or probe.get("conversation_api_recent_429"))))
                    captured_errors+=int(probe.get("capture_error") not in
                        ("none","no-verified-assistant-boundary"))
                    tab_id=str(tab.get("id") or "")
                    signature=history_content_signature(result)
                    quiet=bool(
                        probe.get("ready_state")=="complete"
                        and probe.get("route")=="conversation"
                        and (int(probe.get("last_finished_age_ms") or 0)>=2000
                            or not probe.get("fetch_total")))
                    now=time.monotonic()
                    if signature and quiet:
                        last=existing_stability.get(tab_id)
                        if not last or last[0]!=signature:
                            existing_stability[tab_id]=(signature,now,1)
                        else:
                            existing_stability[tab_id]=(signature,last[1],last[2]+1)
                        _,since,samples=existing_stability[tab_id]
                        if (samples>=HISTORY_LOAD_MIN_SAMPLES and
                            now-since>=HISTORY_LOAD_STABLE_SECONDS):
                            verified.append((tab,result))
                        else:
                            content_pending+=1
                    elif signature:
                        existing_stability.pop(tab_id,None)
                        content_pending+=1
                    else:
                        existing_stability.pop(tab_id,None)
            except Exception:
                continue
        evidence={"existing_tab_readonly_checked":True,
            "existing_tab_candidates":len(matches),
            "existing_tab_verified":len(verified),
            "existing_tab_content_pending":content_pending,
            "existing_tab_max_main_text_chars":max_text_chars,
            "existing_tab_ambiguous":len(verified)>1,
            "existing_tab_inspected":inspected_tabs,
            "existing_tab_busy":busy_tabs,
            "existing_tab_rate_limited":rate_limited_tabs,
            "existing_tab_recent_429":recent_429_tabs,
            "existing_tab_historical_429":historical_429_tabs,
            "existing_tab_distinct_structures":len(structural_fingerprints),
            "existing_tab_max_body_tables":max_body_tables,
            "existing_tab_max_table_scope_visible_text_chars":max_table_scope_text,
            "existing_tab_max_table_scope_headings":max_table_scope_headings,
            "existing_tab_max_table_scope_paragraphs":max_table_scope_paragraphs,
            "existing_tab_max_table_scope_composers":max_table_scope_composers,
            "existing_tab_max_table_scope_user_markers":max_table_scope_user_markers,
            "existing_tab_min_table_scope_depth":min_table_scope_depth if inspected_tabs else 0,
            "existing_tab_table_scope_is_main_count":table_scopes_main,
            "existing_tab_max_body_table_rows":max_body_rows,
            "existing_tab_max_body_markdown_blocks":max_body_markdown,
            "existing_tab_max_body_visible_text_chars":max_visible_text,
            "existing_tab_focused":focused_tabs,
            "existing_tab_visible_content_candidates":visible_content_candidates,
            "existing_tab_max_markdown_blocks":max_markdown,
            "existing_tab_max_articles":max_articles,
            "existing_tab_max_message_like":max_message_like,
            "existing_tab_capture_errors":captured_errors}
        if sample_probe:
            last_probe={**desktop_evidence,**sample_probe,
                "navigation_route":"conversation",**evidence}
        if len(verified)==1:
            tab_origin="existing-history-tab-readonly"
            return verified[0][0],verified[0][1],evidence
        return None,None,evidence

    async def open_history_page(url,allow_reload=True):
        """Always open a dedicated tab in the selected Worker's existing Chrome.

        Never reuse/navigate a tab owned by an API request or another
        diagnosis, and never attempt authentication or submit a new prompt.
        """
        nonlocal last_probe,history_readiness
        safe_url(url)
        status(folder,"opening-new-tab",15,open_mode="dedicated-worker-tab")

        initial_url=("about:blank" if not allow_reload else url)
        with urlopen(Request(f"http://127.0.0.1:{args.cdp_port}/json/new?"+
                quote(initial_url,safe=""),method="PUT"),timeout=8) as response:
            created=json.load(response)
        tab_id=str(created.get("id") or "")
        if not re.fullmatch(r"[a-fA-F0-9]{8,64}",tab_id):
            raise RuntimeError("diagnostic_tab_creation_unconfirmed")
        owned_tabs.append(tab_id)
        status(folder,"tab-opened",18,open_mode="dedicated-worker-tab",
               tab_created=True)
        if not allow_reload:
            try:
                endpoint=ws_endpoint(str(created.get("webSocketDebuggerUrl") or ""),
                                     args.cdp_port)
                async with websockets.connect(endpoint,open_timeout=5,
                        max_size=24*1024*1024) as conn:
                    cdp=CDP(conn)
                    await cdp.call("Page.enable",timeout=8)
                    instrumented=False
                    try:
                        await cdp.call("Page.addScriptToEvaluateOnNewDocument",
                            {"source":NEW_DOCUMENT_ERRORS},timeout=8)
                        instrumented=True
                    except Exception:
                        pass
                    await cdp.call("Page.navigate",{"url":url},timeout=15)
                    last_probe={**last_probe,
                        "fresh_pre_navigation_hook_installed":instrumented,
                        "fresh_navigation_requested":True}
            except Exception:
                last_probe={**last_probe,
                    "navigation_route":"navigation-error",
                    "fresh_navigation_requested":False,
                    "capture_error":"fresh-navigation-cdp-error"}
                status(folder,"page-unavailable",44,page_probe=last_probe)
                raise RuntimeError("history_instrumented_navigation_failed")
        page=None
        access_observed=0
        reload_attempted=False
        reload_result="not-attempted"
        post_reload_at=-1
        opened_monotonic=time.monotonic()
        first_stable_monotonic=None
        stable_signature=None
        stable_samples=0
        observed_content_changes=0
        screenshot_preflight_root_seen=False
        history_readiness={
            "stable":False,"elapsed_sec":0,"stable_sec":0,
            "stable_samples":0,"content_changes":0,
            "network_quiet":False,"root_seen":False,
            "max_wait_sec":HISTORY_LOAD_MAX_SECONDS}
        suspicious_redirect_streak=0
        transient_redirect_count=0
        existing_evidence={}
        missing_polls=0;tab_retried=False
        unassigned_signature=None;unassigned_since=None
        for i in range(HISTORY_LOAD_MAX_SECONDS//HISTORY_LOAD_POLL_SECONDS):
            await asyncio.sleep(HISTORY_LOAD_POLL_SECONDS if i else 0.4)
            # Do not discard a redirected tab just because it moved to
            # about:blank, an error route, or an authentication endpoint.
            all_tabs=json_get(args.cdp_port,"/json/list")
            page=next((t for t in all_tabs if t.get("id")==tab_id
                       and t.get("type")=="page"),None)
            if not page:
                missing_polls+=1
                last_probe={"navigation_route":"target-not-visible",
                    "capture_error":"target-not-visible","attempt":i+1}
                if missing_polls>=5:
                    if not tab_retried:
                        tab_retried=True
                        try:
                            with urlopen(Request(f"http://127.0.0.1:{args.cdp_port}/json/new?"+
                                    quote(url,safe=""),method="PUT"),timeout=8) as response:
                                replacement=json.load(response)
                            new_id=str(replacement.get("id") or "")
                            if re.fullmatch(r"[a-fA-F0-9]{8,64}",new_id):
                                owned_tabs.append(new_id);tab_id=new_id
                                missing_polls=0
                                continue
                        except Exception:pass
                    break
            else:
                missing_polls=0
                actual=urlsplit(str(page.get("url") or ""))
                if actual.scheme=="https" and actual.hostname in HOSTS:
                    kind=("conversation" if CONVERSATION.fullmatch(actual.path)
                          else "other-chatgpt-route")
                    # A redirect to a different /c/ URL is not the
                    # operator's explicitly requested conversation.
                    if (kind=="conversation" and
                        (actual.hostname,actual.path)!=
                        (urlsplit(url).hostname,urlsplit(url).path)):
                        kind="unexpected-redirect"
                elif (actual.scheme=="https" and actual.hostname in
                      {"auth.openai.com","auth.chatgpt.com","login.openai.com"}):
                    kind="access-gate"
                elif actual.scheme in ("about","chrome","chrome-extension") or not actual.scheme:
                    kind="navigation-pending"
                elif actual.scheme=="chrome-error":
                    kind="navigation-error"
                else:
                    kind="unexpected-redirect"
                # During Page.reload the Chrome target can briefly report
                # chrome://, blank, or a provisional URL. A single snapshot
                # of /json/list is not proof that the tab left ChatGPT.
                # Continue observing only THIS newly created tab id, and
                # never evaluate arbitrary off-origin document content.
                if kind=="unexpected-redirect":
                    suspicious_redirect_streak+=1
                    if (reload_attempted and post_reload_at>=0
                        and i-post_reload_at<=15):
                        transient_redirect_count+=1
                        kind="navigation-pending"
                elif kind=="conversation":
                    suspicious_redirect_streak=0
                if kind=="access-gate":
                    access_observed+=1
                    last_probe={"navigation_route":kind,"route":"access-gate",
                                "capture_error":"none","attempt":i+1}
                elif kind in ("navigation-pending","navigation-error",
                              "unexpected-redirect"):
                    last_probe={"navigation_route":kind,"capture_error":"none",
                                "attempt":i+1}
                elif (page.get("webSocketDebuggerUrl")
                      and kind in ("conversation","other-chatgpt-route")):
                    endpoint=ws_endpoint(page["webSocketDebuggerUrl"],args.cdp_port)
                    try:
                        async with websockets.connect(endpoint,open_timeout=5,
                                max_size=24*1024*1024) as conn:
                            cdp=CDP(conn)
                            await cdp.call("Runtime.enable")
                            obj,probe=await inspect_page(cdp,args.request_id)
                            # Avoid false "still loading" when the app's
                            # a verified assistant reply always wins.
                            last_probe={**last_probe,**probe,
                                        "navigation_route":kind,
                                        "attempt":i+1,
                                        "reload_attempted":reload_attempted,
                                        "reload_result":reload_result}
                            # First sight of content is not readiness. Long
                            # late tables/images/chapters after readyState.
                            now=time.monotonic()
                            elapsed=now-opened_monotonic
                            signature=history_content_signature(obj)
                            snapshot_ready=bool(signature and
                                probe.get("ready_state")=="complete" and
                                probe.get("route")=="conversation" and
                                not probe.get("password_fields") and
                                not probe.get("challenge_frames"))
                            # A recent resource completion may be the final
                            # conversation payload; allow the browser time to
                            resource_age=probe.get("last_finished_age_ms")
                            network_quiet=(type(resource_age) is int and
                                (resource_age>=2000 or resource_age==0
                                    and not probe.get("fetch_total")))
                            if (not obj and network_quiet and elapsed>=45
                                and probe.get("ready_state")=="complete"
                                and int(probe.get("main_visible_text_chars") or 0)>=250
                                and not probe.get("conversation_api_recent_429")):
                                sig=tuple(probe.get(k) for k in (
                                    "main_visible_text_chars","main_longest_child_paragraphs",
                                    "main_visible_tables","main_visible_images"))
                                if sig!=unassigned_signature:
                                    unassigned_signature=sig;unassigned_since=now
                                elif elapsed>=75 and now-unassigned_since>=20:
                                    last_probe["history_content_visible_unassigned"]=True
                                    break
                            else:
                                unassigned_signature=None;unassigned_since=None
                            if snapshot_ready and network_quiet:
                                if signature!=stable_signature:
                                    if stable_signature is not None:
                                        observed_content_changes+=1
                                    stable_signature=signature
                                    first_stable_monotonic=now
                                    stable_samples=1
                                else:
                                    stable_samples+=1
                            else:
                                stable_signature=None
                                first_stable_monotonic=None
                                stable_samples=0
                            stable_seconds=(now-first_stable_monotonic
                                if first_stable_monotonic is not None else 0)
                            screenshot_preflight_root_seen=(
                                screenshot_preflight_root_seen or
                                bool(obj and obj.get("root",{}).get("children")))
                            history_readiness={
                                "stable":False,
                                "elapsed_sec":round(elapsed,1),
                                "stable_sec":round(stable_seconds,1),
                                "stable_samples":stable_samples,
                                "content_changes":observed_content_changes,
                                "network_quiet":network_quiet,
                                "root_seen":screenshot_preflight_root_seen,
                                "max_wait_sec":HISTORY_LOAD_MAX_SECONDS}
                            last_probe={**last_probe,
                                "history_load_elapsed_sec":int(elapsed),
                                "history_load_stable_sec":int(stable_seconds),
                                "history_load_stable_samples":stable_samples,
                                "history_load_content_changes":observed_content_changes,
                                "history_load_network_quiet":network_quiet,
                                "history_load_root_seen":screenshot_preflight_root_seen,
                                "history_load_ready":False}
                            if (snapshot_ready and network_quiet
                                and elapsed>=HISTORY_LOAD_MIN_SECONDS
                                and stable_samples>=HISTORY_LOAD_MIN_SAMPLES
                                and stable_seconds>=HISTORY_LOAD_STABLE_SECONDS):
                                history_readiness["stable"]=True
                                last_probe["history_load_ready"]=True
                                status(folder,"history-content-stable",40,
                                       page_probe=last_probe,
                                       attempt=i+1)
                                return page,obj,tab_id
                            # 429 may be caused by historical page traffic or

                            if probe.get("fetch_http_recent_429") or probe.get("conversation_api_recent_429"):
                                last_probe["rate_limit_detected"]=True
                                break
                            # Existing tabs were already checked before this
                            # new tab was opened. Never switch mid-hydration
                            # to a different, possibly partial history tab.
                            # only reload OUR diagnostic tab when its own
                            # page is complete, visible, skeleton-only and
                            # has no assistant/user turns after ~25 seconds.
                            # This never navigates another Worker/API tab,
                            # enters credentials or sends model prompts.
                            if (allow_reload and
                                elapsed>=45 and not reload_attempted
                                and kind=="conversation"
                                and probe.get("ready_state")=="complete"
                                and probe.get("visibility_state")=="visible"
                                and probe.get("loading_skeletons")
                                and not screenshot_preflight_root_seen
                                and int(probe.get("body_visible_tables") or 0)==0
                                and int(probe.get("main_longest_child_paragraphs") or 0)<5
                                and not any(probe.get(k) for k in (
                                    "assistant_roles","assistant_testids",
                                    "user_roles","conversation_turns",
                                    "verified_roleless_headings"))
                                and not probe.get("password_fields")
                                and not probe.get("challenge_frames")
                                and not (probe.get("fetch_http_401_403")
                                    or probe.get("fetch_http_recent_429")
                                    or probe.get("fetch_http_5xx"))):
                                reload_attempted=True
                                post_reload_at=i
                                reload_result="attempted"
                                status(folder,"retrying-history-render",33,
                                       page_probe=last_probe,
                                       retry_count=1,attempt=i+1)
                                try:
                                    await cdp.call("Page.enable")

                                    try:
                                        await cdp.call("Page.addScriptToEvaluateOnNewDocument",
                                            {"source":NEW_DOCUMENT_ERRORS},timeout=5)
                                        instrumented=True
                                    except Exception:
                                        instrumented=False
                                    await cdp.call("Page.reload",{"ignoreCache":False},
                                                   timeout=10)
                                    reload_result=("issued-instrumented"
                                        if instrumented else "issued-uninstrumented")
                                except Exception:
                                    reload_result="cdp-reload-failed"
                                last_probe={**last_probe,
                                    "reload_attempted":True,
                                    "reload_result":reload_result}
                    except Exception:
                        last_probe={"navigation_route":kind,
                            "capture_error":"cdp-tab-connection-error",
                            "attempt":i+1}
                else:
                    last_probe={"navigation_route":kind,
                        "capture_error":"cdp-endpoint-unavailable","attempt":i+1}
            last_probe={**last_probe,**existing_evidence,
                "reload_attempted":reload_attempted,
                "reload_result":reload_result,
                "transient_redirect_count":transient_redirect_count,
                "suspicious_redirect_streak":suspicious_redirect_streak,
                "diagnostic_tab_reopened":tab_retried}
            if i==0 or (i+1)%3==0 or i==HISTORY_LOAD_MAX_SECONDS//HISTORY_LOAD_POLL_SECONDS-1:
                status(folder,"waiting-history-stable",
                       min(39,19+int(20*(i+1)/
                           (HISTORY_LOAD_MAX_SECONDS//HISTORY_LOAD_POLL_SECONDS))),
                       page_probe=last_probe,tab_created=True,
                       retry_count=int(reload_attempted),attempt=i+1)
            # Access gates are evidence of a redirect; do not repeatedly
            # probe the login page or attempt any sign-in action.
            if access_observed>=3:break
            # Do not fail on a one-poll transient after the diagnostic tab

            # stop after the bounded grace and repeated confirmations.
            if (last_probe.get("navigation_route")=="unexpected-redirect"
                and suspicious_redirect_streak>=4
                and (not reload_attempted or
                     i-post_reload_at>15)):break
        history_readiness["stable"]=False
        history_readiness["timed_out"]=bool(
            time.monotonic()-opened_monotonic>=HISTORY_LOAD_MAX_SECONDS-4)
        last_probe={**last_probe,"history_load_ready":False,
            "history_load_timeout":bool(history_readiness["timed_out"]),
            "history_load_root_seen":screenshot_preflight_root_seen}
        return None,None,tab_id

    if not picked and target_url:

        # than repeatedly creating/reloading more copies of the same page.

        existing,found,evidence=await read_existing_history_tab(target_url,"")
        observation_rounds=1
        # Poll only existing DOM state. Never reload or request conversation
        # again. An old Resource Timing 429 does not abort this observation.
        if not found and evidence["existing_tab_candidates"]:
            # Existing visible history tabs may still be hydrating. Stay
            # read-only and require the same 12s stable structural evidence
            # a bounded 60s observation precedes the guarded recovery path.
            for _ in range(HISTORY_LOAD_MAX_SECONDS//3-1):
                if evidence.get("existing_tab_recent_429"):
                    break
                if (observation_rounds>=20 and
                    not evidence.get("existing_tab_content_pending")):
                    break
                await asyncio.sleep(3)
                existing,found,evidence=await read_existing_history_tab(
                    target_url,"")
                observation_rounds+=1
                if found or not evidence["existing_tab_candidates"]:
                    break
        evidence["existing_tab_observation_rounds"]=observation_rounds
        last_probe={**desktop_evidence,**last_probe,**evidence}
        if existing and found:
            picked,report=existing,found
            history_readiness={"stable":True,
                "mode":"existing-tab-readonly",
                "observation_rounds":observation_rounds,
                "minimum_stable_seconds":HISTORY_LOAD_STABLE_SECONDS}
        elif evidence["existing_tab_candidates"]:
            # v1.2.9 could not progress: four long-stalled identical shell
            # tabs prevented a fresh history navigation forever. Recover only
            # when ALL inspected tabs are empty, structurally identical,
            # and no recent 429 or access gate was observed.
            shell_only=(
                evidence["existing_tab_inspected"]>0
                and evidence["existing_tab_inspected"]==evidence["existing_tab_candidates"]
                and evidence["existing_tab_busy"]==evidence["existing_tab_candidates"]
                and evidence["existing_tab_distinct_structures"]==1
                and evidence["existing_tab_max_articles"]==0
                and evidence["existing_tab_max_message_like"]==0
                and evidence["existing_tab_max_markdown_blocks"]==0
                and evidence["existing_tab_visible_content_candidates"]==0
                and evidence["existing_tab_recent_429"]==0
                and evidence["existing_tab_content_pending"]==0
                and not last_probe.get("password_fields")
                and not last_probe.get("challenge_frames")
                and not last_probe.get("fetch_http_401_403")
                and not last_probe.get("fetch_http_5xx"))
            last_probe={**last_probe,**evidence,
                "navigation_route":"conversation",
                "recovery_shell_only":shell_only,
                "recovery_fresh_attempted":False}
            if shell_only:
                granted,remaining,gate_reason=fresh_navigation_gate(
                    folder,args.worker_id,args.cdp_port,target_url)
                last_probe={**last_probe,
                    "recovery_cooldown_remaining_sec":remaining,
                    "recovery_gate":gate_reason}
                if granted:
                    last_probe["recovery_fresh_attempted"]=True
                    status(folder,"recovering-stalled-history",28,
                           page_probe=last_probe)
                    # A single newly owned tab, never reloading any old tab.
                    # Disable automatic reload on this recovery path.
                    recovered,recovered_reply,_=await open_history_page(
                        target_url,allow_reload=False)
                    last_probe={**last_probe,**evidence,
                        "recovery_shell_only":True,
                        "recovery_fresh_attempted":True,
                        "recovery_gate":"granted",
                        "recovery_cooldown_remaining_sec":0}
                    if recovered and recovered_reply:
                        picked,report=recovered,recovered_reply
            if not picked:
                if evidence.get("existing_tab_content_pending"):
                    last_probe["history_existing_content_not_stable"]=True
                status(folder,"page-unavailable",44,page_probe=last_probe)
                raise RuntimeError(failure_reason(last_probe))
        else:
            status(folder,"opening-history",18)
            # Use pre-navigation error hooks and avoid mid-hydration reload
            # for a newly opened, potentially very long history page.
            picked,report,_=await open_history_page(target_url,allow_reload=False)
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
        if last_probe:
            status(folder,"page-unavailable",44,page_probe=last_probe)
        raise RuntimeError(failure_reason(last_probe))
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
            boundary_verified=report.get("boundary_verified") is not False
            dom_filename=("assistant_dom.json" if boundary_verified
                else "unverified_rich_candidate_dom.json")
            # DOM is written AFTER candidate screenshot capture so a second
            # verified in-page snapshot can include images loaded meanwhile.
            if not boundary_verified:

                # stub, never mislabel the candidate as an assistant reply.
                archive.writestr("assistant_dom.json",json.dumps({
                    "schema":"native-page-boundary-unverified-v1",
                    "ok":False,"boundary_verified":False,
                    "artifact_kind":"unverified-rich-candidate",
                    "candidate_file":dom_filename},ensure_ascii=False))
            rect=report["rect"];screens=0
            screenshot_coverage_px=0
            screenshot_image_evidence={}
            screenshot_image_conflicts=0
            screenshot_strips=[]
            screenshot_complete=False
            screenshot_skip_reason="not-attempted"
            # negative viewport Y. This mistakenly captured the USER PROMPT
            # and then blank frames. Never reuse that unsafe clip.
            if not boundary_verified:
                screenshot_skip_reason="unverified-candidate-not-owned-tab"
                if (tab_origin=="dedicated-worker-tab"
                    and str(picked.get("id") or "") in owned_tabs
                    and str(report.get("scope") or "")=="inferred-rich-table-candidate"):
                    screenshot_skip_reason="candidate-viewport-not-verified"
                    while (screenshot_coverage_px<
                               min(int(rect["h"]),MAX_SCREENSHOTS*640)
                           and screens<MAX_SCREENSHOTS):
                        off=screenshot_coverage_px
                        try:
                            evaluate=await cdp.call("Runtime.evaluate",{
                                "expression":SCREENSHOT_PREP+"("+
                                    json.dumps(args.request_id)+","+
                                    str(off)+",640)",
                                "awaitPromise":True,
                                "returnByValue":True,"silent":True},timeout=15)
                            if evaluate.get("exceptionDetails"):break
                            geometry=(evaluate.get("result") or {}).get("value") or {}
                            if not geometry.get("ok"):
                                screenshot_skip_reason=str(geometry.get("reason") or
                                    "candidate-viewport-not-verified")[:60]
                                break
                            clip=geometry.get("clip") or {}
                            if (not all(isinstance(clip.get(k),int) for k in (
                                    "x","y","width","height"))
                                or clip["x"]<0 or clip["y"]<0
                                or clip["width"]<100 or clip["height"]<1
                                or geometry.get("offset")!=off):
                                break
                            result=await cdp.call("Page.captureScreenshot",{
                                "format":"png","fromSurface":True,
                                "captureBeyondViewport":False,"clip":clip},timeout=20)
                            encoded=base64.b64decode(result["data"],validate=True)
                            if (not encoded.startswith(b"\x89PNG\r\n\x1a\n")
                                or len(encoded)<24):break
                            width,height=struct.unpack(">II",encoded[16:24])
                            if (width!=clip["width"] or
                                height!=clip["height"]):break
                            filename=f"screenshots/unverified_candidate_{screens:02d}.png"
                            archive.writestr(filename,encoded)
                            screenshot_strips.append((filename,off,encoded,width,height))
                            screens+=1
                            screenshot_coverage_px+=height
                            for item in (geometry.get("observed_images") or [])[:40]:
                                if not isinstance(item,dict):
                                    continue
                                idx=item.get("index")
                                if (type(idx) is not int or not 0<=idx<40
                                    or idx>=int(report.get("counts",{}).get("images") or 0)):
                                    continue
                                fact={"image_index":idx,
                                      "source_url_sha256":item.get("source_url_sha256"),
                                      "natural_width":item.get("natural_width"),
                                      "natural_height":item.get("natural_height")}
                                if (not isinstance(fact["source_url_sha256"],str)
                                    or not re.fullmatch("[0-9a-f]{64}",
                                        fact["source_url_sha256"])
                                    or any(type(fact[k]) is not int or
                                           not 0<fact[k]<=20000 for k in
                                           ("natural_width","natural_height"))):
                                    continue
                                old=screenshot_image_evidence.get(idx)
                                if old and old!=fact:
                                    screenshot_image_conflicts+=1
                                elif old is None:
                                    screenshot_image_evidence[idx]=fact
                            expected_shots=max(1,
                                (min(int(rect["h"]),MAX_SCREENSHOTS*640)+639)//640)
                            status(folder,"capturing-screenshots",
                                45+int(40*screens/expected_shots),
                                screenshots=screens)
                            if path.stat().st_size>MAX_ZIP:
                                raise RuntimeError("evidence_zip_size_limit")
                        except Exception:
                            screenshot_skip_reason="candidate-screenshot-cdp-error"
                            break
                    screenshot_complete=(screenshot_coverage_px>=int(rect["h"]))
                    if screenshot_complete:screenshot_skip_reason="none"
            else:
                # Existing-tab DOM reads MUST remain read-only; do not scroll

                screenshot_skip_reason="verified-screenshot-needs-safe-viewport"

            # A screenshot can show images that were still unloaded during

            # and accept it only if the same candidate, text and structural
            # counts are unchanged. This does NOT identify the media asset.
            loaded_images_before=image_loaded_count(report)
            post_render_snapshot_accepted=False
            if (not boundary_verified and screens>0
                and tab_origin=="dedicated-worker-tab"
                and str(picked.get("id") or "") in owned_tabs):
                try:
                    refreshed,_=await inspect_page(cdp,args.request_id)
                    matching=(
                        isinstance(refreshed,dict) and refreshed.get("ok")
                        and refreshed.get("scope")==report.get("scope")
                        and refreshed.get("boundary_verified") is False
                        and native_text_digest(refreshed)==native_text_digest(report)
                        and all(refreshed.get("counts",{}).get(k)==
                                report.get("counts",{}).get(k)
                                for k in ("nodes","tables","headings","text_chars",
                                          "images","max_children","grids","flex"))
                        and all(abs(int(refreshed.get("rect",{}).get(k) or 0)-
                                    int(report.get("rect",{}).get(k) or 0))<=2
                                for k in ("w","h"))
                    )
                    if matching:
                        report=refreshed
                        post_render_snapshot_accepted=True
                except Exception:
                    pass
            loaded_images_after=image_loaded_count(report)
            capture_layout_stable=(boundary_verified or
                (screens>0 and post_render_snapshot_accepted))
            if not capture_layout_stable and screens>0:
                screenshot_complete=False
                screenshot_skip_reason="dom-changed-during-capture"
            # P2: create a stable within-run mapping from DOM image index to
            # the *rendered image pixels* and SHA-256 of the full source URL.
            # Never export the URL itself or claim to have original bytes.
            rendered_image_evidence=[]
            image_source_probe_ok=False
            image_source_consistent=False
            if (not boundary_verified and
                tab_origin=="dedicated-worker-tab" and
                str(picked.get("id") or "") in owned_tabs and
                0<int(report.get("counts",{}).get("images") or 0)<=40):
                try:
                    checked=await cdp.call("Runtime.evaluate",{
                        "expression":MEDIA_SOURCE_PROBE+"("+
                            json.dumps(args.request_id)+")",
                        "awaitPromise":True,"returnByValue":True,"silent":True},
                        timeout=15)
                    images=(checked.get("result") or {}).get("value") or {}
                    source_rows=images.get("images") or []
                    image_source_probe_ok=bool(
                        images.get("ok") and isinstance(source_rows,list)
                        and len(source_rows)==int(report["counts"]["images"])
                        and [
                            (row.get("index"),row.get("natural_width"),
                             row.get("natural_height"))
                            for row in source_rows if isinstance(row,dict)
                        ]==image_dimensions_sequence(report)
                        and all(isinstance(row,dict)
                            and row.get("index")==idx
                            and isinstance(row.get("natural_width"),int)
                            and isinstance(row.get("natural_height"),int)
                            and 0<row["natural_width"]<=20000
                            and 0<row["natural_height"]<=20000
                            and re.fullmatch(r"[a-f0-9]{64}",
                                str(row.get("source_url_sha256") or ""))
                            for idx,row in enumerate(source_rows)))
                    if image_source_probe_ok:
                        for row in source_rows:
                            idx=row["index"]
                            geometry=await cdp.call("Runtime.evaluate",{
                                "expression":MEDIA_CROP_PREP+"("+
                                    json.dumps(args.request_id)+","+str(idx)+")",
                                "returnByValue":True,"silent":True},timeout=10)
                            clipdata=(geometry.get("result") or {}).get("value") or {}
                            clip=clipdata.get("clip") or {}
                            if (not clipdata.get("ok") or
                                clipdata.get("index")!=idx or
                                clipdata.get("natural_width")!=row["natural_width"] or
                                clipdata.get("natural_height")!=row["natural_height"] or
                                not all(type(clip.get(k)) is int for k in
                                    ("x","y","width","height")) or
                                clip["width"]<64 or clip["height"]<64 or
                                clip["x"]<0 or clip["y"]<0):
                                break
                            png=await cdp.call("Page.captureScreenshot",{
                                "format":"png","fromSurface":True,
                                "captureBeyondViewport":False,"clip":clip},timeout=20)
                            raw=base64.b64decode(png["data"],validate=True)
                            if (len(raw)<24 or len(raw)>4*1024*1024
                                or not raw.startswith(b"\x89PNG\r\n\x1a\n")):
                                break
                            w,h=struct.unpack(">II",raw[16:24])
                            if w!=clip["width"] or h!=clip["height"]:break
                            fname=f"media/rendered_image_{idx:02d}.png"
                            archive.writestr(fname,raw)
                            rendered_image_evidence.append({
                                "image_index":idx,
                                "source_url_sha256":row["source_url_sha256"],
                                "natural_width":row["natural_width"],
                                "natural_height":row["natural_height"],
                                "rendered_width":w,"rendered_height":h,
                                "rendered_png_sha256":hashlib.sha256(raw).hexdigest(),
                                "rendered_file":fname,
                                "original_bytes_captured":False})
                            if path.stat().st_size>MAX_ZIP:
                                raise RuntimeError("evidence_zip_size_limit")
                    if image_source_probe_ok:
                        after=await cdp.call("Runtime.evaluate",{
                            "expression":MEDIA_SOURCE_PROBE+"("+
                                json.dumps(args.request_id)+")",
                            "awaitPromise":True,"returnByValue":True,
                            "silent":True},timeout=15)
                        rows_after=((after.get("result") or {}).get("value")
                            or {}).get("images") or []
                        image_source_consistent=bool(
                            isinstance(rows_after,list)
                            and len(rows_after)==len(source_rows)
                            and all(
                                isinstance(a,dict) and
                                a.get("index")==b.get("index") and
                                a.get("source_url_sha256")==b.get("source_url_sha256") and
                                a.get("natural_width")==b.get("natural_width") and
                                a.get("natural_height")==b.get("natural_height")
                                for a,b in zip(rows_after,source_rows)))
                except Exception:
                    image_source_probe_ok=False
            report["rendered_image_evidence"]=rendered_image_evidence
            report["rendered_image_source_consistent"]=image_source_consistent
            report["screenshot_image_evidence"]=[
                screenshot_image_evidence[idx]
                for idx in sorted(screenshot_image_evidence)]
            report["screenshot_image_conflicts"]=screenshot_image_conflicts
            strip_mappings=screenshot_image_mapping(report,screenshot_strips,
                report["screenshot_image_evidence"],archive) if (
                    not boundary_verified and screenshot_image_conflicts==0) else []
            report["screenshot_image_mapping"]=strip_mappings
            archive.writestr(dom_filename,json.dumps(report,ensure_ascii=False))
            remaining_px=max(0,int(rect["h"])-screenshot_coverage_px)
            screenshot_near_complete=(screens>0 and remaining_px<=2
                and screenshot_coverage_px>0)
            if (screenshot_near_complete and not screenshot_complete
                and screenshot_skip_reason!="dom-changed-during-capture"):
                screenshot_skip_reason="rounding-tail-1-to-2-px"


            status(folder,"validating-delivery",88,tool_version=VERSION)
            extra_buffer=BytesIO()
            try:
                with ZipFile(extra_buffer,"w",ZIP_DEFLATED,compresslevel=6) as extra:
                    validation=await asyncio.wait_for(delivery_validation_live(cdp,args,report,extra,
                        owned_tabs,str(picked.get("id") or "") in owned_tabs),timeout=110)
            except Exception as exc:
                validation={"schema":"native-delivery-validation-v1","tool_version":VERSION,
                    "status":"incomplete","reason":"validation-"+type(exc).__name__,
                    "checks":{"candidate_capture":"not-run","visual":"not-run"},
                    "model_request_sent":False,"production_redeployed":False}
                # Discard a partial extra archive; normal evidence survives.
                extra_buffer=BytesIO()
                with ZipFile(extra_buffer,"w",ZIP_DEFLATED,compresslevel=6) as extra:
                    validation_write_result(extra,validation,[])
            validation=validation_merge(archive,extra_buffer.getvalue(),validation)
            archive.writestr("manifest.json",json.dumps({"tool":"native-page-diagnostic",
                "version":VERSION,"request_id":args.request_id,"worker_id":args.worker_id,
                "scope":report["scope"],"counts":report["counts"],
                "boundary_verified":boundary_verified,
                "completeness_verified":boundary_verified and not bool(report.get("reason")),
                "artifact_kind":("verified-assistant" if boundary_verified
                    else "unverified-rich-candidate"),"dom_file":dom_filename,
                "screenshot_count":screens,
                "screenshot_coverage_px":screenshot_coverage_px,
                "screenshot_capture_complete":screenshot_complete,
                "screenshot_near_complete":screenshot_near_complete,
                "screenshot_uncovered_px":remaining_px,
                "screenshot_skip_reason":screenshot_skip_reason,
                "screenshot_max_count":MAX_SCREENSHOTS,
                "screenshot_image_sources_observed":len(screenshot_image_evidence),
                "screenshot_image_source_conflicts":screenshot_image_conflicts,
                "screenshot_image_mapped_count":len(strip_mappings),
                "screenshot_image_crop_count":sum(
                    bool(item.get("rendered_png_file")) for item in strip_mappings),
                "screenshot_image_mapping_complete":(
                    screenshot_image_conflicts==0 and
                    len(strip_mappings)==int(report.get("counts",{}).get("images") or 0)
                    and len(strip_mappings)>0),
                "screenshot_image_sources_complete":(
                    screenshot_image_conflicts==0 and
                    len(screenshot_image_evidence)==
                    int(report.get("counts",{}).get("images") or 0)),
                "loaded_images_before":loaded_images_before,
                "loaded_images_after":loaded_images_after,
                "image_source_probe_ok":image_source_probe_ok,
                "image_source_consistent":image_source_consistent,
                "rendered_image_crop_count":len(rendered_image_evidence),
                "rendered_image_crops_complete":(
                    image_source_consistent and
                    len(rendered_image_evidence)==
                    int(report.get("counts",{}).get("images") or 0) and
                    len(rendered_image_evidence)>0),
                "post_render_snapshot_accepted":post_render_snapshot_accepted,
                "capture_layout_stable":capture_layout_stable,
                "history_readiness":history_readiness,
                "truncated":bool(report.get("reason")),
                "delivery_validation_status":validation.get("status"),
                "delivery_validation_report":"validation/report.json",
                "delivery_validation_view":"index.html",
                "source":"existing-chatgpt-page","conversation_url_included":False,
                "request_binding":verification,
                 "capture_origin":tab_origin},ensure_ascii=False))
            if (folder/"diagnostic.log").is_file():
                archive.write(folder/"diagnostic.log","diagnostic.log")
    if path.stat().st_size>MAX_ZIP:raise RuntimeError("evidence_zip_size_limit")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    status(folder,"complete",100,size=path.stat().st_size,sha256=digest,
           screenshots=screens,counts=report["counts"],tool_version=VERSION,
           boundary_verified=report.get("boundary_verified") is not False,
           screenshot_capture_complete=screenshot_complete,
           screenshot_coverage_px=screenshot_coverage_px,
           screenshot_uncovered_px=remaining_px,
           loaded_images_after=loaded_images_after,
           history_readiness=history_readiness,
           rendered_image_crop_count=len(rendered_image_evidence),
           artifact_kind=("verified-assistant" if report.get("boundary_verified") is not False
               else "unverified-rich-candidate"))
    data=json.loads((folder/"status.json").read_text());data["status"]="completed"
    (folder/"status.json").write_text(json.dumps(data))
    with (folder/"diagnostic.log").open("a",encoding="utf8") as stream:
        stream.write(json.dumps({"tool_version":VERSION,"status":"completed",
            "stage":"complete","percent":100,"updated_at":int(time.time())})+"\n")


# execute. Fixture ZIPs supply inert data; their bundled Python/JS is NEVER run.
VALIDATION_REF = "native-delivery-validation-candidate"
VALIDATION_REPO = "b8vipvip/chat2api"
VALIDATION_FILES = (
    "chrome_extension/content_native_dom_evidence_v1.js",
    "chrome_extension/content_native_content_v1.js",
    "chrome_extension/content_native_file_v210.js",
    "chrome_extension/native_file_main_v210.js",
    "app/native_content_v1.py", "app/request_history_v94_patch.py",
)
VALIDATION_CSS = "display position top right bottom left boxSizing width height minWidth maxWidth minHeight maxHeight margin marginTop marginRight marginBottom marginLeft padding paddingTop paddingRight paddingBottom paddingLeft fontFamily fontSize fontWeight fontStyle lineHeight letterSpacing wordSpacing textAlign textDecorationLine textDecorationColor textTransform whiteSpace wordBreak overflowWrap verticalAlign color backgroundColor borderColor borderWidth borderTopWidth borderRightWidth borderBottomWidth borderLeftWidth borderTopStyle borderRightStyle borderBottomStyle borderLeftStyle borderTopColor borderRightColor borderBottomColor borderLeftColor borderRadius borderCollapse borderSpacing tableLayout listStyleType listStylePosition flexDirection flexWrap flexGrow flexShrink flexBasis justifyContent alignItems alignContent alignSelf gap rowGap columnGap gridTemplateColumns gridTemplateRows gridColumn gridRow objectFit objectPosition overflowX overflowY opacity visibility transform transformOrigin".split()
VALIDATION_TAGS = set("div section article p span strong em b i h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br a sup sub button svg g path rect circle ellipse line polyline polygon text tspan defs clippath math mrow mi mn mo msup msub mfrac msqrt semantics annotation".split())

FREEZE_REFERENCE = r"""(function(cssKeys){
const roots=globalThis.__CHAT2API_VALIDATION_ROOTS__||[];
if(!roots.length||roots.some(e=>!e.isConnected))throw Error('reference-root-missing');
const blocked='script,style,iframe,form,input,textarea,select,nav,aside,header,footer,[contenteditable="true"],[data-message-author-role="user"],[data-testid="user-message"]';
const allowed=new Set('div section article p span strong em b i h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br a sup sub button svg g path rect circle ellipse line polyline polygon text tspan defs clippath math mrow mi mn mo msup msub mfrac msqrt semantics annotation'.split(' '));
const visible=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&(r.width>0&&r.height>0||e.namespaceURI.includes('svg'));};
let count=0,chars=0;const styles=[],styleIds=new Map(),images=[],unsupported=[];
const styleOf=e=>{const s=getComputedStyle(e),out={};for(const key of cssKeys){const v=String(s[key]||'');if(v&&v.length<300&&!/url\s*\(|var\s*\(|[<>;{}]/i.test(v))out[key]=v;}const sig=JSON.stringify(out);if(!styleIds.has(sig)){styleIds.set(sig,styles.length);styles.push(out);}return styleIds.get(sig);};
function node(e,depth){
 if(depth>64||++count>30000)throw Error('reference-tree-limit');
 if(e.nodeType===3){const text=String(e.nodeValue||'');if(!text.trim())return null;if((chars+=text.length)>300000)throw Error('reference-text-limit');return {tag:'#text',text};}
 if(e.nodeType!==1||e.matches(blocked)||!visible(e))return null;
 let tag=e.tagName.toLowerCase();if(tag==='canvas'){unsupported.push('canvas');return null;}
 if(tag==='button'&&!e.querySelector('img'))return null;
 if(!allowed.has(tag))tag='div';
 const out={tag,style:styleOf(e)},attrs={};
 for(const key of ['colspan','rowspan','start','viewBox','d','fill','stroke','stroke-width','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','width','height','points']){const v=e.getAttribute(key);if(v&&v.length<20000&&!/url\s*\(|[<>]/i.test(v))attrs[key]=v;}
 if(Object.keys(attrs).length)out.attrs=attrs;
 if(e.namespaceURI.includes('svg'))out.ns='svg';if(e.namespaceURI.includes('MathML'))out.ns='math';
 if(tag==='img'){
  const r=e.getBoundingClientRect();if(r.width<40||r.height<40)return null;
  if(images.length>=40)throw Error('reference-image-limit');
  out.image=images.length;out.natural_width=e.naturalWidth;out.natural_height=e.naturalHeight;images.push(e);
 }else{out.children=[...e.childNodes].map(c=>node(c,depth+1)).filter(Boolean);}
 return out;
}
const children=roots.map(e=>node(e,0)).filter(Boolean),rects=roots.map(e=>e.getBoundingClientRect());
const left=Math.min(...rects.map(r=>r.left)),top=Math.min(...rects.map(r=>r.top));
const right=Math.max(...rects.map(r=>r.right)),bottom=Math.max(...rects.map(r=>r.bottom));
globalThis.__CHAT2API_VALIDATION_IMAGES__=images;
return {schema:'native-reply-fixture-v1',root:{tag:'div',children},styles,unsupported,
 counts:{nodes:count,text_chars:chars,images:images.length},
 rect:{w:Math.ceil(right)-Math.floor(left),h:Math.ceil(bottom)-Math.floor(top)},
 viewport:{width:innerWidth,height:innerHeight,device_scale:devicePixelRatio},
 font_family:getComputedStyle(roots[0]).fontFamily};
})"""

HYDRATE_REFERENCE = r"""(async function(fixture,assets){
const spaces={svg:'http://www.w3.org/2000/svg',math:'http://www.w3.org/1998/Math/MathML'};
function node(v){if(v.tag==='#text')return document.createTextNode(v.text);
 const e=v.ns?document.createElementNS(spaces[v.ns],v.tag==='clippath'?'clipPath':v.tag):document.createElement(v.tag);
 for(const [k,x] of Object.entries(fixture.styles[v.style]||{}))e.style[k]=x;
 for(const [k,x] of Object.entries(v.attrs||{}))e.setAttribute(k,x);
 if(v.tag==='img'){e.src=assets[v.image]||'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6V2sAAAAASUVORK5CYII=';
  if(!assets[v.image])e.style.background='#fdd';
  if(v.natural_width>0){Object.defineProperty(e,'naturalWidth',{get:()=>v.natural_width});Object.defineProperty(e,'naturalHeight',{get:()=>v.natural_height});}
 }else for(const c of v.children||[])e.appendChild(node(c));
 return e;
}
document.body.style.cssText='margin:0;background:white';document.body.style.fontFamily=fixture.font_family||'Arial,sans-serif';
const main=document.createElement('main'),scope=node(fixture.root);main.appendChild(scope);document.body.replaceChildren(main);
globalThis.__CHAT2API_VALIDATION_ROOTS__=[scope];
globalThis.__CHAT2API_VALIDATION_IMAGES__=[...scope.querySelectorAll('img')];
await Promise.all([...scope.querySelectorAll('img')].map(e=>e.decode().catch(()=>{})));
return true;
})"""

REFERENCE_STRIP = r"""(async function(offset){
const roots=globalThis.__CHAT2API_VALIDATION_ROOTS__||[];
if(!roots.length||roots.some(e=>!e.isConnected))return {ok:false};
let top=Math.min(...roots.map(e=>e.getBoundingClientRect().top));
let scroller=null;for(let p=roots[0].parentElement;p;p=p.parentElement){const s=getComputedStyle(p);if(/auto|scroll/.test(s.overflowY)&&p.scrollHeight>p.clientHeight+8){scroller=p;break;}}
const delta=top+offset-96;if(scroller)scroller.scrollTo({top:scroller.scrollTop+delta,behavior:'instant'});else scrollTo({top:scrollY+delta,behavior:'instant'});
await new Promise(r=>setTimeout(r,80));
const rr=roots.map(e=>e.getBoundingClientRect()),x=Math.max(0,Math.floor(Math.min(...rr.map(r=>r.left)))),y=Math.max(0,Math.floor(Math.min(...rr.map(r=>r.top)))+offset);
const width=Math.min(3000,Math.ceil(Math.max(...rr.map(r=>r.right)))-x),height=Math.min(640,Math.ceil(Math.max(...rr.map(r=>r.bottom)))-y,innerHeight-y-1);
if(width<1||height<1||x+width>innerWidth||y<0)return {ok:false};
for(const fx of [.08,.5,.92])for(const fy of [.08,.5,.92]){const hit=document.elementFromPoint(x+width*fx,y+height*fy);if(!hit||!roots.some(e=>e===hit||e.contains(hit)))return {ok:false,reason:'viewport-occluded'};}
return {ok:true,clip:{x,y,width,height,scale:1},height,width,offset};
})"""

def validation_json_get(path, limit=1000000):
    # The URL cannot come from a fixture, UI, environment or filename.
    req=Request("https://api.github.com/repos/"+VALIDATION_REPO+path,
        headers={"Accept":"application/vnd.github+json","User-Agent":"chat2api-delivery-validation"})
    with urlopen(req,timeout=15) as response:
        if urlsplit(response.url).hostname!="api.github.com":raise ValueError("candidate_redirect_refused")
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError("candidate_source_size")
    return json.loads(data)

def validation_sources(folder, candidate_sha="", source_dir=""):
    if source_dir:
        source=Path(source_dir).resolve();texts={};rows=[]
        for name in VALIDATION_FILES:
            path=(source/name).resolve()
            if source not in path.parents:raise ValueError("candidate_path_escape")
            raw=path.read_bytes()
            if not 0<len(raw)<=512000:raise ValueError("candidate_source_size")
            texts[name]=raw.decode("utf8");rows.append({"path":name,"sha256":hashlib.sha256(raw).hexdigest()})
        # Local worktree bytes are explicit CLI input, never code from a ZIP.
        return texts,{"repository":VALIDATION_REPO,"commit":None,"source":"explicit-local-worktree",
            "files":rows,"tree_sha256":hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
    if candidate_sha and not re.fullmatch(r"[a-f0-9]{40}",candidate_sha):raise ValueError("candidate_sha_invalid")
    sha=candidate_sha or str(validation_json_get("/branches/"+VALIDATION_REF).get("commit",{}).get("sha") or "")
    if not re.fullmatch(r"[a-f0-9]{40}",sha):raise ValueError("candidate_sha_invalid")
    cache=Path(folder).parent/"validation_source_cache"/sha;cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    meta_path=cache/"source.json";saved={}
    if meta_path.is_file():
        try:saved=json.loads(meta_path.read_text())
        except (ValueError,OSError):pass
    texts={};rows=[]
    for name in VALIDATION_FILES:
        path=cache/name;raw=None
        info=next((x for x in saved.get("files",[]) if x.get("path")==name),{})
        if path.is_file():
            body=path.read_bytes()
            blob=hashlib.sha1(b"blob "+str(len(body)).encode()+b"\0"+body).hexdigest()
            if saved.get("commit")==sha and blob==info.get("git_blob_sha") and hashlib.sha256(body).hexdigest()==info.get("sha256"):raw=body
        if raw is None:
            obj=validation_json_get("/contents/"+name+"?ref="+sha)
            if obj.get("type")!="file" or obj.get("path")!=name or obj.get("encoding")!="base64":raise ValueError("candidate_source_envelope")
            raw=base64.b64decode(str(obj.get("content") or "").replace("\n",""),validate=True)
            blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
            if not 0<len(raw)<=512000 or obj.get("size")!=len(raw) or obj.get("sha")!=blob:raise ValueError("candidate_source_hash")
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(raw);path.chmod(0o600)
        rows.append({"path":name,"git_blob_sha":blob,"sha256":hashlib.sha256(raw).hexdigest()})
        texts[name]=raw.decode("utf8")
    info={"repository":VALIDATION_REPO,"branch":VALIDATION_REF,"commit":sha,"source":"github-fixed-repo-verified-blob","files":rows}
    meta_path.write_text(json.dumps(info));meta_path.chmod(0o600)
    return texts,info

async def validation_eval(cdp, code, context=None, timeout=35):
    params={"expression":code,"returnByValue":True,"awaitPromise":True,"silent":True}
    if context is not None:params["contextId"]=context
    result=await cdp.call("Runtime.evaluate",params,timeout=timeout)
    if result.get("exceptionDetails"):
        if ((result["exceptionDetails"].get("exception") or {}).get("className")
            =="SyntaxError"):raise SyntaxError("candidate_source_syntax_error")
        raise RuntimeError("candidate_javascript_failed")
    return (result.get("result") or {}).get("value")

async def validation_world(cdp):
    tree=await cdp.call("Page.getFrameTree")
    frame=tree["frameTree"]["frame"]["id"]
    world=await cdp.call("Page.createIsolatedWorld",{"frameId":frame,"worldName":"chat2api-delivery-validation-"+os.urandom(8).hex()})
    return frame,world["executionContextId"]

async def validation_blank(port,owned_tabs):
    import websockets
    req=Request(f"http://127.0.0.1:{port}/json/new?about:blank",method="PUT")
    with urlopen(req,timeout=5) as response:page=json.load(response)
    tab=str(page.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,100}",tab):raise ValueError("validation_tab_invalid")
    owned_tabs.append(tab)
    ws=await websockets.connect(ws_endpoint(page["webSocketDebuggerUrl"],port),open_timeout=5,max_size=24*1024*1024)
    cdp=CDP(ws);await cdp.call("Page.enable")
    return ws,cdp

def validation_raster(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):return "image/png","png"
    if raw.startswith(b"\xff\xd8\xff"):return "image/jpeg","jpg"
    if raw[:6] in (b"GIF87a",b"GIF89a"):return "image/gif","gif"
    if raw[:4]==b"RIFF" and raw[8:12]==b"WEBP":return "image/webp","webp"
    raise ValueError("original_image_signature")

async def validation_originals(cdp,frame,context,fixture,archive):
    urls=await validation_eval(cdp,"(globalThis.__CHAT2API_VALIDATION_IMAGES__||[]).map(e=>String(e.currentSrc||e.src||''))",context)
    assets=[];digests=[];total_bytes=0
    for index,rawurl in enumerate((urls or [])[:40]):
        item={"image_index":index,"original_bytes_captured":False}
        try:
            u=urlsplit(rawurl)
            allowed=(u.scheme=="https" and not u.username and not u.password and
                (u.hostname in HOSTS or re.search(r"\.(?:oaiusercontent\.com|oaistatic\.com|openai\.com|chatgpt\.com)$",u.hostname or "")))
            if u.scheme=="data" and rawurl.startswith("data:image/") and ";base64," in rawurl:
                raw=base64.b64decode(rawurl.split(";base64,",1)[1],validate=True)
            elif allowed:
                # Read the already-loaded image response from THIS page's

                response=await cdp.call("Page.getResourceContent",{"frameId":frame,"url":rawurl},timeout=8)
                if not response.get("base64Encoded"):raise ValueError("image_cache_not_binary")
                encoded=response.get("content") or ""
                if len(encoded)>5600000:raise ValueError("image_cache_limit")
                raw=base64.b64decode(encoded,validate=True)
            elif u.scheme=="blob" and u.path.startswith("https://chatgpt.com/"):
                # Browser-local Blob only. A remote fetch is not a fallback.
                encoded=await validation_eval(cdp,"(async()=>{const e=__CHAT2API_VALIDATION_IMAGES__["+str(index)+"];const r=await fetch(e.currentSrc||e.src);const b=await r.blob();if(b.size>4194304)throw Error();const a=new Uint8Array(await b.arrayBuffer());let s='';for(let i=0;i<a.length;i+=16384)s+=String.fromCharCode(...a.subarray(i,i+16384));return btoa(s);})()",context,timeout=8)
                raw=base64.b64decode(encoded,validate=True)
            else:raise ValueError("image_source_not_cached")
            if not 0<len(raw)<=4194304:raise ValueError("image_original_limit")
            if total_bytes+len(raw)>6*1024*1024:raise ValueError("image_original_total_limit")
            mime,ext=validation_raster(raw);digest=hashlib.sha256(raw).hexdigest()
            name=f"validation/assets/original_{index:02d}.{ext}"
            archive.writestr(name,raw)
            total_bytes+=len(raw)
            item.update({"file":name,"sha256":digest,"bytes":len(raw),"mime":mime,"original_bytes_captured":True})
        except Exception:item["reason"]="original-byte-cache-unavailable"
        assets.append(item);digests.append(item.get("sha256") or "")
    await validation_eval(cdp,"globalThis.__CHAT2API_VALIDATION_DIGESTS__="+json.dumps(digests),context)
    return assets

def validation_file_bytes(filename,raw):
    ext=filename.rsplit(".",1)[-1].lower()
    if not 1<=len(raw)<=12*1024*1024:raise ValueError("file_byte_limit")
    if ext in ("xlsx","pptx","docx","zip"):
        with ZipFile(BytesIO(raw)) as zipped:
            infos=zipped.infolist();names=set(zipped.namelist())
            if len(infos)>1000 or sum(i.file_size for i in infos)>96*1024*1024:raise ValueError("file_zip_expansion")
            if len(names)!=len(infos) or any(i.flag_bits&1 for i in infos):raise ValueError("file_zip_ambiguous")
            if any(i.filename.startswith(("/","\\")) or ".." in i.filename.replace("\\","/").split("/") for i in infos):raise ValueError("file_zip_path")
            required={"xlsx":"xl/workbook.xml","pptx":"ppt/presentation.xml","docx":"word/document.xml"}
            if ext in required and ("[Content_Types].xml" not in names or required[ext] not in names):raise ValueError("file_office_structure")
            if zipped.testzip() is not None:raise ValueError("file_zip_crc")
    elif ext=="json":json.loads(raw.decode("utf-8-sig"))
    elif ext=="pdf" and not raw.startswith(b"%PDF-"):raise ValueError("file_pdf_signature")
    return True

async def validation_native_files(cdp,context,texts,archive,allow_click):
    if not allow_click:return {"status":"not-run","reason":"native-download-needs-owned-verified-single-reply","files":[]}
    # Download suppression is scoped to THIS diagnostic page, never Browser.*.
    try:await cdp.call("Page.setDownloadBehavior",{"behavior":"deny"})
    except Exception:return {"status":"not-run","reason":"per-page-download-suppression-unavailable","files":[]}
    channel="chat2api.validation.file."+os.urandom(12).hex()
    main=texts["chrome_extension/native_file_main_v210.js"]
    content=texts["chrome_extension/content_native_file_v210.js"]
    if main.count('"__CHAT2API_NATIVE_FILE_MAIN_V210__"')!=1 or main.count('"chat2api.native-file.v210"')!=1 or content.count('"chat2api.native-file.v210"')!=1:
        return {"status":"failed","reason":"candidate-file-hook-contract-changed","files":[]}
    # Only the singleton key and private message channel are namespaced. All

    main=main.replace('"__CHAT2API_NATIVE_FILE_MAIN_V210__"',json.dumps(channel+".main"),1).replace('"chat2api.native-file.v210"',json.dumps(channel),1)
    content=content.replace('"chat2api.native-file.v210"',json.dumps(channel),1)
    await validation_eval(cdp,main)
    await validation_eval(cdp,"globalThis.__VALIDATION_FILE_CHUNKS__=[];globalThis.chrome={runtime:{sendMessage:async m=>{if(m.type!=='chat2api.file.chunk')return {ok:false};__VALIDATION_FILE_CHUNKS__.push(m.event);return {ok:true};}}};",context)
    await validation_eval(cdp,content,context)
    expected=await validation_eval(cdp,r"[...String(__CHAT2API_VALIDATION_ROOTS__[0].innerText||'').matchAll(/\.((?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md))\b/gi)].map(m=>m[1].toLowerCase())",context)
    result=await validation_eval(cdp,"__CHAT2API_NATIVE_FILE_V210__.capture(__CHAT2API_VALIDATION_ROOTS__[0],'req_validation_files001')",context,timeout=45)
    if set(result.get("captured_types",[]))>={"xlsx","pptx","json"} and "zip" not in result.get("captured_types",[]):
        await asyncio.sleep(5.1)
        result=await validation_eval(cdp,"__CHAT2API_NATIVE_FILE_V210__.capture(__CHAT2API_VALIDATION_ROOTS__[0],'req_validation_files001')",context,timeout=15)
    chunks=await validation_eval(cdp,"__VALIDATION_FILE_CHUNKS__",context)
    staged={}
    for row in chunks or []:staged.setdefault(row.get("upload_id"),[]).append(row)
    rows=[];total=0;originals={}
    pattern=r"!\[📎 ([^\]\r\n]{1,180})\]\((data:[a-z0-9.+-]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+|chat2api-staged:[a-f0-9]{32})\)"
    for index,match in enumerate(re.finditer(pattern,str(result.get("text") or ""),re.I)):
        filename,marker=match.groups();ext=filename.rsplit(".",1)[-1].lower()
        if re.search(r"[/\\\x00-\x1f]",filename) or ext not in {"xlsx","pptx","docx","zip","json","pdf","csv","txt","md","py","js","ts","html"}:continue
        item={"filename":filename,"extension":ext,"status":"failed"}
        try:
            if marker.startswith("data:"):raw=base64.b64decode(marker.split(";base64,",1)[1],validate=True)
            else:
                parts=sorted(staged.get(marker.split(":",1)[1],[]),key=lambda x:x.get("sequence",-1))
                if not parts or [x.get("sequence") for x in parts]!=list(range(len(parts))) or parts[-1].get("final") is not True:raise ValueError("file_staging_sequence")
                raw=b"".join(base64.b64decode(x["base64_chunk"],validate=True) for x in parts)
                if len(raw)!=parts[0]["expected_bytes"] or hashlib.sha256(raw).hexdigest()!=parts[0]["expected_sha256"]:raise ValueError("file_staging_hash")
            validation_file_bytes(filename,raw)
            if total+len(raw)>6*1024*1024:raise ValueError("file_report_total_limit")
            name=f"validation/files/original_{index:02d}.{ext}";archive.writestr(name,raw);total+=len(raw)
            item.update({"file":name,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"status":"passed","source":"candidate-native-click-original-bytes"})
            originals[filename]=raw
        except Exception:item["reason"]="file-byte-validation-or-budget-failed"
        rows.append(item)
    has_candidates=int(result.get("button_candidates") or 0)>0
    missing=sorted(set(expected or [])-{x["extension"] for x in rows if x["status"]=="passed"})
    zip_members=[]
    for name,raw in originals.items():
        if not name.lower().endswith('.zip'):continue
        with ZipFile(BytesIO(raw)) as zipped:
            for filename,data in originals.items():
                if filename.lower().endswith('.zip'):continue
                ok=filename in zipped.namelist() and hashlib.sha256(zipped.read(filename)).digest()==hashlib.sha256(data).digest()
                zip_members.append({"filename":filename,"bytes_identical":ok})
    state="failed" if any(x["status"]=="failed" for x in rows) or result.get("failed_types") or any(not x["bytes_identical"] for x in zip_members) else "incomplete" if missing else "passed" if rows else "not-applicable" if not has_candidates and not expected else "incomplete"
    return {"status":state,"files":rows,"missing_types":missing,"zip_members":zip_members,"button_candidates":result.get("button_candidates",0),"failed_types":result.get("failed_types",[]),"failure_stages":result.get("failure_stages",{}),"synthesized_zip":bool(result.get("synthesized_zip")),"browser_save_suppressed":True,"source_transform":"singleton-key-and-message-channel-only","generated_file_http_routes":"not-run"}

async def validation_capture_snapshot(cdp,context,texts):

    # supplied externally; this does NOT test request-v6's live turn binding.
    for path in VALIDATION_FILES[:2]:await validation_eval(cdp,texts[path],context)
    return await validation_eval(cdp,"""(()=>{
 const roots=__CHAT2API_VALIDATION_ROOTS__,images=__CHAT2API_VALIDATION_IMAGES__||[];
 const active={requestId:'req_validation_replay001',submitStartedAt:1,lastCandidateNode:roots[0],
   nativeVerifiedReplyScope:roots.length===1?roots[0]:null,baselineRolelessHeadings:new Set(),
   nativeImageDigests:new Map(images.map((e,i)=>[e,(__CHAT2API_VALIDATION_DIGESTS__||[])[i]]).filter(x=>x[1]))};
 const snapshot=__CHAT2API_NATIVE_CONTENT_V1__.snapshot(active);
 return {snapshot,failure:active.nativeStructuralFailure||'',max_children:active.nativeStructuralMaxChildren||0};
})()""",context)

def validation_server(texts,snapshot,assets,folder):
    import tempfile
    ns={"__name__":"chat2api_validation_candidate"}

    # Never import any .py/.js that happened to arrive inside the fixture ZIP.
    exec(compile(texts["app/native_content_v1.py"],"candidate/app/native_content_v1.py","exec"),ns)
    validated=ns["validate_native_snapshot"](snapshot)
    with tempfile.TemporaryDirectory(prefix="native-validation-",dir=folder) as tmp:
        store=ns["NativeContentStore"](Path(tmp));owner="validation-only-owner"
        rid="req_validation_direct001"
        if not store.publish(rid,owner,snapshot) or not store.persist(rid,owner):raise ValueError("candidate_store_failed")
        if ns["NativeContentStore"](Path(tmp)).get(rid,owner)!=validated:raise ValueError("candidate_store_reload_failed")
        if store.get(rid,"different-validation-owner") is not None:raise ValueError("candidate_store_owner_failed")
        chunks=ns["NativeChunkAssembler"](store);raw=json.dumps(snapshot,ensure_ascii=False,separators=(",",":")).encode()
        length=chunks.CHUNK_BYTES;count=(len(raw)+length-1)//length;digest=hashlib.sha256(raw).hexdigest()
        for index in range(count):
            committed=chunks.ingest("req_validation_chunks001",owner,{"revision":snapshot["revision"],"index":index,"count":count,"byte_length":len(raw),"sha256":digest,"data":base64.b64encode(raw[index*length:(index+1)*length]).decode()})
            if committed!=(index==count-1):raise ValueError("candidate_chunk_commit_failed")
        if store.get("req_validation_chunks001",owner)!=validated:raise ValueError("candidate_chunk_reload_failed")
    identity=ns["native_image_identity_candidates"](validated,[{"sha256":x.get("sha256")} for x in assets])
    return validated,{"validation":"passed","persistence":"passed","chunk_assembly":"passed","owner_isolation":"passed",
        "layout":ns["native_layout_summary"](validated,len(assets)),"image_identity":identity,
        "http_routes":"not-run","live_websocket_ack":"not-run","sse_delivery":"not-run"}

def validation_renderer(texts):
    source=texts["app/request_history_v94_patch.py"]
    first=source.find("const requestHistoryNativeTags=new Set(")
    end=source.find("async function requestHistoryLoadNativePreview(",first)
    if first<0 or end<=first:raise ValueError("candidate_renderer_contract_changed")
    return source[first:end]

def validation_plain_tree(root):
    parts=[];counts={"tables":0,"images":0,"headings":0};stack=[root]
    while stack:
        node=stack.pop();tag=node.get("tag")
        if node.get("text"):parts.append(str(node["text"]))
        if tag=="table":counts["tables"]+=1
        if tag=="img":counts["images"]+=1
        if re.fullmatch(r"h[1-6]",str(tag or "")):counts["headings"]+=1
        stack.extend(reversed(node.get("children") or []))
    return re.sub(r"\s+"," ","".join(parts)).strip(),counts

def validation_pixel_diff(reference,candidate):
    width,height=struct.unpack(">II",reference[16:24]);cw,ch=struct.unpack(">II",candidate[16:24])
    if (width,height)!=(cw,ch):raise ValueError("comparison_dimensions_mismatch")
    c1,r1=native_png_crop_rows(reference,0,0,width,height);c2,r2=native_png_crop_rows(candidate,0,0,width,height)
    a=3 if c1==2 else 4;b=3 if c2==2 else 4;changed=0;changed12=0;rows=[]
    for left,right in zip(r1,r2):
        heat=bytearray(b"\xff"*(width*3))
        if a==b and left==right:
            rows.append(bytes(heat));continue
        for x in range(width):
            lp=left[x*a:x*a+3];rp=right[x*b:x*b+3]
            diff=lp!=rp
            if diff:
                changed+=1;heat[x*3:x*3+3]=bytes((235,45,70))
                if (abs(lp[0]-rp[0])>12 or abs(lp[1]-rp[1])>12
                    or abs(lp[2]-rp[2])>12):changed12+=1
        rows.append(bytes(heat))
    return {"changed_pixels":changed,"changed_pixels_tol12":changed12,"pixels":width*height,"changed_ratio":round(changed/(width*height),6),"changed_ratio_tol12":round(changed12/(width*height),6)},native_png_pack_rows(width,height,2,rows)

def validation_safe_fixture(value):
    if not isinstance(value,dict) or value.get("schema")!="native-reply-fixture-v1":raise ValueError("fixture_schema")
    styles=value.get("styles")
    if not isinstance(styles,list) or len(styles)>30000:raise ValueError("fixture_styles")
    for css in styles:
        if not isinstance(css,dict) or len(css)>len(VALIDATION_CSS):raise ValueError("fixture_styles")
        for k,v in css.items():
            if k not in VALIDATION_CSS or not isinstance(v,str) or len(v)>300 or re.search(r"url\s*\(|var\s*\(|[<>;{}]",v,re.I):raise ValueError("fixture_unsafe_style")
    state={"nodes":0,"chars":0,"images":[]}
    def check(node,depth):
        if depth>66 or not isinstance(node,dict):raise ValueError("fixture_depth")
        state["nodes"]+=1
        if state["nodes"]>30001 or set(node)-{"tag","text","style","children","attrs","ns","image","natural_width","natural_height"}:raise ValueError("fixture_node")
        tag=node.get("tag")
        if tag=="#text":
            if set(node)!={"tag","text"} or not isinstance(node["text"],str):raise ValueError("fixture_text")
            state["chars"]+=len(node["text"])
            if state["chars"]>300000:raise ValueError("fixture_text_limit")
            return
        if tag not in VALIDATION_TAGS or node.get("ns") not in (None,"svg","math"):raise ValueError("fixture_tag")
        if "style" in node and (type(node["style"]) is not int or not 0<=node["style"]<len(styles)):raise ValueError("fixture_style_ref")
        attrs=node.get("attrs",{})
        if not isinstance(attrs,dict):raise ValueError("fixture_attrs")
        for k,v in attrs.items():
            if k not in {"colspan","rowspan","start","viewBox","d","fill","stroke","stroke-width","x","y","x1","y1","x2","y2","cx","cy","r","rx","ry","width","height","points"} or not isinstance(v,str) or len(v)>20000 or re.search(r"url\s*\(|[<>]",v,re.I):raise ValueError("fixture_unsafe_attr")
        if tag=="img" and (type(node.get("image")) is not int or not 0<=node["image"]<40):raise ValueError("fixture_image_index")
        if tag=="img":
            state["images"].append(node["image"])
            if any(type(node.get(k,0)) is not int or not 0<=node.get(k,0)<=16384 for k in ("natural_width","natural_height")):raise ValueError("fixture_image_dimensions")
        children=node.get("children",[])
        if not isinstance(children,list):raise ValueError("fixture_children")
        for child in children:check(child,depth+1)
    check(value.get("root"),0)
    rect=value.get("rect",{})
    if any(type(rect.get(k)) is not int or not 1<=rect[k]<=40960 for k in ("w","h")) or rect["w"]>3000:raise ValueError("fixture_geometry")
    family=value.get("font_family","")
    if not isinstance(family,str) or len(family)>300 or re.search(r"[<>;{}]|url\s*\(",family,re.I):raise ValueError("fixture_font")
    if state["images"]!=list(range(len(state["images"]))):raise ValueError("fixture_image_order")
    if value.get("unsupported") not in (None,[]) and (not isinstance(value["unsupported"],list) or len(value["unsupported"])>30000 or any(x!="canvas" for x in value["unsupported"])):raise ValueError("fixture_unsupported")
    value["counts"]={"nodes":state["nodes"],"text_chars":state["chars"],"images":len(state["images"])}
    return value

def validation_report_html(report,strips):
    import html
    esc=lambda x:html.escape(str(x),quote=True)
    nonce=os.urandom(12).hex();status=str(report.get("status") or "incomplete")
    labels={"passed":"通过","failed":"失败","incomplete":"未完整验证","not-run":"未运行"}
    checks=report.get("checks") or {}
    rows="".join("<tr><td>"+esc(k)+"</td><td>"+esc(labels.get(v,v))+"</td></tr>" for k,v in checks.items())
    pics=""
    for item in strips:
        cols="".join('<div><b>'+label+'</b><img loading="lazy" src="'+esc(item[key])+'"></div>' for key,label in (("reference","原生参考"),("candidate","候选代码交付"),("difference","像素差异（红色）")))
        pics+='<section><h3>第 '+str(item["index"]+1)+' 段 · 差异 '+esc(item["diff"]["changed_ratio"]*100)+'%</h3><div class="compare">'+cols+'</div></section>'
    note="历史页面验证不证明新请求发送、模型选择、生成终态或真实 SSE/WebSocket 送达；原始图片、文件下载、范围归属或参考截图缺失时，不能判为完整通过。"
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' data: file:; style-src \'unsafe-inline\'; script-src \'nonce-'+nonce+'\'"><title>富内容交付验证报告</title><style>body{font:15px system-ui;margin:24px;color:#152238;background:#f6f8fc}h1{margin-bottom:8px}table{border-collapse:collapse;background:white}td{padding:8px 16px;border:1px solid #dae0ea}.compare{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.compare div{background:white;overflow:auto}.compare b{display:block;padding:8px;position:sticky;top:0;background:white}.compare img{width:100%;display:block}section{margin:24px 0}pre{white-space:pre-wrap;max-height:240px;overflow:auto;background:white;padding:16px}.note{background:#fff0c2;padding:12px;border-radius:8px}button{padding:8px 16px}body.diff-off .compare{grid-template-columns:repeat(2,minmax(0,1fr))}body.diff-off .compare div:last-child{display:none}</style><h1>富内容交付验证：'+esc(labels.get(status,status))+'</h1><p>候选提交：<code>'+esc((report.get("candidate") or {}).get("commit") or (report.get("candidate") or {}).get("tree_sha256") or "未加载")+'</code> · 工具 '+esc(VERSION)+'</p><p class="note">'+note+'</p><table>'+rows+'</table><p><button id="diff">显示／隐藏差异列</button></p>'+pics+'<h2>机器可读报告</h2><pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre><script nonce="'+nonce+'">document.getElementById("diff").onclick=()=>document.body.classList.toggle("diff-off");</script></html>')

def validation_write_result(archive,report,strips):
    archive.writestr("validation/report.json",json.dumps(report,ensure_ascii=False,indent=2))

    archive.writestr("index.html",validation_report_html(report,strips))
    archive.writestr("REPLAY.txt", "富内容交付回放\n\n解压后打开 index.html 查看原生参考、候选交付和像素差异。\n"
        "把这个 ZIP 留作回归样本；以后测试候选修复无需重新生成回复或部署主服务。\n"
        "从仓库独立诊断分支取得可信工具后，复用 Worker 的本机 Chrome 调试端口：\n"
        "python native_page_diagnostic_v1.py --replay sample.zip --output replay-result --cdp-port 9222\n"
        "可加 --candidate-sha <40位提交> 固定版本；本地开发可用 --source-dir <可信项目目录>。\n"
        "默认候选分支："+VALIDATION_REF+"；每次开始时固定提交并校验源码哈希。\n"
        "不要执行样本 ZIP 内的源码；工具只把它们留作审计，不会导入执行。\n"
        "回放仅新建空白页，不打开 ChatGPT、不登录、不发起模型请求。\n"
        "缺失的原图、文件、回复归属或参考截图仍需一次真实采样。\n"
        "历史回放不能证明新请求绑定、模型选择、生成终态或真实 SSE/WebSocket 送达。\n")

def validation_merge(archive,payload,report):
    # Preserve verified sources and complete triples within the ZIP cap.
    with ZipFile(BytesIO(payload)) as extra:
        budget=MAX_ZIP-archive.fp.tell()-512000;used=0;keep=set();omitted=[]
        special={"validation/report.json","index.html","REPLAY.txt","validation/assets.json"}
        items=extra.infolist()
        def visual(name):
            return name.startswith(("validation/reference/strip_",
                "validation/candidate/strip_","validation/difference/strip_"))
        def priority(name):
            if (name.endswith("fixture.json") or name in
                ("validation/source.json","validation/snapshot.json") or
                name.startswith("validation/source/")):return 0
            if name.startswith(("validation/assets/","validation/files/")):return 1
            return 2
        def cost(item):return item.compress_size+len(item.filename)+160
        def copy(item):
            nonlocal used
            archive.writestr(item.filename,extra.read(item.filename))
            used+=cost(item);keep.add(item.filename)
        for item in sorted(items,key=lambda x:(priority(x.filename),x.filename)):
            if item.filename in special or visual(item.filename):continue
            if used+cost(item)>budget:omitted.append(item.filename);continue
            copy(item)
        # Save complete visual triples, not half-pairs.
        rows=report.get("visual_strips") or []
        order=([0,len(rows)-1] if len(rows)>1 else [0] if rows else [])
        pending=[(1,len(rows)-2)]
        while pending:
            next_pending=[]
            for lo,hi in pending:
                if lo>hi:continue
                mid=(lo+hi)//2;order.append(mid)
                next_pending.extend(((lo,mid-1),(mid+1,hi)))
            pending=next_pending
        available={item.filename:item for item in items}
        for idx in order:
            row=rows[idx]
            names=[row.get(k) for k in ("reference","candidate","difference")]
            group=[available.get(n) for n in names]
            if not all(group) or used+sum(cost(item) for item in group)>budget:
                omitted.extend(n for n in names if isinstance(n,str))
                continue
            for item in group:copy(item)
        for item in items:
            if visual(item.filename) and item.filename not in keep and item.filename not in omitted:
                omitted.append(item.filename)
        assets=json.loads(extra.read("validation/assets.json")) if "validation/assets.json" in extra.namelist() else []
        for row in assets:
            if row.get("file") and row["file"] not in keep:
                for k in ("file","sha256","bytes","mime"):row.pop(k,None)
                row.update({"original_bytes_captured":False,"reason":"evidence-transfer-budget"})
        archive.writestr("validation/assets.json",json.dumps(assets,ensure_ascii=False))
        for row in (report.get("files") or {}).get("files",[]):
            if row.get("file") and row["file"] not in keep:
                row.pop("file",None);row["status"]="incomplete";row["reason"]="evidence-transfer-budget"
                report["files"]["status"]="incomplete";report.setdefault("checks",{})["file_bytes"]="incomplete"
        strips=[x for x in rows if all(x.get(k) in keep for k in ("reference","candidate","difference"))]
        report["visual_packaging"]={"complete_triplets":len(strips),
            "total_triplets":len(rows),"selection":"spread-complete-triples-v1",
            "all_strips_archived":len(strips)==len(rows)}
        if omitted:
            report["omitted_artifacts"]=omitted
            if report.get("status")!="failed":report["status"]="incomplete"
            if any(n.startswith(("validation/reference/","validation/candidate/",
                "validation/difference/","validation/assets/")) for n in omitted):
                report.setdefault("checks",{})["visual"]="incomplete"
                if report.get("capture_render_status")!="failed":report["capture_render_status"]="incomplete"
        validation_write_result(archive,report,strips)
    return report


async def validation_render(cdp,context,texts,snapshot,fixture,assets,reference_strips,archive):
    renderer=validation_renderer(texts)
    # Use trusted production renderer; fixture code is never evaluated.
    await validation_eval(cdp,renderer,context)
    result=await validation_eval(cdp,"""(async function(snapshot,assets,font){
 // A full-height preview would otherwise create a 15px viewport scrollbar
 // and shrink the 768px reference content to 753px in Chromium.
 // The isolated screenshot uses Page.captureScreenshot beyond the viewport.
 document.documentElement.style.overflow='hidden';
 document.body.style.cssText='margin:0;background:white';document.body.style.fontFamily=font||'Arial,sans-serif';
 const tree=requestHistoryNativeNode(snapshot.root,assets,{count:0});document.body.replaceChildren(tree);
 // The production renderer remains lazy. Only the isolated validation
 // screenshot eagerly decodes offscreen images: otherwise decode() can hang.
 const images=[...tree.querySelectorAll('img')];
 for(const image of images)image.loading='eager';
 const imagesReady=await new Promise(resolve=>{
   const deadline=setTimeout(()=>resolve(false),12000);
   Promise.allSettled(images.map(image=>image.decode())).then(
     ()=>{clearTimeout(deadline);resolve(true);},
     ()=>{clearTimeout(deadline);resolve(false);});
 });
 const fontsReady=await Promise.race([
   document.fonts.ready.then(()=>true,()=>false),
   new Promise(resolve=>setTimeout(()=>resolve(false),5000))]);
 const r=tree.getBoundingClientRect();
 return {width:Math.ceil(r.width),height:Math.ceil(r.height),
   images_expected:images.length,
   images_loaded:images.filter(image=>image.complete&&image.naturalWidth>0).length,
   images_ready:imagesReady,fonts_ready:fontsReady};
})("""+json.dumps(snapshot,ensure_ascii=False)+","+json.dumps(assets)+","+json.dumps(fixture.get("font_family",""))+")",context)
    comparisons=[];changed=changed12=total=0
    for index,item in enumerate(reference_strips[:64]):
        raw=item["bytes"];width,height=struct.unpack(">II",raw[16:24]);offset=int(item["offset"])
        png=await cdp.call("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,"clip":{"x":0,"y":offset,"width":width,"height":height,"scale":1}},timeout=20)
        candidate=base64.b64decode(png["data"],validate=True)
        diff,heat=validation_pixel_diff(raw,candidate);changed+=diff["changed_pixels"];changed12+=diff["changed_pixels_tol12"];total+=diff["pixels"]
        refname=f"validation/reference/strip_{index:02d}.png";canname=f"validation/candidate/strip_{index:02d}.png";diffname=f"validation/difference/strip_{index:02d}.png"
        archive.writestr(refname,raw);archive.writestr(canname,candidate);archive.writestr(diffname,heat)
        comparisons.append({"index":index,"offset":offset,"reference":refname,"candidate":canname,"difference":diffname,"diff":diff})
        if archive.fp.tell()>MAX_ZIP-512000:break
    coverage=sum(struct.unpack(">II",x["bytes"][16:24])[1] for x in reference_strips[:len(comparisons)])
    assets_ready=(result["images_loaded"]==result["images_expected"] and
        result["images_ready"] is True and result["fonts_ready"] is True)
    visual={"strips":len(comparisons),"reference_width":fixture["rect"]["w"],"rendered_width":result["width"],"reference_height":fixture["rect"]["h"],"rendered_height":result["height"],"coverage_px":coverage,"changed_pixels":changed,"pixels":total,"changed_ratio":round(changed/total,6) if total else None,"changed_ratio_tol12":round(changed12/total,6) if total else None,
        "images_expected":result["images_expected"],"images_loaded":result["images_loaded"],
        "images_ready":result["images_ready"],"fonts_ready":result["fonts_ready"],
        "complete":bool(assets_ready and reference_strips and coverage==fixture["rect"]["h"] and not fixture.get("unsupported")),"exact":bool(assets_ready and total and changed==0 and result["width"]==fixture["rect"]["w"] and result["height"]==fixture["rect"]["h"]),"renderer_scope":"production-admin-component"}
    return visual,comparisons

async def validation_run(cdp,context,fixture,texts,source,assets,reference_strips,archive,args,owned_tabs,provenance,files=None):
    started=time.monotonic()
    fixture=validation_safe_fixture(fixture)
    result={"schema":"native-delivery-validation-v1","tool_version":VERSION,"candidate":source,"mode":provenance.get("mode"),"provenance":provenance,
        "checks":{"capture":"not-run","server_validation":"not-run","persistence":"not-run","chunk_assembly":"not-run","text_order":"not-run","tables":"not-run","images":"not-run","file_bytes":"not-run","native_file_click":"not-run","visual":"not-run","live_turn_binding":"not-run","live_generation_terminal":"not-run","live_sse_delivery":"not-run"}}
    checks=result["checks"];comparisons=[]
    result["files"]=files or {"status":"not-run","files":[]}
    checks["file_bytes"]=result["files"]["status"]
    if result["files"].get("browser_save_suppressed") is True:checks["native_file_click"]=result["files"]["status"]
    archive.writestr("validation/reference/fixture.json",json.dumps(fixture,ensure_ascii=False))
    archive.writestr("validation/source.json",json.dumps(source,ensure_ascii=False,indent=2))
    for path,text in texts.items():archive.writestr("validation/source/"+path,text)
    archive.writestr("validation/assets.json",json.dumps(assets,ensure_ascii=False))
    try:
        result["stage"]="production-native-capture"
        captured=await validation_capture_snapshot(cdp,context,texts)
        if not captured or not captured.get("snapshot"):
            result["reason"]="capture-"+str((captured or {}).get("failure") or "unavailable")
            checks["capture"]="failed";result["status"]="failed";result["elapsed_seconds"]=round(time.monotonic()-started,3);validation_write_result(archive,result,[]);return result
        checks["capture"]="passed";snapshot=captured["snapshot"]
        result["stage"]="production-server-validation"
        validated,server=validation_server(texts,snapshot,assets,Path(args.output))
        result["server"]=server
        for key in ("server_validation","persistence","chunk_assembly"):checks[key]="passed"
        archive.writestr("validation/snapshot.json",json.dumps(validated,ensure_ascii=False))
        before,bcounts=validation_plain_tree(fixture["root"]);after,acounts=validation_plain_tree(validated["root"])
        checks["text_order"]="passed" if before==after else "failed"
        checks["tables"]="passed" if bcounts["tables"]==acounts["tables"] else "failed"
        result["structure"]={"reference":bcounts,"captured":acounts,"reference_text_sha256":hashlib.sha256(before.encode()).hexdigest(),"captured_text_sha256":hashlib.sha256(after.encode()).hexdigest()}
        checks["images"]=("passed" if bcounts["images"]==acounts["images"] and
            (bcounts["images"]==0 or server["image_identity"].get("status")=="verified-sha256") else "incomplete")
        if bcounts["images"]!=acounts["images"]:checks["images"]="failed"
        display=[]
        for i in range(bcounts["images"]):
            item=next((x for x in assets if x.get("image_index")==i),{})
            raw=archive.read(item["file"]) if item.get("file") in archive.namelist() else b""
            display.append("data:"+item.get("mime","image/png")+";base64,"+base64.b64encode(raw).decode() if raw else "")
        # assets. Missing originals stay explicitly incomplete, never verified.
        count=server["layout"]["images"]
        display=(display+[""]*40)[:count]
        if server["image_identity"].get("status")=="verified-sha256":
            # Ordinal-only display could conceal a candidate image reorder.
            display=[display[index] for index in server["image_identity"]["indices"]]
        if not reference_strips:
            result["stage"]="visual-reference-unavailable"
            result["visual"]={"complete":False,"exact":False,
                "reason":"safe-reference-screenshots-unavailable"}
            checks["visual"]="incomplete"
        else:
            ws,target=await validation_blank(args.cdp_port,owned_tabs)
            try:
                result["stage"]="production-renderer-visual-comparison"
                await target.call("Emulation.setDeviceMetricsOverride",{"width":fixture["rect"]["w"],"height":1100,"deviceScaleFactor":1,"mobile":False})
                _,world=await validation_world(target)
                visual,comparisons=await validation_render(target,world,texts,validated,fixture,display,reference_strips,archive)
                if provenance.get("reference_unverified_candidate_screenshots"):
                    visual["reference_quality"]="unverified-history-candidate-screenshots"
                    visual["complete"]=False
                    visual["exact"]=False
                result["visual"]=visual
                result["visual_strips"]=comparisons
                checks["visual"]=("passed" if visual["complete"] and visual["exact"] else "failed" if visual["complete"] else "incomplete")
            finally:await ws.close()
        required=("capture","server_validation","persistence","chunk_assembly","text_order","tables","images","visual")
        result["capture_render_status"]=("failed" if any(checks[k]=="failed" for k in required) else "passed" if all(checks[k]=="passed" for k in required) else "incomplete")
        result["status"]=("failed" if "failed" in checks.values() else "passed" if result["capture_render_status"]=="passed" and checks["file_bytes"] in ("passed","not-applicable") and provenance.get("boundary_verified") is True else "incomplete")
        result["scope"]="selected-existing-reply-capture-store-render-only"
    except Exception as exc:
        result["status"]="failed";result["reason"]=result.get("stage","validation")+":"+type(exc).__name__
        # Fixed stages and exception class only; no page/source URL or stack.
    result["elapsed_seconds"]=round(time.monotonic()-started,3)
    validation_write_result(archive,result,comparisons)
    return result

async def validation_scope(args,fixture,assets,archive,owned_tabs):
    # Candidate capture runs on the inert selected reply, not the page's
    # other conversation turns. Live request-v6 boundary binding is separate.
    display=[];digests=[]
    for index in range(fixture["counts"]["images"]):
        item=next((x for x in assets if x["image_index"]==index),{})
        raw=archive.read(item["file"]) if item.get("file") in archive.namelist() else b""
        display.append("data:"+item.get("mime","image/png")+";base64,"+base64.b64encode(raw).decode() if raw else "")
        digests.append(item.get("sha256","") if raw else "")
    ws,cdp=await validation_blank(args.cdp_port,owned_tabs)
    try:
        await cdp.call("Emulation.setDeviceMetricsOverride",{"width":fixture["rect"]["w"],"height":1100,"deviceScaleFactor":1,"mobile":False})
        _,context=await validation_world(cdp)
        await validation_eval(cdp,HYDRATE_REFERENCE+"("+json.dumps(fixture,ensure_ascii=False)+","+json.dumps(display)+")",context)
        await validation_eval(cdp,"globalThis.__CHAT2API_VALIDATION_DIGESTS__="+json.dumps(digests),context)
        return ws,cdp,context
    except BaseException:
        await ws.close();raise

async def delivery_validation_live(cdp,args,report,archive,owned_tabs,source_owned):
    import asyncio
    texts,source=await asyncio.to_thread(validation_sources,args.output,getattr(args,"candidate_sha",""),getattr(args,"source_dir",""))
    if source_owned:
        viewport=await validation_eval(cdp,"({width:innerWidth,height:innerHeight})")
        await cdp.call("Emulation.setDeviceMetricsOverride",{"width":max(800,min(3000,int(viewport["width"]))),"height":1100,"deviceScaleFactor":1,"mobile":False})
    frame,context=await validation_world(cdp)
    choose=CAPTURE.replace("const counters={","globalThis.__CHAT2API_VALIDATION_ROOTS__=roots;const counters={",1)
    chosen=await validation_eval(cdp,choose+"("+json.dumps(args.request_id)+")",context)
    if not chosen or not chosen.get("ok") or chosen.get("generating"):raise ValueError("validation_reference_unavailable")
    fixture=await validation_eval(cdp,FREEZE_REFERENCE+"("+json.dumps(VALIDATION_CSS)+")",context)
    fixture=validation_safe_fixture(fixture)
    assets=await validation_originals(cdp,frame,context,fixture,archive)
    try:files=await validation_native_files(cdp,context,texts,archive,source_owned and chosen.get("boundary_verified") is True and len(fixture["root"]["children"])==1)
    except Exception:files={"status":"failed","reason":"candidate-native-file-capture-failed","files":[]}
    strips=[];offset=0
    if source_owned:
        while offset<fixture["rect"]["h"] and len(strips)<64:
            part=await validation_eval(cdp,REFERENCE_STRIP+"("+str(offset)+")",context)
            if not part or not part.get("ok"):break
            png=await cdp.call("Page.captureScreenshot",{"format":"png","captureBeyondViewport":False,"clip":part["clip"]},timeout=20)
            raw=base64.b64decode(png["data"],validate=True)
            w,h=struct.unpack(">II",raw[16:24])
            if (w,h)!=(part["width"],part["height"]):break
            strips.append({"bytes":raw,"offset":offset});offset+=h
            if archive.fp.tell()>MAX_ZIP:raise ValueError("validation_archive_limit")
    # Detect source mutation during screenshots; unrelated page changes do
    # not matter, but altered reply text/structure must invalidate the reference.
    after=await validation_eval(cdp,FREEZE_REFERENCE+"("+json.dumps(VALIDATION_CSS)+")",context)
    stable=(validation_plain_tree(fixture["root"])==validation_plain_tree(after["root"]) and fixture["rect"]==after["rect"])
    if not stable:strips=[]
    ws,target,world=await validation_scope(args,fixture,assets,archive,owned_tabs)
    try:
        return await validation_run(target,world,fixture,texts,source,assets,strips,archive,args,owned_tabs,
            {"mode":"existing-live-history","boundary_verified":chosen.get("boundary_verified") is True,"business_request_bound":report.get("request_marker")==args.request_id,"reference_stable":stable,"source_owned_tab":source_owned,"candidate_capture_input":"inert-selected-reply-reconstruction","model_request_sent":False,"production_redeployed":False},files)
    finally:await ws.close()

def validation_import_legacy(archive):
    """Older diagnosis bundles remain useful, with explicit missing evidence."""
    names=set(archive.namelist());meta=json.loads(archive.read("manifest.json"))
    if meta.get("tool")!="native-page-diagnostic":raise ValueError("legacy_fixture_schema")
    name=meta.get("dom_file") or "assistant_dom.json"
    if name not in ("assistant_dom.json","unverified_rich_candidate_dom.json") or name not in names:raise ValueError("legacy_fixture_dom")
    report=json.loads(archive.read(name));styles=[];style_ids={};counter={"images":0}
    def node(value):
        tag=value.get("tag")
        if tag=="#text":return {"tag":"#text","text":str(value.get("text") or "")}
        if tag not in VALIDATION_TAGS:tag="div"
        css={k:v for k,v in (value.get("style") or {}).items() if k in VALIDATION_CSS and isinstance(v,str) and len(v)<300 and not re.search(r"url\s*\(|var\s*\(|[<>;{}]",v,re.I)}
        signature=json.dumps(css,sort_keys=True)
        if signature not in style_ids:style_ids[signature]=len(styles);styles.append(css)
        result={"tag":tag,"style":style_ids[signature]}
        if tag=="img":
            image=value.get("image") or {};result.update({"image":counter["images"],"natural_width":int(image.get("natural_width") or 0),"natural_height":int(image.get("natural_height") or 0)})
            counter["images"]+=1
        else:result["children"]=[node(x) for x in value.get("children",[])]
        return result
    root=node(report.get("root") or {});rect=report.get("rect") or {}
    fixture={"schema":"native-reply-fixture-v1","root":root,"styles":styles,"rect":{"w":int(rect.get("w") or 0),"h":int(rect.get("h") or 0)},"counts":{"images":counter["images"]},"font_family":"Arial,sans-serif","legacy_partial_style_capture":True}
    validation_safe_fixture(fixture)
    assets=[{"image_index":i,"original_bytes_captured":False,"reason":"legacy-bundle-has-no-original-image-bytes"} for i in range(counter["images"])]
    strips=[];offset=0
    for name in sorted(n for n in names if re.fullmatch(r"screenshots/(?:unverified_candidate|assistant)_[0-9]{2}\.png",n)):
        raw=archive.read(name)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw)>8*1024*1024:raise ValueError("legacy_png")
        w,h=struct.unpack(">II",raw[16:24])
        if not 1<=w<=3000 or not 1<=h<=2000:raise ValueError("legacy_png_bounds")
        strips.append({"bytes":raw,"offset":offset});offset+=h
    return fixture,assets,strips,{"mode":"legacy-diagnostic-replay","boundary_verified":False,"business_request_bound":False,"model_request_sent":False,"production_redeployed":False,"legacy_partial_styles":True,"original_image_bytes":"unavailable","source_tool_version":meta.get("version")}

def validation_import_zip(path):
    """Read inert fixtures with bounded ZIP expansion; never extract paths/code."""
    archive=ZipFile(path)
    infos=archive.infolist()
    if len(infos)>400 or sum(x.file_size for x in infos)>100*1024*1024:raise ValueError("fixture_zip_limit")
    if any(x.filename.startswith(("/","\\")) or ".." in x.filename.replace("\\","/").split("/") or x.flag_bits&1 for x in infos):raise ValueError("fixture_zip_path")
    names=set(archive.namelist())
    if len(names)!=len(infos):raise ValueError("fixture_duplicate_paths")
    if "validation/reference/fixture.json" not in names:
        if "manifest.json" not in names:raise ValueError("legacy_fixture_needs_one_recapture")
        try:return validation_import_legacy(archive)
        finally:archive.close()
    fixture=validation_safe_fixture(json.loads(archive.read("validation/reference/fixture.json")))
    assets=json.loads(archive.read("validation/assets.json"));meta=json.loads(archive.read("validation/report.json"))
    if not isinstance(meta,dict):raise ValueError("fixture_report")
    if not isinstance(assets,list) or len(assets)>40:raise ValueError("fixture_asset_limit")
    originals=[]
    for item in assets:
        if not isinstance(item,dict) or type(item.get("image_index")) is not int or not 0<=item["image_index"]<40:raise ValueError("fixture_asset")
        row={k:item[k] for k in ("image_index","file","sha256","mime","bytes") if k in item}
        row["original_bytes_captured"]=False
        if row.get("file"):
            if not re.fullmatch(r"validation/assets/original_[0-9]{2}\.(?:png|jpg|webp|gif)",row["file"]):raise ValueError("fixture_asset_path")
            raw=archive.read(row["file"])
            if not 0<len(raw)<=4194304 or hashlib.sha256(raw).hexdigest()!=row.get("sha256"):raise ValueError("fixture_asset_hash")
            mime,_=validation_raster(raw)
            if mime!=row.get("mime"):raise ValueError("fixture_asset_mime")
            row.update({"bytes_data":raw,"bytes":len(raw),"original_bytes_captured":True})
        originals.append(row)
    if [x["image_index"] for x in originals]!=list(range(len(originals))):raise ValueError("fixture_asset_order")
    strips=[];offset=0
    for name in sorted(n for n in names if re.fullmatch(r"validation/reference/strip_[0-9]{2}\.png",n)):
        raw=archive.read(name)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw)>8*1024*1024:raise ValueError("fixture_png")
        w,h=struct.unpack(">II",raw[16:24])
        if not 1<=w<=3000 or not 1<=h<=640 or w!=fixture["rect"]["w"]:raise ValueError("fixture_png_bounds")
        strips.append({"bytes":raw,"offset":offset});offset+=h
    fallback_unverified=False
    # Legacy screenshots remain unverified; never upgrade their provenance.
    if not strips and "manifest.json" in names:
        try:
            manifest=json.loads(archive.read("manifest.json"))
            count=manifest.get("screenshot_count")
            if (manifest.get("tool")=="native-page-diagnostic"
                and manifest.get("capture_layout_stable") is True
                and manifest.get("scope")=="inferred-rich-table-candidate"
                and type(count) is int and 1<=count<=MAX_SCREENSHOTS):
                recovered=[];covered=0
                for i in range(count):
                    name=f"screenshots/unverified_candidate_{i:02d}.png"
                    if name not in names:break
                    raw=archive.read(name)
                    if (len(raw)>8*1024*1024 or len(raw)<24 or
                        not raw.startswith(b"\x89PNG\r\n\x1a\n")):break
                    w,h=struct.unpack(">II",raw[16:24])
                    if w!=fixture["rect"]["w"] or not 1<=h<=640:break
                    recovered.append({"bytes":raw,"offset":covered});covered+=h
                if (len(recovered)==count and
                    covered==manifest.get("screenshot_coverage_px") and
                    covered<=fixture["rect"]["h"]):
                    strips=recovered;fallback_unverified=True
        except (ValueError,TypeError,KeyError,struct.error):
            pass
    files={"status":"not-run","files":[],"native_click":"not-run","source":"fixture-byte-revalidation"}
    oldfiles=meta.get("files") or {};items=oldfiles.get("files",[]) if isinstance(oldfiles,dict) else []
    if not isinstance(items,list) or len(items)>40:raise ValueError("fixture_file_limit")
    total=0
    for item in items:
        if not isinstance(item,dict):raise ValueError("fixture_file")
        name=item.get("file");filename=item.get("filename")
        if not name:continue
        if not isinstance(name,str) or not re.fullmatch(r"validation/files/original_[0-9]{2}\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md|py|js|ts|html)",name):raise ValueError("fixture_file_path")
        if not isinstance(filename,str) or len(filename)>180 or re.search(r"[/\\\x00-\x1f]",filename) or filename.rsplit(".",1)[-1].lower()!=name.rsplit(".",1)[-1]:raise ValueError("fixture_file_name")
        raw=archive.read(name);total+=len(raw)
        if total>6*1024*1024 or hashlib.sha256(raw).hexdigest()!=item.get("sha256"):raise ValueError("fixture_file_hash")
        validation_file_bytes(filename,raw)
        files["files"].append({"filename":filename,"file":name,"extension":filename.rsplit(".",1)[-1],"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"bytes_data":raw,"status":"passed"})
    if files["files"]:files["status"]="passed"
    files["zip_members"]=[]
    for row in files["files"]:
        if row["extension"].lower()!="zip":continue
        with ZipFile(BytesIO(row["bytes_data"])) as zipped:
            for other in files["files"]:
                if other["extension"].lower()=="zip":continue
                name=other["filename"];ok=name in zipped.namelist() and zipped.read(name)==other["bytes_data"]
                files["zip_members"].append({"filename":name,"bytes_identical":ok})
                if not ok:files["status"]="failed"
    # Missing earlier files remain incomplete even if another file replays.
    if not isinstance(oldfiles,dict):raise ValueError("fixture_file_metadata")
    if oldfiles and oldfiles.get("status") not in ("passed","not-applicable") and files["status"]!="failed":files["status"]="incomplete"
    archive.close()
    # User-supplied report status and candidate source are not trusted proof.
    return fixture,originals,strips,{"mode":"offline-fixture-replay","boundary_verified":False,"business_request_bound":False,"reference_from_prior_capture":True,"reference_unverified_candidate_screenshots":fallback_unverified,"candidate_capture_input":"inert-selected-reply-reconstruction","model_request_sent":False,"production_redeployed":False,"input_provenance":str((meta.get("provenance") or {}).get("mode", ""))[:80],"_file_replay":files}

async def delivery_validation_replay(args):
    import websockets
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if Path(args.replay).resolve()==(folder/"native-page-evidence.zip").resolve():raise ValueError("replay_output_would_overwrite_input")
    start=time.monotonic();owned=[];ws=None
    try:
        fixture,assets,strips,provenance=validation_import_zip(args.replay)
        texts,source=await asyncio.to_thread(validation_sources,folder,args.candidate_sha,args.source_dir)
        files=provenance.pop("_file_replay",None)
        with ZipFile(folder/"native-page-evidence.zip","w",ZIP_DEFLATED,compresslevel=6) as archive:
            for row in assets:
                raw=row.pop("bytes_data",b"")
                if raw:archive.writestr(row["file"],raw)
            for row in (files or {}).get("files",[]):
                raw=row.pop("bytes_data",b"")
                if raw:archive.writestr(row["file"],raw)
            ws,cdp,context=await validation_scope(args,fixture,assets,archive,owned)
            result=await validation_run(cdp,context,fixture,texts,source,assets,strips,archive,args,owned,provenance,files)
            archive.writestr("manifest.json",json.dumps({"tool":"native-delivery-replay","version":VERSION,"candidate":source,"model_request_sent":False,"validation_status":result["status"]}))
        print(json.dumps({"status":result["status"],"capture_render_status":result.get("capture_render_status","incomplete"),"report":str(folder/"native-page-evidence.zip"),"elapsed_seconds":round(time.monotonic()-start,3)},ensure_ascii=False))
        return 0 if result["status"]=="passed" else 2
    finally:
        if ws:await ws.close()
        for tab in owned:
            try:
                with urlopen(f"http://127.0.0.1:{args.cdp_port}/json/close/"+quote(tab,safe=""),timeout=3):pass
            except Exception:pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--version",action="store_true")
    parser.add_argument("--output");parser.add_argument("--request-id")
    parser.add_argument("--worker-id");parser.add_argument("--cdp-port",type=int,default=9222)
    parser.add_argument("--conversation-url",default="")
    parser.add_argument("--replay",default="",help="Replay an inert evidence ZIP in new blank Chrome tabs; no model request")
    parser.add_argument("--candidate-sha",default="",help="Pin trusted repository sources to a 40-character commit")
    parser.add_argument("--source-dir",default="",help="Use an explicitly trusted local checkout instead of GitHub sources")
    args=parser.parse_args()
    if args.version:print(VERSION);return
    if args.replay:
        if not args.output or not 1024<=args.cdp_port<=65535:raise SystemExit("invalid_replay_arguments")
        raise SystemExit(asyncio.run(delivery_validation_replay(args)))
    if not REQUEST.fullmatch(args.request_id or "") or not re.fullmatch(r"wrk_[A-Za-z0-9_-]{6,100}",args.worker_id or "") or args.cdp_port not in range(9222,9254):
        raise SystemExit("invalid_request_or_worker")
    folder=Path(args.output)
    try:asyncio.run(capture(args))
    except Exception as exc:
        # can carry this fixed, non-sensitive diagnostic summary as "error".
        probe={}
        try:
            probe=json.loads((folder/"status.json").read_text()).get("page_probe") or {}
        except (OSError,ValueError,TypeError):pass
        error=str(exc)[:115]
        if isinstance(probe,dict) and probe:
            bits=[str(probe.get(key) or "-")[:22] for key in (
                "navigation_route","ready_state","capture_error")]
            bits.append("a="+str(int(probe.get("assistant_roles") or 0)))
            bits.append("v="+str(probe.get("visibility_state") or "-")[:8])
            bits.append("f="+str(int(probe.get("fetch_total") or 0)))
            bits.append("4="+str(sum(int(probe.get(k) or 0) for k in (
                "fetch_http_401_403","fetch_http_404","fetch_http_429",
                "fetch_http_other_4xx"))))
            bits.append("5="+str(int(probe.get("fetch_http_5xx") or 0)))
            bits.append("r="+str(int(bool(probe.get("reload_attempted")))))
            bits.append("s="+str(int(probe.get("skeleton_count") or 0)))
            bits.append("c="+str(int(probe.get("conversation_api_observed") or 0)))
            bits.append("j="+str(int(probe.get("js_exception_count") or 0)))
            bits.append("u="+str(int(probe.get("unhandled_rejection_count") or 0)))
            bits.append("t="+str(int(probe.get("transient_redirect_count") or 0)))
            bits.append("g="+str(int(probe.get("agent_turns") or 0)))
            error=(error+" ["+";".join(bits)+"]")[:180]
        status(folder,"failed",100,error=error)
        raise SystemExit(1)

if __name__=="__main__":main()
