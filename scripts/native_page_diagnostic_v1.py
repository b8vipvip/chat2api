#!/usr/bin/env python3
"""Versioned, read-only CDP evidence collector for existing ChatGPT replies.

An independent Worker tool: it neither submits prompts nor changes chat2api
request status. Does not read cookies, storage, network bodies or signed URLs.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit, quote
from urllib.request import urlopen, Request
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = "1.2.15"
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
let scope='',roots=[],boundaryVerified=true;
const tagged=document.querySelector('[data-chat2api-native-request-id="'+expected+'"]');
const assistant=[...document.querySelectorAll('[data-message-author-role="assistant"]')].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&!e.parentElement?.closest('[data-message-author-role="assistant"]'));
if(assistant.length){
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
 // sometimes no data-message-author-role or "ChatGPT said" heading.
 // This is an explicit assistant marker, never a generic last article.
 scope='verified-agent-turn';
 roots=[...document.querySelectorAll(
  'main article[data-testid^="conversation-turn-"] .agent-turn,main article .agent-turn,main [data-testid^="conversation-turn-"].agent-turn'
 )].filter(e=>e.isConnected&&visible(e)&&!hasUser(e)&&
 !e.closest('[data-message-author-role="user"],[data-testid="user-message"]')).slice(-1);
}
else {
 // For historical ChatGPT replies, a roleless "ChatGPT said" heading may
 // live INSIDE a verified turn container, not beside the answer body.
 const h=[...document.querySelectorAll('main h4,main h5,main h6,[role="main"] h4,[role="main"] h5,[role="main"] h6,#thread h4,#thread h5')].filter(e=>e.isConnected&&/^ChatGPT said\s*[:：]?$/.test(norm(e.textContent)));
 if(!h.length){
  // The explicit URL-only req_diag_ path permits bounded STRUCTURAL
  // evidence, never a verified assistant reply or API delivery result.
  if(!/^req_diag_[A-Za-z0-9_-]{6,100}$/.test(expected))
    return {ok:false,error:'no-verified-assistant-boundary'};
  const main=document.querySelector('main,[role="main"]');
  const tables=[...document.querySelectorAll('main table,main [role="table"],[role="main"] table')]
    .filter(e=>e.isConnected&&visible(e)).slice(0,200);
  if(!main||tables.length<3)return {ok:false,error:'no-verified-assistant-boundary'};
  let candidate=tables[0].parentElement;
  while(candidate&&tables.some(e=>!candidate.contains(e)))candidate=candidate.parentElement;
  let depth=0,ancestor=candidate;
  while(ancestor&&ancestor!==main&&depth<55){ancestor=ancestor.parentElement;depth++;}
  const headings=candidate?.querySelectorAll('h1,h2,h3,h4,h5,h6')||[];
  const paragraphs=candidate?.querySelectorAll('p')||[];
  const text=String(candidate?.innerText||'');
  const unsafe='form,textarea,input,[contenteditable="true"],[role="textbox"],nav,aside,header,footer,[role="dialog"],[data-message-author-role="user"],[data-testid="user-message"]';
  const userHeading=[...headings].some(e=>/^(?:You said|你说|你发送)\s*[:：]?$/i.test(norm(e.textContent)));
  const safe=!!candidate&&candidate!==main&&ancestor===main&&
    !candidate.matches(unsafe)&&
    !candidate.closest('nav,aside,header,footer,[role="dialog"],[data-message-author-role="user"]')&&
    !candidate.querySelector(unsafe)&&!hasUser(candidate)&&!userHeading&&
    tables.every(e=>candidate.contains(e))&&depth>=3&&depth<55&&
    candidate.children.length>=4&&headings.length>=2&&
    paragraphs.length>=10&&text.length>=600&&text.length<=250000&&
    candidate.getBoundingClientRect().height>=500&&visible(candidate);
  if(!safe)return {ok:false,error:'no-verified-assistant-boundary'};
  roots=[candidate];scope='inferred-rich-table-candidate';boundaryVerified=false;
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
return {ok:!reason,reason,version:1,scope,boundary_verified:boundaryVerified,
 boundary_evidence:scope==='inferred-rich-table-candidate'?{
  table_count:roots[0].querySelectorAll('table,[role="table"]').length,
  heading_count:roots[0].querySelectorAll('h1,h2,h3,h4,h5,h6').length,
  paragraph_count:roots[0].querySelectorAll('p').length,
  excluded_composer:true,excluded_user_markers:true,manual_url_only:true}:null,
 request_marker:tagged?expected:null,
 counts:counters,rect:{x:Math.max(0,Math.floor(left)),y:Math.max(0,Math.floor(top)),w:Math.ceil(right-left),h:Math.ceil(bottom-top)},
 image_rects:imageRects,root:{tag:'assistant-reply',children},
 generating:!!document.querySelector('button[aria-label*="Stop generating"],button[aria-label*="Stop response"]')};
})"""

