/* Windows Chrome-extension native page diagnosis: read-only, request-terminal independent.
 * The Linux Python/CDP collector is intentionally not executed on Windows.
 */
(() => {
  "use strict";
  const KEY="__CHAT2API_WINDOWS_NATIVE_PAGE_DIAG_V1__";
  if(globalThis[KEY])return;
  const VERSION="windows-dom-1.1.0";
  const URL_RE=/^https:\/\/(?:chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)\/c\/[A-Za-z0-9-]{12,90}\/?$/;
  const JOB_RE=/^[0-9a-f]{32}$/;
  const jobs=new Set();
  // Shared, ephemeral tab lease: tab supervisor and physical window observers
  // must not reclaim or count the diagnostic's own temporary ChatGPT tab.
  const TAB_LEASE_KEY="__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__";
  const diagnosticTabs=globalThis[TAB_LEASE_KEY] || new Set();
  globalThis[TAB_LEASE_KEY]=diagnosticTabs;
  const sleep=ms=>new Promise(done=>setTimeout(done,ms));
  function validUrl(text) {
    if(!URL_RE.test(String(text||"")))return false;
    try{const u=new URL(text);return !u.search&&!u.hash&&!u.username&&!u.password;}catch(_){return false;}
  }
  // Runs in the chosen ChatGPT tab's isolated world; no source HTML,
  // event handlers, URL tokens, editable drafts or user message text exported.
  function capturePage() {
    const now=new Date().toISOString();
    const visible=node=>{
      try{const r=node.getBoundingClientRect(),s=getComputedStyle(node);
        return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden";
      }catch(_){return false;}
    };
    const USER="[data-message-author-role='user'],[data-conversation-role='user'],[data-testid='user-message']";
    const SKIP="script,style,form,textarea,input,button,nav,aside,header,footer,[contenteditable='true'],[role='textbox'],[role='dialog'],[role='menu']";
    const selections=[];
    for(const heading of document.querySelectorAll("h4[data-conversation-role='assistant']")){
      const wrap=heading.closest("[data-chatgpt-search-message-ids]");
      if(!wrap||wrap.querySelector("h4[data-conversation-role='user']"))continue;
      const list=String(wrap.getAttribute("data-chatgpt-search-message-ids")||"").split(/\s+/);
      for(const sel of wrap.querySelectorAll("[data-chatgpt-selection-message-id]")){
        const id=String(sel.getAttribute("data-chatgpt-selection-message-id")||"");
        if(!id||!list.includes(id)||!visible(sel)||sel.querySelector(USER))continue;
        const md=sel.querySelector("[data-markdown-text-style='assistant-message']");
        if(!md||!visible(md))continue;
        const dil=md.querySelector("[data-dil-message-id]");
        const source=md.querySelector("[data-dil-source-message-id]");
        if(dil&&dil.getAttribute("data-dil-message-id")!==id||
            source&&source.getAttribute("data-dil-source-message-id")!==id)continue;
        selections.push({node:sel,id});
      }
    }
    for(const role of document.querySelectorAll("[data-message-author-role='assistant']")){
      if(!visible(role)||role.querySelector(USER))continue;
      if(selections.some(s=>s.node===role||s.node.contains(role)))continue;
      selections.push({node:role,id:String(role.getAttribute("data-message-id")||"")});
    }
    const target=selections[selections.length-1];
    // A stable DOM is not necessarily a finished response. A generation may
    // pause without changing node/text counts for multiple polling intervals.
    const generationActive=Array.from(document.querySelectorAll(
      "[data-testid='stop-button'],[data-testid='composer-stop-button'],"+
      "[data-testid='stop-generating-button'],"+
      "button[aria-label='Stop generating'],button[aria-label='停止生成'],"+
      "[data-conversation-role='assistant'][aria-busy='true']"
    )).some(visible);
    const probe={
      ready_state:document.readyState,assistant_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='assistant'],[data-message-author-role='assistant']").length,
      native_message_nodes:document.querySelectorAll("[data-chatgpt-selection-message-id]").length,
      user_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='user'],[data-message-author-role='user']").length,
      selected_assistant_count:selections.length,
      generation_active:generationActive,
      captured_at:now
    };
    if(!target)return {ok:false,stage:"no-safe-assistant-boundary",probe};
    const allowed=new Set("div p span strong em h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br math semantics mrow mi mo mn mfrac msub msup msubsup".split(" "));
    const cssKeys=["display","fontSize","fontWeight","lineHeight","color","backgroundColor",
      "textAlign","whiteSpace","padding","marginTop","marginBottom","marginLeft","marginRight",
      "width","height","gridTemplateColumns","gridTemplateRows","gap","borderWidth"];
    const safeCss=v=>typeof v==="string"&&v.length<100&&
      !/(?:url\s*\(|expression|javascript:|var\s*\(|calc\s*\(|[;<>{}])/i.test(v);
    let nodes=0,chars=0,images=0,tables=0,truncated=false;
    function walk(node,depth) {
      if(truncated||depth>64){truncated=true;return null;}
      if(node.nodeType===Node.TEXT_NODE){
        const value=String(node.nodeValue||"");
        if(!value.trim())return null;
        if(chars+value.length>150000){truncated=true;return null;}
        chars+=value.length;return {tag:"#text",text:value};
      }
      if(!(node instanceof Element)||!visible(node)||node.matches(SKIP)||node.matches(USER))return null;
      if(++nodes>12000){truncated=true;return null;}
      const tag=node.tagName.toLowerCase();
      const out={tag:allowed.has(tag)?tag:"div"};
      if(out.tag==="img"){
        images++;
        const box=node.getBoundingClientRect();
        out.image={natural_width:Math.max(0,Number(node.naturalWidth)||0),
          natural_height:Math.max(0,Number(node.naturalHeight)||0),
          display_width:Math.round(box.width*100)/100,
          display_height:Math.round(box.height*100)/100,
          loaded:node.complete===true};
      }else if(out.tag==="table"){tables++;}
      const computed=getComputedStyle(node);
      const styles={};
      for(const key of cssKeys){
        const v=computed[key];
        if(safeCss(v))styles[key]=v;
      }
      if(Object.keys(styles).length)out.style=styles;
      const children=[];
      for(const child of node.childNodes){const c=walk(child,depth+1);if(c)children.push(c);if(truncated)break;}
      if(children.length)out.children=children;
      return out;
    }
    const tree=walk(target.node,0);
    const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.1.0",
      boundary_verified:!!target.id&&!truncated,
      request_bound:false,complete:!truncated&&!generationActive,
      message_id:target.id,counts:{nodes,text_chars:chars,tables,images},
      probe,root:tree,limitations:["No full-page screenshot","No source image bytes","No API request identity validation"]};
    return {ok:true,answer};
  }
  async function send(message) {
    if(typeof trySendSocket!=="function"||!await trySendSocket(message))
      throw new Error("extension_socket_disconnected");
  }
  async function execute(job) {
    const jobId=String(job.job_id||"");
    if(!JOB_RE.test(jobId)||!validUrl(job.conversation_url))
      throw new Error("invalid_diagnostic_target");
    if(jobs.has(jobId))throw new Error("duplicate_job");
    jobs.add(jobId);
    const showTab=job.show_tab===true;
    const started=Date.now();
    let tab=null,previousActiveTabId=null,finished=null,windowId=null;
    const progress=async stage=>{
      const event={type:"native.page.windows.progress",job_id:jobId,stage,
        elapsed_ms:Math.min(180000,Math.max(0,Date.now()-started)),
        show_tab:showTab};
      if(Number.isInteger(tab?.id))event.tab_id=tab.id;
      if(Number.isInteger(windowId))event.window_id=windowId;
      // Status is best-effort; never allow UI telemetry to break evidence capture.
      try{await send(event);}catch(_){}
    };
    try {
      await progress("accepted");
      // Append only to an explicitly Worker-owned ChatGPT window. Do not
      // borrow arbitrary windows even when this is a foreground diagnosis.
      const tabs=(await chrome.tabs.query({url:[
        "https://chatgpt.com/*","https://www.chatgpt.com/*",
        "https://chat.openai.com/*"
      ]}).catch(()=>[])).filter(item=>Number.isInteger(item?.windowId)
        && Number.isInteger(item?.id)&&!diagnosticTabs.has(item.id));
      let boundTabId=null;
      try{
        const data=await chrome.storage?.local?.get?.({boundTabId:null});
        if(Number.isInteger(data?.boundTabId))boundTabId=data.boundTabId;
      }catch(_){}
      const router=globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
      const managedIds=new Set(Object.values(router?.routes || {})
        .map(route=>route?.tab_id).filter(Number.isInteger));
      const pool=globalThis.__CHAT2API_PERSISTENT_WINDOW_POOL_V132__;
      const readyPoolIds=new Set(Array.from(pool?.runtimeReadyTabs?.entries?.() || [])
        .filter(([,ready])=>ready===true).map(([id])=>id));
      const existing=tabs.find(item=>item.id===boundTabId)
        || tabs.find(item=>managedIds.has(item.id))
        || tabs.find(item=>readyPoolIds.has(item.id));
      if(!existing)throw new Error("worker_managed_chatgpt_tab_not_found");
      windowId=existing.windowId;
      await progress("worker_lookup");
      if(showTab){
        // Foreground mode is explicit opt-in, never permitted to hide a
        // currently routed API request's Chrome tab.
        const busy=Boolean(router?.activeRequests?.size)
          || Object.values(router?.routes||{}).some(route=>Boolean(route?.inflight_request_id))
          || Boolean(globalThis.__CHAT2API_CONVERSATION_WORKERS_V24__?.requestRoutes?.size);
        if(busy)throw new Error("worker_busy_visual_diagnosis");
        const active=await chrome.tabs.query({active:true,windowId}).catch(()=>[]);
        previousActiveTabId=active.find(item=>Number.isInteger(item?.id))?.id??null;
      }
      await progress("tab_opening");
      tab=await chrome.tabs.create({url:job.conversation_url,active:showTab,windowId});
      if(!Number.isInteger(tab?.id))throw new Error("independent_tab_not_created");
      diagnosticTabs.add(tab.id);
      await progress("tab_opened");
      if(showTab){
        // Opt-in foreground mode intentionally reveals the history tab. Do
        // not navigate or reload the previous Worker-owned tab.
        await chrome.windows?.update?.(windowId,{focused:true}).catch(()=>{});
      }
      const expectedPath=new URL(job.conversation_url).pathname.replace(/\/$/,"");
      let stable="",seen=0,best=null,pageReady=false,scanning=false;
      for(let attempt=0;attempt<60;attempt++){
        if(attempt===0||attempt===3||attempt===10||attempt===25)
          await progress("tab_loading");
        await sleep(attempt<2?1200:1800);
        const current=await chrome.tabs.get(tab.id).catch(()=>null);
        if(!current)throw new Error("diagnostic_tab_closed");
        if(current.status!=="complete")continue;
        let loaded;
        try{loaded=new URL(String(current.url||""));}catch(_){}
        if(!loaded||loaded.protocol!=="https:"||
            !["chatgpt.com","www.chatgpt.com","chat.openai.com"].includes(loaded.hostname)||
            loaded.pathname.replace(/\/$/,"")!==expectedPath)
          throw new Error("diagnostic_conversation_redirected");
        if(!pageReady){pageReady=true;await progress("page_ready");}
        if(!scanning||[10,25,45].includes(attempt)){
          scanning=true;await progress("assistant_scanning");
        }
        const result=await chrome.scripting.executeScript({
          target:{tabId:tab.id},func:capturePage
        }).catch(()=>null);
        const value=result?.[0]?.result;
        if(!value?.ok||!value.answer?.root){stable="";seen=0;continue;}
        const meta=value.answer;
        const bytesForFingerprint=new TextEncoder().encode(JSON.stringify(meta.root));
        const fingerprint=Array.from(new Uint8Array(await crypto.subtle.digest(
          "SHA-256",bytesForFingerprint))).map(v=>v.toString(16).padStart(2,"0")).join("")
          +"|"+meta.message_id+"|"+meta.complete;
        if(fingerprint===stable)seen++;else{stable=fingerprint;seen=1;}
        best=meta;
        if(seen>=3&&meta.complete&&meta.boundary_verified)break;
      }
      if(!best)throw new Error("no_safe_assistant_reply_after_wait");
      if(seen<3||!best.boundary_verified)best.complete=false;
      await progress("assistant_captured");
      const bytes=new TextEncoder().encode(JSON.stringify(best));
      if(bytes.byteLength>1500000)throw new Error("diagnostic_data_budget_exceeded");
      const digest=new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
      const sha=[...digest].map(x=>x.toString(16).padStart(2,"0")).join("");
      const binaryChunk=32768,total=Math.ceil(bytes.length/binaryChunk);
      await progress("uploading");
      for(let i=0;i<total;i++){
        const part=bytes.subarray(i*binaryChunk,(i+1)*binaryChunk);
        let raw="";
        for(let j=0;j<part.length;j+=8192)raw+=String.fromCharCode(...part.subarray(j,j+8192));
        await send({type:"native.page.windows.chunk",job_id:jobId,index:i,
          count:total,size:bytes.length,sha256:sha,data:btoa(raw)});
      }
      await progress("upload_complete");
      finished={type:"native.page.windows.finished",job_id:jobId,
        ok:true,version:VERSION,sha256:sha,size:bytes.length};
    }catch(e){
      finished={type:"native.page.windows.finished",job_id:jobId,
        ok:false,version:VERSION,error:String(e?.message||e).slice(0,120)};
      await progress("failed");
    }finally{
      if(Number.isInteger(tab?.id)){
        await chrome.tabs.remove(tab.id).catch(()=>{});
        diagnosticTabs.delete(tab.id);
        if(showTab&&Number.isInteger(previousActiveTabId))
          await chrome.tabs.update(previousActiveTabId,{active:true}).catch(()=>{});
        await progress("tab_closed");
      }
      try{if(finished)await send(finished);}catch(_){}
      jobs.delete(jobId);
    }
  }
  const previous=globalThis.handleServerMessage;
  const wrapped=async message=>{
    if(message?.type==="native.page.windows.start"){
      const jobId=String(message.job_id||"");
      if(JOB_RE.test(jobId)&&validUrl(message.conversation_url)){
        void execute({job_id:jobId,conversation_url:message.conversation_url,show_tab:message.show_tab===true});
      }
      return;
    }
    if(typeof previous==="function")return previous(message);
  };
  globalThis.handleServerMessage=wrapped;
  try{handleServerMessage=wrapped}catch(_){}
  globalThis[KEY]=Object.freeze({version:VERSION,execute,validUrl});
})();
