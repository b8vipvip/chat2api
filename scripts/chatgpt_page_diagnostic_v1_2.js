/*
 * ChatGPT historical page diagnostic v1.2 (local-only, no dependencies)
 * Usage: Open https://chatgpt.com/c/... in Chrome, F12 > Console, paste ALL and Enter.
 * It downloads a ZIP containing the LAST visible assistant turn's safe DOM representation,
 * computed styles, image states before/after scrolling, timings, and environment metadata.
 * It does NOT send messages, fetch resources, upload data, capture cookies/tokens,
 * collect Network response bodies, or include full resource URLs.
 * CAUTION: the ZIP contains assistant reply text. Review it before sharing.
 */
(async function chatgptPageDiagnosticV11() {
  'use strict';
  const VERSION = '1.2.0';
  const CFG = { maxNodes: 16000, maxTextChars: 1200000, maxImages: 160,
    maxScrollImages: 24, scrollWaitMs: 700, settleWaitMs: 1200,
    maxOutputBytes: 24 * 1024 * 1024 };
  const started = Date.now();
  const warnings = [];
  const enc = new TextEncoder();
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const truncate = (s,n=300) => String(s == null ? '' : s).slice(0,n);
  const htmlEscape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const maskPath = () => {
    if (/^\/c\//.test(location.pathname)) return '/c/[conversation-id-redacted]';
    return '/[path-redacted]';
  };
  const shortError = err => truncate(err && err.message || err, 160);
  if (!/^(chatgpt\.com|chat\.openai\.com)$/.test(location.hostname)) {
    console.error('[chat2api diagnostic] Run only on chatgpt.com / chat.openai.com.');
    return;
  }
  if (!/^\/c\//.test(location.pathname)) warnings.push('Not a /c/ history page; assistant selection may be limited.');
  const safeUrlType = raw => {
    if (!raw) return 'none';
    const s = String(raw);
    if (s.startsWith('blob:')) return 'blob';
    if (s.startsWith('data:')) return 'data';
    try { const u = new URL(s, location.href); return u.origin === location.origin ? 'same-origin' : 'cross-origin'; }
    catch (_) { return 'other'; }
  };
  function queryCount(selector, root=document) { try { return root.querySelectorAll(selector).length; } catch (_) { return -1; } }
  function identifyRoot() {
    // Current ChatGPT pages may use native message-ID and heading anchors,
    // without data-message-author-role. Prove the selected ID belongs to the
    // assistant wrapper before collecting anything from that subtree.
    const native=[];
    const nativeHeadings=Array.from(document.querySelectorAll('h4[data-conversation-role="assistant"]'));
    for (const heading of nativeHeadings) {
      const wrap=heading.closest('[data-chatgpt-search-message-ids]');
      if (!wrap || wrap.querySelector('h4[data-conversation-role="user"]')) continue;
      const ids=new Set((wrap.getAttribute('data-chatgpt-search-message-ids')||'').split(/\s+/).filter(Boolean));
      for (const sel of wrap.querySelectorAll('[data-chatgpt-selection-message-id]')) {
        const id=sel.getAttribute('data-chatgpt-selection-message-id')||'';
        const markdown=sel.querySelector('[data-markdown-text-style="assistant-message"]');
        if (!id || !ids.has(id) || !markdown || sel.querySelector('[data-message-author-role="user"]')) continue;
        const dil=markdown.querySelector('[data-dil-message-id]');
        const source=markdown.querySelector('[data-dil-source-message-id]');
        if (dil && dil.getAttribute('data-dil-message-id')!==id) continue;
        if (source && source.getAttribute('data-dil-source-message-id')!==id) continue;
        native.push({el:sel,kind:'native-assistant-message-id',messageId:id});
      }
    }
    if (native.length) return {...native[native.length-1],candidates:native.length};
    const roleNodes = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
    if (roleNodes.length) {
      let item = roleNodes[roleNodes.length-1];
      const mid = item.getAttribute('data-message-id') || item.closest('[data-chatgpt-selection-message-id]')?.getAttribute('data-chatgpt-selection-message-id') || null;
      const turn = item.closest('[data-testid^="conversation-turn-"], article');
      if (turn && !turn.querySelector('[data-message-author-role="user"]')) item = turn;
      return {el:item, kind:'assistant-role', candidates:roleNodes.length,messageId:mid};
    }
    const fallback = Array.from(document.querySelectorAll('article')).filter(a =>
      a.getAttribute('data-message-author-role') === 'assistant' ||
      a.querySelector('[data-message-author-role="assistant"]'));
    if (fallback.length) return {el:fallback[fallback.length-1],kind:'assistant-article',
      candidates:fallback.length,messageId:null};
    return {el:null,kind:'none',candidates:0,messageId:null};
  }
  function getRect(el) {
    try {const r=el.getBoundingClientRect();
      return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)};
    } catch (_) {return null;}
  }
  const STYLE_KEYS = ['display','position','visibility','opacity','fontFamily','fontSize','fontWeight',
    'fontStyle','lineHeight','letterSpacing','textAlign','whiteSpace','overflowWrap','wordBreak',
    'color','backgroundColor','width','height','minWidth','maxWidth','boxSizing','objectFit',
    'borderCollapse','borderSpacing','borderTopWidth','borderRightWidth','borderBottomWidth',
    'borderLeftWidth','borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle',
    'borderTopColor','borderRightColor','borderBottomColor','borderLeftColor','paddingTop',
    'paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
    'gap','rowGap','columnGap','gridTemplateColumns','flexDirection','justifyContent','alignItems'];
  function styleSummary(el) {
    try { const cs = getComputedStyle(el); const out={};
      for (const k of STYLE_KEYS) {
        const v=truncate(cs[k]||'',210);
        if (v) out[k]=v;
      }
      return out;
    } catch (e) {return {error:shortError(e)};}
  }
  const images = [];
  function snapshotImage(img, index) {
    const raw = img.currentSrc || img.src || '';
    let resource = null;
    try {
      const matches = performance.getEntriesByType('resource').filter(e => e.name === raw);
      if (matches.length) {
        const e=matches[matches.length-1];
        resource={initiatorType:e.initiatorType||null,startTime:Math.round(e.startTime),duration:Math.round(e.duration),
          responseEnd:Math.round(e.responseEnd),responseStatus:Number.isInteger(e.responseStatus)&&e.responseStatus>=100&&e.responseStatus<=599?e.responseStatus:null,transferSize:e.transferSize||0,
          encodedBodySize:e.encodedBodySize||0,decodedBodySize:e.decodedBodySize||0,
          timingVisible:!!(e.responseEnd>0)};
      }
    } catch (_) {}
    const box=getRect(img);
    const inViewport=!!box && box.width>0 && box.height>0 && box.x+box.width>0 && box.y+box.height>0 && box.x<innerWidth && box.y<innerHeight;
    return {index,srcKind:safeUrlType(raw),hasSrc:!!img.getAttribute('src'),hasCurrentSrc:!!img.currentSrc,srcsetPresent:!!img.getAttribute('srcset'),inViewport,
      loading:img.loading||null,decoding:img.decoding||null,
      complete:!!img.complete,naturalWidth:img.naturalWidth||0,naturalHeight:img.naturalHeight||0,
      rendered:box,connected:img.isConnected,resource,
      alt:truncate(img.alt||'',200)};
  }
  function collectImageState(root,label) {
    return {phase:label,msSinceStart:Date.now()-started,states:
      Array.from(root?root.querySelectorAll('img'):[]).slice(0,CFG.maxImages).map(snapshotImage)};
  }
  function collectDOM(root) {
    let nodeCount=0,textChars=0,truncatedNodes=false,truncatedText=false;
    const SKIP_TAGS=new Set(['SCRIPT','STYLE','NOSCRIPT','IFRAME','FRAME','FORM','INPUT','TEXTAREA','SELECT','OPTION','VIDEO','AUDIO','SOURCE','CANVAS','OBJECT','EMBED']);
    const ALLOW_ATTRS=['role','aria-label','aria-hidden','colspan','rowspan','scope','dir','lang','title','data-testid','data-message-author-role','data-chatgpt-selection-message-id','data-dil-message-id','data-dil-source-message-id'];
    const allowedAttrValue=(k,v) => k==='data-testid'?truncate(v.replace(/\d{7,}/g,'[number]'),100):truncate(v,170);
    function walk(node,depth) {
      if (nodeCount>=CFG.maxNodes) {truncatedNodes=true;return null;}
      nodeCount++;
      if (node.nodeType===Node.TEXT_NODE) {
        let t=node.nodeValue||'';
        const remaining=Math.max(0,CFG.maxTextChars-textChars);
        if (t.length>remaining) {t=t.slice(0,remaining);truncatedText=true;}
        textChars+=t.length;
        return t?{type:'text',value:t}:null;
      }
      if (node.nodeType!==Node.ELEMENT_NODE) return null;
      const el=node,tag=el.tagName;
      if (SKIP_TAGS.has(tag)) return {type:'omitted',tag:tag.toLowerCase()};
      const attrs={};
      for (const k of ALLOW_ATTRS) {
        const v=el.getAttribute(k);
        if (v!==null) attrs[k]=allowedAttrValue(k,v);
      }
      if (tag==='IMG') {
        const i=images.indexOf(el);attrs['image-index']=i>=0?i:'not-indexed';
        attrs['src-kind']=safeUrlType(el.currentSrc||el.src);
      }
      if (tag==='A') attrs['href-kind']=safeUrlType(el.getAttribute('href'));
      const keepStyle=/^(DIV|P|SPAN|SECTION|ARTICLE|H[1-6]|TABLE|THEAD|TBODY|TFOOT|TR|TD|TH|PRE|CODE|UL|OL|LI|BLOCKQUOTE|IMG|A|BUTTON|SVG|MATH|FIGURE|FIGCAPTION|STRONG|EM|B|I)$/.test(tag);
      const out={type:'element',tag:tag.toLowerCase(),depth,attrs,rect:getRect(el)};
      if (keepStyle) out.computedStyle=styleSummary(el);
      if (tag==='IMG') out.imageIndex=images.indexOf(el);
      const children=[];
      for (const child of el.childNodes) {
        const part=walk(child,depth+1);if(part)children.push(part);
        if (nodeCount>=CFG.maxNodes || textChars>=CFG.maxTextChars) break;
      }
      if(children.length)out.children=children;
      return out;
    }
    return {root:root?walk(root,0):null,nodeCount,textChars,truncatedNodes,truncatedText,
      limits:{maxNodes:CFG.maxNodes,maxTextChars:CFG.maxTextChars}};
  }
  function collectStructure() {
    return {documentReadyState:document.readyState,visibilityState:document.visibilityState,
      navigationType:(performance.getEntriesByType('navigation')[0]||{}).type||null,
      assistantRoleNodes:queryCount('[data-message-author-role="assistant"], h4[data-conversation-role="assistant"]'),
      userRoleNodes:queryCount('[data-message-author-role="user"], h4[data-conversation-role="user"]'),
      conversationTurns:queryCount('[data-testid^="conversation-turn-"], [data-chatgpt-selection-message-id]'),
      mainLandmarks:queryCount('main'),imagesInDocument:queryCount('img'),
      loadingIndicators:queryCount('[aria-busy="true"], [role="progressbar"], [data-testid*="loading"]'),
      bodyChildElements:document.body?document.body.children.length:0,
      screen:{width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio||1},
      scroll:{x:window.scrollX,y:window.scrollY},
      online:navigator.onLine,
      elapsedMs:Date.now()-started};
  }
  function collectResources() {
    let all=[];
    try {all=performance.getEntriesByType('resource');} catch(_) {}
    const byType={};
    for (const e of all) {
      const k=truncate(e.initiatorType||'unknown',25);
      byType[k]=(byType[k]||0)+1;
    }
    const relevant=all.filter(e=>e.initiatorType==='img').slice(-180).map(e=>({
      type:'img',startMs:Math.round(e.startTime),durationMs:Math.round(e.duration),
      responseEndMs:Math.round(e.responseEnd),transferSize:e.transferSize||0,
      encodedBodySize:e.encodedBodySize||0,
      sourceKind:safeUrlType(e.name)
    }));
    return {total:all.length,byInitiatorType:byType,recentImageResources:relevant,
      caveat:'Cross-origin resource timing may report zeros even when an image loaded; no HTTP status or raw URL is collected.'};
  }
  async function scrollProbe(root) {
    if(!root)return {count:0,records:[]};
    const all=Array.from(root.querySelectorAll('img')).slice(0,CFG.maxScrollImages);
    const oldWindow={x:scrollX,y:scrollY};
    const scrollers=new Map();
    for (const img of all) {
      let p=img.parentElement;
      while(p && p!==document.body) {
        if ((p.scrollHeight>p.clientHeight+4 || p.scrollWidth>p.clientWidth+4) && !scrollers.has(p)) {
          scrollers.set(p,{top:p.scrollTop,left:p.scrollLeft});
        }
        p=p.parentElement;
      }
    }
    const records=[];
    try {
      for(let i=0;i<all.length;i++) {
        const img=all[i];const index=images.indexOf(img);
        const before=snapshotImage(img,index);
        if(img.isConnected) {try{img.scrollIntoView({behavior:'instant',block:'center',inline:'nearest'});}catch(_){}}
        await delay(CFG.scrollWaitMs);
        records.push({index,before,after:snapshotImage(img,index),elapsedMs:Date.now()-started});
      }
    } finally {
      for (const [el,pos] of scrollers) {try{el.scrollTop=pos.top;el.scrollLeft=pos.left;}catch(_){}}
      try{window.scrollTo({left:oldWindow.x,top:oldWindow.y,behavior:'instant'});}catch(_){window.scrollTo(oldWindow.x,oldWindow.y);}
    }
    return {count:records.length,totalImages:images.length,scrollWaitMs:CFG.scrollWaitMs,records};
  }
  // ZIP store method (no network, third-party libraries, or CompressionStream required).
  const crcTable=new Uint32Array(256);
  for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=(c&1)?(0xEDB88320^(c>>>1)):(c>>>1);crcTable[n]=c>>>0;}
  function crc32(arr){let c=0xFFFFFFFF;for(const b of arr)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xFFFFFFFF)>>>0;}
  function zipStore(entries){
    const locals=[],dirs=[];let offset=0;
    const u16=(dv,o,n)=>dv.setUint16(o,n,true),u32=(dv,o,n)=>dv.setUint32(o,n>>>0,true);
    for(const [name,content] of entries) {
      const nameBytes=enc.encode(name),data=enc.encode(content),crc=crc32(data);
      const lh=new Uint8Array(30+nameBytes.length),lv=new DataView(lh.buffer);
      u32(lv,0,0x04034b50);u16(lv,4,20);u16(lv,6,0x0800);u16(lv,8,0);
      u32(lv,14,crc);u32(lv,18,data.length);u32(lv,22,data.length);u16(lv,26,nameBytes.length);
      lh.set(nameBytes,30);locals.push(lh,data);
      const dh=new Uint8Array(46+nameBytes.length),dv=new DataView(dh.buffer);
      u32(dv,0,0x02014b50);u16(dv,4,20);u16(dv,6,20);u16(dv,8,0x0800);u16(dv,10,0);
      u32(dv,16,crc);u32(dv,20,data.length);u32(dv,24,data.length);
      u16(dv,28,nameBytes.length);u32(dv,42,offset);dh.set(nameBytes,46);
      dirs.push(dh);offset+=lh.length+data.length;
    }
    const dirSize=dirs.reduce((n,x)=>n+x.length,0),end=new Uint8Array(22),dv=new DataView(end.buffer);
    u32(dv,0,0x06054b50);u16(dv,8,entries.length);u16(dv,10,entries.length);u32(dv,12,dirSize);u32(dv,16,offset);
    return new Blob([...locals,...dirs,end],{type:'application/zip'});
  }
  let mutationCount=0,mutationKinds={};
  const chosen=identifyRoot(),root=chosen.el;
  if(!root)warnings.push('No verified assistant turn was found. Page hydration/loading or changed ChatGPT markup may be the cause.');
  images.push(...(root?Array.from(root.querySelectorAll('img')).slice(0,CFG.maxImages):[]));
  if(root && root.querySelectorAll('img').length>CFG.maxImages)warnings.push('Image cap reached; some images omitted.');
  const observer=new MutationObserver(ms=>{mutationCount+=ms.length;for(const m of ms)mutationKinds[m.type]=(mutationKinds[m.type]||0)+1;});
  try {observer.observe(root||document.body,{subtree:true,childList:true,attributes:true,characterData:true,attributeFilter:['src','srcset','class','style','aria-busy']});}
  catch(e){warnings.push('MutationObserver unavailable: '+shortError(e));}
  const before=collectImageState(root,'before-scroll');
  const initialStructure=collectStructure();
  const dom=collectDOM(root);
  console.info('[chat2api diagnostic] Assistant:',chosen.kind,'images:',images.length,'capturing local scroll probes...');
  const scroll=await scrollProbe(root);
  await delay(CFG.settleWaitMs);
  try{observer.disconnect();}catch(_){}
  const after=collectImageState(root,'after-scroll-and-settle');
  const finalStructure=collectStructure();
  const resources=collectResources();
  const countBad=phase=>phase.states.filter(s=>!s.complete||!s.naturalWidth).length;
  const summary={version:VERSION,capturedAt:new Date().toISOString(),site:location.hostname,
    route:maskPath(),durationMs:Date.now()-started,assistantSelection:chosen.kind,
    assistantCandidates:chosen.candidates,assistantMessageId:chosen.messageId||null,assistantElementFound:!!root,
    images:images.length,pendingImagesBefore:countBad(before),pendingImagesAfter:countBad(after),
    mutationCount,mutationKinds,initialStructure,finalStructure,
    completenessVerified:false,visualVerified:false,warnings,
    privacy:'Assistant reply text is included. Cookie, token, raw URL, network bodies, and other conversation turns are excluded by design.'};
  const json=(obj)=>JSON.stringify(obj,null,2);
  const readme=[
    'ChatGPT local page diagnostic '+VERSION,
    'This ZIP was generated locally by a JS snippet on a ChatGPT conversation history page.',
    'Nothing was uploaded by this script. Review all files before sharing.',
    'report.json: summary, counters, status and limitations.',
    'assistant_dom.json: selected last assistant turn, text content, safe attributes, layout/computed styles.',
    'image_evidence.json: image loading/size/geometry before+after local scrolling, timing if available.',
    'resource_evidence.json: resource timing summary (NO resource URLs).',
    'No raw image bytes, complete HAR, response bodies or full-page screenshots are captured.',
    'Do not interpret this as proof of pixel-perfect rendering or backend request ownership.'
  ].join('\n');
  let entries=[['README.txt',readme],['report.json',json(summary)],
    ['assistant_dom.json',json(dom)],['image_evidence.json',json({before,scroll,after})],
    ['resource_evidence.json',json(resources)]];
  let size=entries.reduce((n,[a,b])=>n+enc.encode(a).length+enc.encode(b).length+90,0);
  if(size>CFG.maxOutputBytes) {
    warnings.push('DOM payload exceeded 24 MiB output limit; DOM tree omitted, summary and image evidence retained.');
    entries=entries.filter(e=>e[0]!=='assistant_dom.json');
    entries[1]=['report.json',json(summary)];
    size=entries.reduce((n,[a,b])=>n+enc.encode(a).length+enc.encode(b).length+90,0);
  }
  if(size>CFG.maxOutputBytes){console.error('[chat2api diagnostic] Too much data even after trimming; nothing downloaded.');return;}
  const zip=zipStore(entries);
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  const filename=`chatgpt-page-evidence-${stamp}.zip`;
  const url=URL.createObjectURL(zip);
  const anchor=document.createElement('a');
  anchor.href=url;anchor.download=filename;anchor.style.display='none';document.body.appendChild(anchor);
  anchor.click();anchor.remove();
  // Keep the object URL available briefly so Chrome can finish saving large ZIPs.
  setTimeout(()=>URL.revokeObjectURL(url),120000);
  console.info('[chat2api diagnostic] Finished:',{filename,bytes:zip.size,
    assistant:summary.assistantSelection,images:summary.images,
    pendingBefore:summary.pendingImagesBefore,pendingAfter:summary.pendingImagesAfter,
    mutations:summary.mutationCount,warnings:summary.warnings});
})().catch(e=>console.error('[chat2api diagnostic] Failed:',e&&e.stack||String(e)));