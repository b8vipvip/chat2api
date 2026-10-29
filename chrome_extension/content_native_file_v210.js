/* v210: correlate one verified current-reply download button with page Blob bytes.
 * No terminal decisions, no arbitrary page clicks, no external file URL exports.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_FILE_V210__";
  if (globalThis[KEY]) return;
  const KIND = "chat2api.native-file.v210";
  const MAX_BYTES = 12 * 1024 * 1024;
  const MAX_INLINE_BYTES = 6 * 1024 * 1024;
  const MAX_TOTAL = 24 * 1024 * 1024;
  const UPLOAD_CHUNK_BYTES = 96 * 1024;
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
    const result=[], seen=new Set();
    const nodes=[...scope.querySelectorAll("button,[role='button'],a[href],a[download]")];
    for (const button of nodes) {
      if (!visible(button) || button.disabled
          || button.getAttribute("aria-disabled")==="true" || seen.has(button)) continue;
      const filename=matchButtonFilename(button) || (
        filenames(button).length===1 ? filenames(button)[0] : "");
      if (!filename || !MIMES[filename.split(".").pop().toLowerCase()]) continue;
      const label=[
        button.getAttribute("aria-label"),button.getAttribute("title"),
        button.getAttribute("data-testid"),button.textContent,
      ].filter(Boolean).join(" ").slice(0,220);
      const isNativeDownload=/(?:download|下载)/i.test(label);
      const isAnchor=String(button.tagName||"").toLowerCase()==="a";
      // Redesigned Office file cards are often accessible links or an icon
      // whose action label is the filename, *not* "Download". Only allow
      // a link to the SAME ChatGPT origin or a browser blob: URL; never
      // click external redirect targets or unrelated navigation controls.
      let sameOriginFileLink=false;
      if (isAnchor) {
        const href=String(button.getAttribute("href")||"").trim();
        if (href && !/^(?:javascript|data|file):/i.test(href)) {
          try {
            const parsed=new URL(href,location.href);
            sameOriginFileLink=(parsed.origin===location.origin || parsed.protocol==="blob:")
              && (Boolean(button.getAttribute("download"))
                || /\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md)(?:[?#]|$)/i.test(parsed.pathname)
                || label.includes(filename));
          } catch (_) {}
        }
      }
      // Do not promote plain file-name buttons unless their semantic action
      // clearly denotes a file attachment rather than an editor/menu action.
      const isFileCardButton=!isAnchor && label.includes(filename)
        && Boolean(button.closest?.("[data-testid*='file'],[class*='file-card'],[class*='fileCard'],[data-state='closed']"));
      if (!isNativeDownload && !sameOriginFileLink && !isFileCardButton) continue;
      result.push({button,filename});
      seen.add(button);
    }
    return result;
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

  // If ChatGPT delivered three *verified binary files* but its final
  // bundle download was absent/unreadable, package exactly those originals.
  // This is a binary presentation fallback, not a generated-content claim,
  // and never grants completion authority to this helper.
  function crc32(bytes) {
    let crc=0xffffffff;
    for(const byte of bytes) {
      crc ^= byte;
      for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);
    }
    return (crc^0xffffffff)>>>0;
  }
  function binaryArchive(files) {
    const encoder=new TextEncoder(),chunks=[],records=[];
    let offset=0;
    const push=data=>{chunks.push(data);offset+=data.length;};
    const u16=(view,index,value)=>view.setUint16(index,value,true);
    const u32=(view,index,value)=>view.setUint32(index,value>>>0,true);
    for(const file of files) {
      const name=encoder.encode(file.filename),data=file.bytes;
      if(!name.length||name.length>1024||!data.length||data.length>MAX_BYTES)return null;
      const start=offset,crc=crc32(data),header=new Uint8Array(30+name.length);
      const v=new DataView(header.buffer);
      u32(v,0,0x04034b50);u16(v,4,20);u16(v,6,0x0800);u16(v,8,0);
      u32(v,14,crc);u32(v,18,data.length);u32(v,22,data.length);
      u16(v,26,name.length);
      header.set(name,30);push(header);push(data);
      records.push({name,data,crc,start});
    }
    const centralOffset=offset;
    for(const row of records) {
      const header=new Uint8Array(46+row.name.length),v=new DataView(header.buffer);
      u32(v,0,0x02014b50);u16(v,4,20);u16(v,6,20);u16(v,8,0x0800);
      u32(v,16,row.crc);u32(v,20,row.data.length);u32(v,24,row.data.length);
      u16(v,28,row.name.length);u32(v,42,row.start);
      header.set(row.name,46);push(header);
    }
    const centralSize=offset-centralOffset,ending=new Uint8Array(22),end=new DataView(ending.buffer);
    u32(end,0,0x06054b50);u16(end,8,records.length);u16(end,10,records.length);
    u32(end,12,centralSize);u32(end,16,centralOffset);push(ending);
    if(offset>MAX_BYTES)return null;
    const output=new Uint8Array(offset);
    let at=0;for(const chunk of chunks){output.set(chunk,at);at+=chunk.length;}
    return output;
  }
  function fallbackZip(session) {
    if(session.files.size!==3 || session.files.has("chat2api-artifacts.zip")
      || [...session.files.values()].some(file=>!file.data_url))return null;
    const byExt={};
    for(const file of session.files.values()) {
      const ext=file.filename.split(".").pop().toLowerCase();
      if(byExt[ext])return null;
      byExt[ext]=file;
    }
    if(!["xlsx","pptx","json"].every(ext=>Boolean(byExt[ext])))return null;
    const originals=["xlsx","pptx","json"].map(ext=>{
      const file=byExt[ext],base64=file.data_url.split("base64,")[1];
      if(!base64)return null;
      const decoded=atob(base64);
      return {filename:file.filename,bytes:Uint8Array.from(decoded,c=>c.charCodeAt(0))};
    });
    if(originals.some(item=>!item))return null;
    const readme=new TextEncoder().encode(
      "高校机器人公开赛交付文件\n"+
      "本 ZIP 由 chat2api 将本轮 ChatGPT 已生成并验证的 XLSX、PPTX、JSON 文件原样打包。\n"+
      "请先解压，再用对应的办公软件和 JSON 解析器打开；打包不改变原始文件内容。\n"
    );
    const bytes=binaryArchive([...originals,{filename:"README.txt",bytes:readme}]);
    if(!bytes)return null;
    let binary="";for(let i=0;i<bytes.length;i+=16384)
      binary+=String.fromCharCode(...bytes.subarray(i,i+16384));
    return {filename:"chat2api-artifacts.zip",data_url:dataUrl("chat2api-artifacts.zip",btoa(binary),bytes.length),
      bytes:bytes.length,source:"verified-originals-local-packaging"};
  }

  function oneClick(button,filename) {
    return new Promise(resolve=>{
      const nonce=crypto.randomUUID();
      let done=false, started=false, lastStage="no-native-download-event";
      const finish=(payload=null)=>{if(done)return;done=true;clearTimeout(timer);
        window.removeEventListener("message",onMessage);resolve(payload);};
      const onMessage=event=>{
        if(event.source!==window||event.origin!==location.origin
          ||event.data?.kind!==KIND||event.data.nonce!==nonce)return;
        if(event.data.direction==="probe") {
          const code=String(event.data.stage || "");
          if (/^(?:download|fetch|same-tab|new-blob)-[a-z-]{3,60}$/.test(code)) lastStage=code;
          return;
        }
        if(event.data.direction==="armed"&&!started) {
          started=true;
          try{button.click();}catch(_){finish();}
        }else if(event.data.direction==="captured"){
          const name=String(event.data.filename||"");
          // The filename may be supplied by ChatGPT's native anchor.
          // An exact match protects against unrelated page download events.
          if(name!==filename){finish({failure_stage:"download-filename-mismatch"});return;}
          const bytes=Number(event.data.bytes||0);
          const url=dataUrl(filename,String(event.data.base64||""),bytes);
          finish(url?{filename,data_url:url,bytes}:{failure_stage:"download-signature-invalid"});
        }
      };
      window.addEventListener("message",onMessage);
      const timer=setTimeout(()=>finish({failure_stage:lastStage}),9000);
      window.postMessage({kind:KIND,direction:"arm",nonce,filename},location.origin);
    });
  }
  async function stageLargeFile(file,requestId) {
    // A request-scoped verified file above 6 MiB travels as <=96 KiB chunks
    // over the *existing* authenticated Worker WebSocket. Never embed these
    // bytes in a completion event, telemetry row or long SSE response.
    if (file.bytes <= MAX_INLINE_BYTES) return file;
    if (typeof crypto?.subtle?.digest !== "function") return null;
    const binary = atob(String(file.data_url || "").split("base64,")[1] || "");
    if (binary.length !== file.bytes || file.bytes > MAX_BYTES) return null;
    const uploadId = crypto.randomUUID().replace(/-/g, "").toLowerCase();
    if (!/^[a-f0-9]{32}$/.test(uploadId)) return null;
    const raw = Uint8Array.from(binary,c=>c.charCodeAt(0));
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256",raw));
    const sha256 = [...hash].map(byte=>byte.toString(16).padStart(2,"0")).join("");
    const mime = MIMES[file.filename.split(".").pop().toLowerCase()];
    for(let offset=0,sequence=0;offset<binary.length;offset+=UPLOAD_CHUNK_BYTES,sequence++){
      const part=binary.slice(offset,offset+UPLOAD_CHUNK_BYTES);
      const result=await chrome.runtime.sendMessage({type:"chat2api.file.chunk",event:{
        type:"chat.file.chunk",request_id:requestId,
        upload_id:uploadId,filename:file.filename,mime,
        sequence,expected_bytes:file.bytes,expected_sha256:sha256,
        base64_chunk:btoa(part),final:offset+part.length===binary.length,
      }});
      if(!result?.ok) return null;
    }
    return {filename:file.filename,bytes:file.bytes,
      stage_marker:"chat2api-staged:"+uploadId,source:"worker-bounded-chunk-staging-v238"};
  }

  async function capture(scope,requestId) {
    if (!requestId || !scope?.isConnected || !scope.closest?.("main,[role='main'],#thread")) 
      return {text:"",count:0,bytes:0,error:"unverified-reply-scope"};
    if(!session || session.requestId!==requestId)
      session={requestId,files:new Map(),attempts:new Map(),lastError:"",
        failures:new Map(),failureStages:new Map()};
    const candidates=candidateButtons(scope).slice(0,12);
    let total=[...session.files.values()].reduce((sum,file)=>sum+file.bytes,0);
    for(const item of candidates) {
      if(session.files.size>=4 || total>=MAX_TOTAL)break;
      if(session.files.has(item.filename))continue;
      const attempts=session.attempts.get(item.filename)||0;
      // A file preview may become downloadable well after the first finished
      // reply toolbar. Exhausting two attempts in the first minute left
      // subsequent 10+ minutes of scans without another real capture attempt.
      // Retry sparingly across the long-running server request, and never
      // claim a generated file without validated bytes.
      if(attempts.count>=6 || (attempts.count && Date.now()-attempts.at<45000))continue;
      session.attempts.set(item.filename,{at:Date.now(),count:(attempts.count||0)+1});
      let result=await oneClick(item.button,item.filename);
      if(result?.data_url && result.bytes>MAX_INLINE_BYTES) {
        try { result=await stageLargeFile(result,requestId); }
        catch (_) { result=null; }
        if(!result) {
          session.lastError="native-chunk-upload-failed";
          session.failures.set(item.filename.split(".").pop().toLowerCase(),"native-chunk-upload-failed");
          session.failureStages.set(item.filename.split(".").pop().toLowerCase(),"chunk-transport-unavailable");
          continue;
        }
      }
      if(!result?.data_url && !result?.stage_marker){
        session.lastError="native-download-not-observed";
        const ext=item.filename.split(".").pop().toLowerCase();
        session.failures.set(ext,"native-download-not-observed");
        session.failureStages.set(ext,String(result?.failure_stage || "no-native-download-event").slice(0,80));
        continue;
      }
      if(total+result.bytes>MAX_TOTAL){session.lastError="file-total-limit";break;}
      session.files.set(result.filename,result);
      session.failures.delete(item.filename.split(".").pop().toLowerCase());
      session.failureStages.delete(item.filename.split(".").pop().toLowerCase());
      total+=result.bytes;
    }
    // Never manufacture a bundle for missing/invalid originals or while
    // files are still being captured. A real ZIP always takes precedence.
    const three=[...session.files.keys()].filter(name=>/\.(?:xlsx|pptx|json)$/i.test(name));
    if(three.length===3 && !session.firstThreeAt) session.firstThreeAt=Date.now();
    if(session.firstThreeAt && Date.now()-session.firstThreeAt>=30000
        && ![...session.files.keys()].some(name=>/\.zip$/i.test(name))) {
      const packed=fallbackZip(session);
      if(packed?.data_url && total+packed.bytes<=MAX_TOTAL) {
        session.files.set(packed.filename,packed);
        session.synthesizedZip=true;
        total+=packed.bytes;
        session.failures.delete("zip");
        session.lastError="";
      }
    }
    const files=[...session.files.values()];
    return {
      text:files.map(file=>"!["+"📎 "+file.filename.replace(/[\[\]\r\n]/g,"_")+"]("+
        (file.stage_marker || file.data_url)+")").join("\n\n"),
      count:files.length,bytes:total,error:session.lastError,
      button_candidates:candidates.length,attempted:session.attempts.size,
      attempt_counts:Object.fromEntries([...session.attempts.entries()].map(([name,entry])=>[name.split(".").pop().toLowerCase(),entry.count])),
      captured_types:files.map(file=>file.filename.split(".").pop().toLowerCase()),
      failed_types:[...session.failures.keys()],
      failure_stages:Object.fromEntries(session.failureStages),
      synthesized_zip:session.synthesizedZip===true,
    };
  }
  function stats(requestId) {
    if(!session||session.requestId!==requestId)return {count:0,attempted:0,error:""};
    return {count:session.files.size,attempted:session.attempts.size,error:session.lastError,
      captured_types:[...session.files.keys()].map(name=>name.split(".").pop().toLowerCase()),
      failed_types:[...session.failures.keys()],
      failure_stages:Object.fromEntries(session.failureStages)};
  }
  globalThis[KEY]=Object.freeze({version:210,capture,stats});
})();