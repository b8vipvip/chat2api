/* v210: correlate one verified current-reply download button with page Blob bytes.
 * No terminal decisions, no arbitrary page clicks, no external file URL exports.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_FILE_V210__";
  if (globalThis[KEY]) return;
  const KIND = "chat2api.native-file.v210";
  const MAX_BYTES = 6 * 1024 * 1024;
  const MAX_TOTAL = 12 * 1024 * 1024;
  const MIMES = Object.freeze({
    xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx:"application/vnd.openxmlformats-officedocument.presentationml.presentation",
    docx:"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    zip:"application/zip", json:"application/json", pdf:"application/pdf",
    csv:"text/csv", txt:"text/plain", md:"text/markdown",
  });
  const FILENAME = /([^\s\n<>:"\/\\|?*]{1,135}\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md))(?=$|[\s）),;])/ig;
  let session = null;
  const visible = node => {
    try {const rect=node.getBoundingClientRect(),css=getComputedStyle(node);
      return rect.width>0 && rect.height>0 && css.display!=="none" && css.visibility!=="hidden";}
    catch (_) {return false;}
  };
  function filenames(node) {
    const labels = [...(node?.querySelectorAll?.("a,[aria-label],[title]") || [])].slice(0,40)
      .map(el=>[el.getAttribute("aria-label"),el.getAttribute("title"),el.textContent].filter(Boolean).join(" "));
    labels.push(String(node?.innerText || node?.textContent || ""));
    return [...new Set(labels.flatMap(label =>
      [...String(label).slice(0,1800).matchAll(FILENAME)].map(m=>m[1])))].slice(0,10);
  }
  function matchButtonFilename(button) {
    let ancestor=button.parentElement;
    for(let depth=0;ancestor && depth<7;depth++,ancestor=ancestor.parentElement) {
      const names=filenames(ancestor);
      if(names.length===1) return names[0];
      if(names.length>1) return "";
    }
    return "";
  }
  function candidateButtons(scope) {
    if (!scope?.querySelectorAll) return [];
    return [...scope.querySelectorAll("button,[role='button']")].filter(button =>
      visible(button) && !button.disabled && button.getAttribute("aria-disabled")!=="true"
      && /(?:download|下载)/i.test([
        button.getAttribute("aria-label"), button.getAttribute("title"),
        button.getAttribute("data-testid"),button.textContent,
      ].filter(Boolean).join(" ").slice(0,180))
    ).map(button=>({button, filename:matchButtonFilename(button)}))
      .filter(item=>Boolean(item.filename) && MIMES[item.filename.split(".").pop().toLowerCase()]);
  }
  function signedBytes(filename,bytes) {
    const ext=filename.split(".").pop().toLowerCase();
    if (["xlsx","pptx","docx","zip"].includes(ext))
      return bytes[0]===80 && bytes[1]===75 && bytes[2]===3 && bytes[3]===4;
    if (ext==="pdf") return bytes[0]===37 && bytes[1]===80 && bytes[2]===68
      && bytes[3]===70 && bytes[4]===45;
    if (bytes.slice(0,Math.min(bytes.length,4096)).includes(0)) return false;
    if(ext==="json") {try{JSON.parse(new TextDecoder().decode(bytes));}catch(_){return false;}}
    return true;
  }
  function dataUrl(filename,base64,bytes) {
    const ext=filename.split(".").pop().toLowerCase();
    const mime=MIMES[ext];
    if (!mime || bytes<4 || bytes>MAX_BYTES || base64.length>4*Math.ceil(MAX_BYTES/3)) return null;
    try {
      const binary=atob(base64);
      if(binary.length!==bytes)return null;
      const data=Uint8Array.from(binary,c=>c.charCodeAt(0));
      if(!signedBytes(filename,data))return null;
    }catch(_){return null;}
    return "data:"+mime+";base64,"+base64;
  }
  function oneClick(button,filename) {
    return new Promise(resolve=>{
      const nonce=crypto.randomUUID();
      let done=false, started=false;
      const finish=(payload=null)=>{if(done)return;done=true;clearTimeout(timer);
        window.removeEventListener("message",onMessage);resolve(payload);};
      const onMessage=event=>{
        if(event.source!==window||event.origin!==location.origin
          ||event.data?.kind!==KIND||event.data.nonce!==nonce)return;
        if(event.data.direction==="armed"&&!started) {
          started=true;
          try{button.click();}catch(_){finish();}
        }else if(event.data.direction==="captured"){
          const name=String(event.data.filename||"");
          // The filename may be supplied by ChatGPT's native anchor.
          // An exact match protects against unrelated page download events.
          if(name!==filename){finish();return;}
          const bytes=Number(event.data.bytes||0);
          const url=dataUrl(filename,String(event.data.base64||""),bytes);
          finish(url?{filename,data_url:url,bytes}:null);
        }
      };
      window.addEventListener("message",onMessage);
      const timer=setTimeout(()=>finish(),7500);
      window.postMessage({kind:KIND,direction:"arm",nonce,filename},location.origin);
    });
  }
  async function capture(scope,requestId) {
    if (!requestId || !scope?.isConnected || !scope.closest?.("main,[role='main'],#thread")) 
      return {text:"",count:0,bytes:0,error:"unverified-reply-scope"};
    if(!session || session.requestId!==requestId)
      session={requestId,files:new Map(),attempts:new Map(),lastError:""};
    const candidates=candidateButtons(scope).slice(0,12);
    let total=[...session.files.values()].reduce((sum,file)=>sum+file.bytes,0);
    for(const item of candidates) {
      if(session.files.size>=4 || total>=MAX_TOTAL)break;
      if(session.files.has(item.filename))continue;
      const attempts=session.attempts.get(item.filename)||0;
      if(attempts.count>=2 || (attempts.count && Date.now()-attempts.at<25000))continue;
      session.attempts.set(item.filename,{at:Date.now(),count:(attempts.count||0)+1});
      const result=await oneClick(item.button,item.filename);
      if(!result){session.lastError="native-download-not-observed";continue;}
      if(total+result.bytes>MAX_TOTAL){session.lastError="file-total-limit";break;}
      session.files.set(result.filename,result);
      total+=result.bytes;
    }
    const files=[...session.files.values()];
    return {
      text:files.map(file=>"!["+"📎 "+file.filename.replace(/[\[\]\r\n]/g,"_")+"]("+file.data_url+")").join("\n\n"),
      count:files.length,bytes:total,error:session.lastError,
      button_candidates:candidates.length,attempted:session.attempts.size,
    };
  }
  function stats(requestId) {
    if(!session||session.requestId!==requestId)return {count:0,attempted:0,error:""};
    return {count:session.files.size,attempted:session.attempts.size,error:session.lastError};
  }
  globalThis[KEY]=Object.freeze({version:210,capture,stats});
})();