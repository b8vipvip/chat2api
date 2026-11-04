/* Windows Chrome-extension native page diagnosis: read-only, request-terminal independent.
 * The Linux Python/CDP collector is intentionally not executed on Windows.
 */
(() => {
  "use strict";
  const KEY="__CHAT2API_WINDOWS_NATIVE_PAGE_DIAG_V1__";
  if(globalThis[KEY])return;
  const VERSION="windows-dom-1.0.0";
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
    const probe={
      ready_state:document.readyState,assistant_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='assistant'],[data-message-author-role='assistant']").length,
      native_message_nodes:document.querySelectorAll("[data-chatgpt-selection-message-id]").length,
      user_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='user'],[data-message-author-role='user']").length,
      selected_assistant_count:selections.length,
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
    const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.0.0",
      boundary_verified:!!target.id&&!truncated,
      request_bound:false,complete:!truncated,
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
    let tab=null;
    try {
      // Append to an existing ChatGPT window without stealing focus or
      // changing any routed tab. Keep this new tab out of Worker pool quotas.
      const existing=(await chrome.tabs.query({url:[
        "https://chatgpt.com/*","https://www.chatgpt.com/*",
        "https://chat.openai.com/*"
      ]}).catch(()=>[])).find(item=>Number.isInteger(item?.windowId)
        && Number.isInteger(item?.id)&&!diagnosticTabs.has(item.id));
      const options={url:job.conversation_url,active:false};
      if(existing)options.windowId=existing.windowId;
      tab=await chrome.tabs.create(options);
      if(!Number.isInteger(tab?.id))throw new Error("independent_tab_not_created");
      diagnosticTabs.add(tab.id);
      let stable="",seen=0,best=null;
      for(let attempt=0;attempt<60;attempt++){
        await sleep(attempt<2?1200:1800);
        const current=await chrome.tabs.get(tab.id).catch(()=>null);
        if(!current)throw new Error("diagnostic_tab_closed");
        if(current.status!=="complete")continue;
        const result=await chrome.scripting.executeScript({
          target:{tabId:tab.id},func:capturePage
        }).catch(()=>null);
        const value=result?.[0]?.result;
        if(!value?.ok||!value.answer?.root){stable="";seen=0;continue;}
        const meta=value.answer;
        // Equal DOM counts do not mean equal content. Responses can stream
        // text updates in-place without adding or removing nodes.
        const bytesForFingerprint=new TextEncoder().encode(JSON.stringify(meta.root));
        const fingerprint=Array.from(new Uint8Array(await crypto.subtle.digest(
          "SHA-256",bytesForFingerprint))).map(v=>v.toString(16).padStart(2,"0")).join("")
          +"|"+meta.message_id+"|"+meta.complete;
        if(fingerprint===stable)seen++;else{stable=fingerprint;seen=1;}
        best=meta;
        if(seen>=3)break;
      }
      if(!best)throw new Error("no_safe_assistant_reply_after_wait");
      if(seen<3)best.complete=false;
      const bytes=new TextEncoder().encode(JSON.stringify(best));
      if(bytes.byteLength>1500000)throw new Error("diagnostic_data_budget_exceeded");
      const digest=new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
      const sha=[...digest].map(x=>x.toString(16).padStart(2,"0")).join("");
      const binaryChunk=32768,total=Math.ceil(bytes.length/binaryChunk);
      for(let i=0;i<total;i++){
        const part=bytes.subarray(i*binaryChunk,(i+1)*binaryChunk);
        let raw="";
        for(let j=0;j<part.length;j+=8192)raw+=String.fromCharCode(...part.subarray(j,j+8192));
        await send({type:"native.page.windows.chunk",job_id:jobId,index:i,
          count:total,size:bytes.length,sha256:sha,data:btoa(raw)});
      }
      await send({type:"native.page.windows.finished",job_id:jobId,
        ok:true,version:VERSION,sha256:sha,size:bytes.length});
    }catch(e){
      try{await send({type:"native.page.windows.finished",job_id:jobId,
        ok:false,version:VERSION,error:String(e?.message||e).slice(0,120)})}catch(_){}
    }finally{
      if(Number.isInteger(tab?.id)){
        await chrome.tabs.remove(tab.id).catch(()=>{});
        diagnosticTabs.delete(tab.id);
      }
      jobs.delete(jobId);
    }
  }
  const previous=globalThis.handleServerMessage;
  const wrapped=async message=>{
    if(message?.type==="native.page.windows.start"){
      const jobId=String(message.job_id||"");
      if(JOB_RE.test(jobId)&&validUrl(message.conversation_url)){
        void execute({job_id:jobId,conversation_url:message.conversation_url});
      }
      return;
    }
    if(typeof previous==="function")return previous(message);
  };
  globalThis.handleServerMessage=wrapped;
  try{handleServerMessage=wrapped}catch(_){}
  globalThis[KEY]=Object.freeze({version:VERSION,execute,validUrl});
})();
