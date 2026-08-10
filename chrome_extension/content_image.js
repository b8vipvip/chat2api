(() => {
  const KEY = "__CHAT2API_IMAGE_CONTROLLER_V1__";
  if (globalThis[KEY]) return;
  const state = { active: null };
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const visible = el => { if (!el) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden"; };

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (error) { console.warn("chat2api image event failed", error); }
  }

  function composer() {
    return [...document.querySelectorAll("#prompt-textarea, textarea, [contenteditable='true'][data-lexical-editor='true']")].find(visible) || null;
  }
  function setText(el, text) {
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    const selection = getSelection(); const range = document.createRange(); range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range);
    document.execCommand("insertText", false, text);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }
  function sendButton() {
    const root = composer()?.closest("form") || document;
    return [...root.querySelectorAll("button")].find(button => {
      if (!visible(button) || button.disabled) return false;
      const label = `${button.getAttribute("aria-label")||""} ${button.getAttribute("data-testid")||""} ${button.innerText||""}`.toLowerCase();
      return /send|submit|发送|生成/.test(label) || button.type === "submit";
    }) || null;
  }
  async function waitForComposer(timeout=30000) {
    const end=Date.now()+timeout;
    while(Date.now()<end){const el=composer();if(el)return el;await delay(200);} throw new Error("ChatGPT Images composer did not become ready");
  }
  function imageCandidates() {
    return [...document.querySelectorAll("img[src]")].filter(img => {
      const r=img.getBoundingClientRect();
      const src=img.currentSrc||img.src||"";
      if(!src || /avatar|emoji|icon/i.test(src)) return false;
      return r.width>=140 && r.height>=140;
    });
  }
  async function imageToBase64(img) {
    const src=img.currentSrc||img.src||"";
    try {
      const response=await fetch(src);
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob=await response.blob();
      const buffer=await blob.arrayBuffer();
      const bytes=new Uint8Array(buffer);let binary="";const chunk=0x8000;
      for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,i+chunk));
      return {b64_json:btoa(binary),mime_type:blob.type||"image/png",url:src,width:img.naturalWidth||0,height:img.naturalHeight||0};
    } catch (error) {
      return {url:src,mime_type:"image/png",width:img.naturalWidth||0,height:img.naturalHeight||0,capture_error:String(error?.message||error)};
    }
  }

  async function run(message) {
    if(state.active) throw new Error("ChatGPT Images is already processing another request");
    const prompt=String(message.prompt||"").trim(); if(!prompt)throw new Error("Image prompt is empty");
    const active={requestId:message.requestId,cancelled:false}; state.active=active;
    const started=performance.now();
    try {
      const input=await waitForComposer();
      const baseline=new Set(imageCandidates().map(img=>img.currentSrc||img.src));
      setText(input,prompt); await delay(250);
      const button=sendButton();
      if(button)button.click();else input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",code:"Enter",keyCode:13,which:13,bubbles:true}));
      await emit({type:"image.started",request_id:active.requestId,diagnostics:{images_page:true,submit_ms:Math.round(performance.now()-started)}});
      const timeout=Math.max(30000,Number(message.options?.timeout_seconds||300)*1000); const end=Date.now()+timeout;
      let lastProgress=0;
      while(!active.cancelled&&Date.now()<end){
        const candidates=imageCandidates().filter(img=>!baseline.has(img.currentSrc||img.src));
        const complete=candidates.find(img=>img.complete&&(img.naturalWidth||0)>=256&&(img.naturalHeight||0)>=256);
        if(complete){
          const captured=await imageToBase64(complete);
          await emit({type:"image.completed",request_id:active.requestId,images:[captured],diagnostics:{images_page:true,capture_ms:Math.round(performance.now()-started),capture_error:captured.capture_error||null}});
          return;
        }
        if(Date.now()-lastProgress>5000){lastProgress=Date.now();await emit({type:"image.progress",request_id:active.requestId,stage:"generating",elapsed_ms:Math.round(performance.now()-started)});}
        await delay(500);
      }
      if(active.cancelled) await emit({type:"image.cancelled",request_id:active.requestId,reason:"Cancelled"});
      else await emit({type:"image.error",request_id:active.requestId,error:"Timed out waiting for a generated image on ChatGPT Images"});
    } finally { if(state.active===active)state.active=null; }
  }

  const listener=(message,_sender,sendResponse)=>{
    if(message.type==="chat2api.image.ping"){sendResponse({ok:true});return false;}
    if(message.type==="chat2api.image.request"){
      run(message).catch(error=>emit({type:"image.error",request_id:message.requestId,error:String(error?.message||error)}));
      sendResponse({ok:true});return false;
    }
    if(message.type==="chat2api.image.cancel"){
      if(state.active&&state.active.requestId===message.requestId)state.active.cancelled=true;
      sendResponse({ok:true});return false;
    }
    return false;
  };
  chrome.runtime.onMessage.addListener(listener);
  globalThis[KEY]={state};
})();
