/* Windows Chrome-extension native page diagnosis: read-only, request-terminal independent.
 * The Linux Python/CDP collector is intentionally not executed on Windows.
 */
(() => {
  "use strict";
  const KEY="__CHAT2API_WINDOWS_NATIVE_PAGE_DIAG_V1__";
  if(globalThis[KEY])return;
  const VERSION="windows-dom-1.1.2";
  const URL_RE=/^https:\/\/(?:chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)\/c\/[A-Za-z0-9-]{12,90}\/?$/;
  const JOB_RE=/^[0-9a-f]{32}$/;
  const jobs=new Set();
  // Shared, ephemeral tab lease: tab supervisor and physical window observers
  // must not reclaim or count the diagnostic's own temporary ChatGPT tab.
  const TAB_LEASE_KEY="__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__";
  const diagnosticTabs=globalThis[TAB_LEASE_KEY] || new Set();
  globalThis[TAB_LEASE_KEY]=diagnosticTabs;
  const sleep=ms=>new Promise(done=>setTimeout(done,ms));
  const HOT_TOOL_STORAGE_KEY="chat2apiWindowsNativePageToolV1";
  const HOT_TOOL_VERSION=/^[0-9]+\.[0-9]+\.[0-9]+$/;
  const HOT_TOOL_SHA=/^[a-f0-9]{64}$/;
  const HOT_TOOL_LIMIT=120000;
  let cachedTool=null,toolLoaded=false;
  const sha256=async bytes=>{
    const hash=new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
    return [...hash].map(value=>value.toString(16).padStart(2,"0")).join("");
  };
  function compareVersions(left,right){
    const a=left.split(".").map(Number),b=right.split(".").map(Number);
    for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]>b[i]?1:-1;}
    return 0;
  }
  async function readTool(){
    if(toolLoaded)return cachedTool;
    const stored=await chrome.storage.local.get(HOT_TOOL_STORAGE_KEY).catch(()=>({}));
    const value=stored?.[HOT_TOOL_STORAGE_KEY];
    if(value&&HOT_TOOL_VERSION.test(value.version)&&HOT_TOOL_SHA.test(value.sha256)
      &&typeof value.source==="string"&&value.source.length<=HOT_TOOL_LIMIT
      &&value.source.includes('const DIAGNOSTIC_TOOL_VERSION="'+value.version+'";')
      &&value.source.trimEnd().endsWith("})()")
      &&await sha256(new TextEncoder().encode(value.source))===value.sha256){
      cachedTool=value;
    }
    toolLoaded=true;
    return cachedTool;
  }
  async function toolStatus(){
    const installed=await readTool();
    let userScriptsReady=false;
    try{
      if(typeof chrome.userScripts?.execute==="function"){
        await chrome.userScripts.getScripts();
        userScriptsReady=true;
      }
    }catch(_){}
    return {version:installed?.version||"",sha256:installed?.sha256||"",
      ready:userScriptsReady};
  }
  async function installTool(message){
    const id=String(message?.update_id||"");
    let finished={type:"native.page.windows.tool.updated",update_id:id,ok:false,
      error:"invalid_tool_install_request"};
    try{
      if(!JOB_RE.test(id)||jobs.size)throw new Error("diagnostic_busy_or_update_id_invalid");
      const source=message.source,version=message.version,digest=message.sha256;
      if(typeof source!=="string"||source.length>HOT_TOOL_LIMIT||source.length<100
        ||typeof version!=="string"||!HOT_TOOL_VERSION.test(version)
        ||typeof digest!=="string"||!HOT_TOOL_SHA.test(digest)
        ||!source.includes('const DIAGNOSTIC_TOOL_VERSION="'+version+'";')
        ||!source.trimEnd().endsWith("})()"))
        throw new Error("invalid_verified_tool_payload");
      if(typeof chrome.userScripts?.execute!=="function")
        throw new Error("user_scripts_api_unavailable_chrome_135_required");
      try{await chrome.userScripts.getScripts();}
      catch(_){throw new Error("user_scripts_permission_disabled_enable_in_extension_details");}
      const raw=new TextEncoder().encode(source);
      if(raw.length>HOT_TOOL_LIMIT||await sha256(raw)!==digest)
        throw new Error("diagnostic_tool_sha256_mismatch");
      const previous=await readTool();
      if(previous){
        if(compareVersions(version,previous.version)<0)
          throw new Error("diagnostic_tool_downgrade_refused");
        if(version===previous.version&&digest!==previous.sha256)
          throw new Error("diagnostic_tool_version_must_increment");
      }
      const changed=!previous||previous.sha256!==digest;
      if(changed){
        const value={version,sha256:digest,source,installed_at:Date.now()};
        await chrome.storage.local.set({[HOT_TOOL_STORAGE_KEY]:value});
        cachedTool=value;
        toolLoaded=true;
      }
      finished={type:"native.page.windows.tool.updated",update_id:id,
        ok:true,version,sha256:digest,updated:changed};
    }catch(error){
      finished.error=String(error?.message||error).slice(0,120);
    }
    try{await send(finished);}catch(_){}
  }
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
    const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.1.2",
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
  // A service-worker restart restores persisted router.routes, but does not
  // restore the in-memory activeRequests map. A historical inflight marker by
  // itself is therefore not proof that an API request still has a live tab.
  // Only disregard provably orphaned markers; never mutate the request router
  // or clear a route here. Unknown Chrome errors always fail closed.
  async function visualDiagnosisBusy() {
    const router=globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    const workers=globalThis.__CHAT2API_CONVERSATION_WORKERS_V24__;
    if(router?.loaded===false||router?.activeRequests?.size||
        workers?.requestRoutes?.size)return true;
    const previous=Object.entries(router?.routes||{})
      .filter(([,route])=>Boolean(route?.inflight_request_id))
      .map(([key,route])=>({
        key,requestId:String(route.inflight_request_id),
        tabId:route.tab_id
      }));
    for(const item of previous){
      if(!Number.isInteger(item.tabId))return true;
      try{
        // Any successful lookup, even without expected metadata, is not
        // proof that this tab is gone. Keep the busy guard fail-closed.
        await chrome.tabs.get(item.tabId);
        return true;
      }catch(error){
        const message=String(error?.message||error);
        if(!/^No tab with id(?::|\s)/i.test(message))return true;
      }
    }
    // A request may arrive while Chrome resolves the tab probes. Only proceed
    // if there are still no active claims and no new/replaced inflight leases.
    if(router?.loaded===false||router?.activeRequests?.size||
        workers?.requestRoutes?.size)return true;
    const before=new Map(previous.map(item=>[item.key,item]));
    return Object.entries(router?.routes||{}).some(([key,route])=>{
      const inflight=String(route?.inflight_request_id||"");
      if(!inflight)return false;
      const original=before.get(key);
      return !original||original.requestId!==inflight||
        original.tabId!==route?.tab_id;
    });
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
      // Reuse a provably Worker-owned window if one still exists. An idle
      // Windows Worker may legitimately have *no* ChatGPT tab after cleanup;
      // never borrow an arbitrary user's tab as a fallback.
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
      windowId=existing?.windowId??null;
      await progress("worker_lookup");

      // Visible mode is explicitly opt-in. Never steal focus from a routed
      // request, even when no Worker-owned browser tab is currently present.
      if(showTab){
        if(await visualDiagnosisBusy())
          throw new Error("worker_busy_visual_diagnosis");
        if(Number.isInteger(windowId)){
          const active=await chrome.tabs.query({active:true,windowId}).catch(()=>[]);
          previousActiveTabId=active.find(item=>Number.isInteger(item?.id))?.id??null;
        }
      }
      await progress("tab_opening");
      if(existing){
        // Start with an inert blank tab; register its lease *before*
        // navigating to ChatGPT so the pool/supervisor never adopts it.
        tab=await chrome.tabs.create({url:"about:blank",active:showTab,windowId});
      }else{
        // No Worker-owned ChatGPT tab is not a fatal condition. Create a new
        // independent, same-profile Chrome window instead of borrowing tabs.
        // about:blank avoids a race with supervisor onCreated handlers.
        await progress("window_creating");
        const ownedWindow=await chrome.windows.create({
          url:"about:blank",focused:showTab,type:"normal"
        });
        if(!Number.isInteger(ownedWindow?.id))
          throw new Error("diagnostic_owned_window_not_created");
        windowId=ownedWindow.id;
        const fresh=Array.isArray(ownedWindow.tabs)?
          ownedWindow.tabs.find(item=>Number.isInteger(item?.id)):null;
        if(fresh)tab=fresh;
        else{
          const inWindow=await chrome.tabs.query({windowId});
          tab=(inWindow||[]).find(item=>Number.isInteger(item?.id)
            && (item.url==="about:blank"||item.pendingUrl==="about:blank"))||null;
        }
        await progress("window_created");
      }
      if(!Number.isInteger(tab?.id))
        throw new Error("independent_tab_not_created");
      diagnosticTabs.add(tab.id);
      // A purely blank, owned temporary tab is never a live API route.
      // Navigating only this tab preserves existing ChatGPT request windows.
      await chrome.tabs.update(tab.id,{url:job.conversation_url});
      await progress("tab_opened");
      if(showTab&&existing){
        try{await chrome.windows?.update?.(windowId,{focused:true});}catch(_){}
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
        const tool=await readTool();
        let result;
        if(tool){
          // Chrome MV3 permits downloaded code only through the documented
          // userScripts API. Never eval/importScripts remote sources.
          if(typeof chrome.userScripts?.execute!=="function")
            throw new Error("user_scripts_api_unavailable");
          try{
            result=await chrome.userScripts.execute({
              target:{tabId:tab.id},world:"USER_SCRIPT",
              js:[{code:tool.source}]
            });
          }catch(_){
            throw new Error("hot_collector_execution_failed_check_user_scripts_permission");
          }
        }else{
          result=await chrome.scripting.executeScript({
            target:{tabId:tab.id},func:capturePage
          }).catch(()=>null);
        }
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
        // An API request may arrive *after* the initial foreground idle
        // check. Never reactivate an old tab when a request took focus.
        let restore=false;
        if(showTab&&Number.isInteger(previousActiveTabId)){
          const routerNow=globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
          const busyNow=Boolean(routerNow?.activeRequests?.size)
            || Object.values(routerNow?.routes||{}).some(route=>Boolean(route?.inflight_request_id))
            || Boolean(globalThis.__CHAT2API_CONVERSATION_WORKERS_V24__?.requestRoutes?.size);
          const activeNow=await chrome.tabs.query({active:true,windowId}).catch(()=>[]);
          restore=!busyNow&&activeNow.some(item=>item?.id===tab.id);
        }
        await chrome.tabs.remove(tab.id).catch(()=>{});
        diagnosticTabs.delete(tab.id);
        if(restore)await chrome.tabs.update(previousActiveTabId,{active:true}).catch(()=>{});
        await progress("tab_closed");
      }
      try{if(finished)await send(finished);}catch(_){}
      jobs.delete(jobId);
    }
  }
  const previous=globalThis.handleServerMessage;
  const wrapped=async message=>{
    if(message?.type==="native.page.windows.tool.update"){
      void installTool(message);
      return;
    }
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
  globalThis[KEY]=Object.freeze({version:VERSION,execute,validUrl,
    getToolStatus:toolStatus,installTool});
})();