# Installed in the dedicated diagnostic tab before fresh navigation or reload.
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
// A single shared table ancestor is NOT automatically an assistant boundary.
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
    if (probe.get("body_visible_tables") or
        probe.get("body_markdown_blocks") or
        probe.get("existing_tab_visible_content_candidates")) and not (
        probe.get("assistant_roles") or probe.get("conversation_turns")):
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
    # skeleton-only is NOT evidence that browser navigation is still loading.
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
    # Preserve the last harmless structural page probe even if the error
    # handler subsequently changes the stage to failed.
    if "page_probe" not in data:
        try:
            previous=json.loads((folder/"status.json").read_text())
            if isinstance(previous.get("page_probe"),dict):
                data["page_probe"]=previous["page_probe"]
        except (ValueError,OSError,TypeError):pass
    tmp=folder/"status.tmp";tmp.write_text(json.dumps(data));tmp.replace(folder/"status.json")
    # A bounded, versioned operational timeline travels with the diagnostic ZIP.
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
    hints=recovery_hints()

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
                    if (result and result.get("ok")
                        and result.get("root",{}).get("children")
                        and not result.get("generating")):
                        verified.append((tab,result))
            except Exception:
                continue
        evidence={"existing_tab_readonly_checked":True,
            "existing_tab_candidates":len(matches),
            "existing_tab_verified":len(verified),
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
        nonlocal last_probe
        safe_url(url)
        status(folder,"opening-new-tab",15,open_mode="dedicated-worker-tab")
        # For one-off recovery create a blank page, instrument before the
        # first ChatGPT navigation, then navigate only this self-created tab.
        # The normal path is preserved for backwards compatibility.
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
        suspicious_redirect_streak=0
        transient_redirect_count=0
        existing_evidence={}
        for i in range(62):
            await asyncio.sleep(1 if i else 0.4)
            # Do not discard a redirected tab just because it moved to
            # about:blank, an error route, or an authentication endpoint.
            all_tabs=json_get(args.cdp_port,"/json/list")
            page=next((t for t in all_tabs if t.get("id")==tab_id
                       and t.get("type")=="page"),None)
            if not page:
                last_probe={"navigation_route":"target-not-visible",
                    "capture_error":"target-not-visible","attempt":i+1}
            else:
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
                            # incidental skeleton markup stays in the DOM:
                            # a verified assistant reply always wins.
                            last_probe={**last_probe,**probe,
                                        "navigation_route":kind,
                                        "attempt":i+1,
                                        "reload_attempted":reload_attempted,
                                        "reload_result":reload_result}
                            if obj and obj.get("root",{}).get("children"):
                                return page,obj,tab_id
                            # 429 may be caused by historical page traffic or
                            # this diagnostic. Avoid automatic reload/extra fetch.
                            if probe.get("fetch_http_recent_429") or probe.get("conversation_api_recent_429"):
                                last_probe["rate_limit_detected"]=True
                                break
                            # A dedicated tab may be skeleton-only while an
                            # operator-visible tab with this exact history URL
                            # has fully rendered the assistant reply. Inspect
                            # the latter read-only (no reload or injection).
                            if target_url and i in (9,32):
                                alternate,reply,evidence=await read_existing_history_tab(
                                    url,tab_id)
                                existing_evidence.update(evidence)
                                if alternate and reply:
                                    return alternate,reply,tab_id
                            # A single, non-destructive recovery attempt:
                            # only reload OUR diagnostic tab when its own
                            # page is complete, visible, skeleton-only and
                            # has no assistant/user turns after ~25 seconds.
                            # This never navigates another Worker/API tab,
                            # enters credentials or sends model prompts.
                            if (allow_reload and i>=24 and not reload_attempted
                                and kind=="conversation"
                                and probe.get("ready_state")=="complete"
                                and probe.get("visibility_state")=="visible"
                                and probe.get("loading_skeletons")
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
                                    # This script is registered on OUR new
                                    # tab only and sees no error message/body.
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
                "suspicious_redirect_streak":suspicious_redirect_streak}
            if i==0 or (i+1)%3==0 or i==61:
                status(folder,"awaiting-assistant",min(44,19+i//3),
                       page_probe=last_probe,tab_created=True,
                       retry_count=int(reload_attempted),attempt=i+1)
            # Access gates are evidence of a redirect; do not repeatedly
            # probe the login page or attempt any sign-in action.
            if access_observed>=3:break
            # Do not fail on a one-poll transient after the diagnostic tab
            # reload. Persistent untrusted off-origin navigation is different:
            # stop after the bounded grace and repeated confirmations.
            if (last_probe.get("navigation_route")=="unexpected-redirect"
                and suspicious_redirect_streak>=4
                and (not reload_attempted or
                     i-post_reload_at>15)):break
        return None,None,tab_id

    if not picked and target_url:
        # Existing exact-URL tabs are both safer and more diagnostically useful
        # than repeatedly creating/reloading more copies of the same page.
        # If they have no verified answer, stop with their structural evidence.
        existing,found,evidence=await read_existing_history_tab(target_url,"")
        observation_rounds=1
        # Poll only existing DOM state. Never reload or request conversation
        # again. An old Resource Timing 429 does not abort this observation.
        if not found and evidence["existing_tab_candidates"]:
            for _ in range(2):
                if evidence.get("existing_tab_recent_429"):
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
                status(folder,"page-unavailable",44,page_probe=last_probe)
                raise RuntimeError(failure_reason(last_probe))
        else:
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
            archive.writestr(dom_filename,json.dumps(report,ensure_ascii=False))
            rect=report["rect"];screens=0
            # Bounded screenshot strips of assistant reply, no sidebar or user prompt.
            if rect["w"]>0 and rect["h"]>0:
                for y in range(rect["y"],rect["y"]+min(rect["h"],1600*24),1600):
                    if screens>=24:break
                    clip={"x":rect["x"],"y":y,"width":min(3000,rect["w"]),
                          "height":min(1600,rect["y"]+rect["h"]-y),"scale":1}
                    result=await cdp.call("Page.captureScreenshot",{"format":"png","fromSurface":True,"captureBeyondViewport":True,"clip":clip})
                    prefix="assistant" if boundary_verified else "unverified_candidate"
                    archive.writestr(f"screenshots/{prefix}_{screens:02d}.png",
                        base64.b64decode(result["data"]))
                    screens+=1
                    status(folder,"capturing-screenshots",45+int(40*screens/min(24,max(1,(rect["h"]+1599)//1600))),screenshots=screens)
                    if path.stat().st_size>MAX_ZIP:raise RuntimeError("evidence_zip_size_limit")
            archive.writestr("manifest.json",json.dumps({"tool":"native-page-diagnostic",
                "version":VERSION,"request_id":args.request_id,"worker_id":args.worker_id,
                "scope":report["scope"],"counts":report["counts"],
                "boundary_verified":boundary_verified,
                "completeness_verified":boundary_verified and not bool(report.get("reason")),
                "artifact_kind":("verified-assistant" if boundary_verified
                    else "unverified-rich-candidate"),"dom_file":dom_filename,
                "screenshot_count":screens,"truncated":bool(report.get("reason")),
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
           artifact_kind=("verified-assistant" if report.get("boundary_verified") is not False
               else "unverified-rich-candidate"))
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
        # Even an older Controller that omits page_probe from its response
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
