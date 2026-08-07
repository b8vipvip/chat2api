(() => {
  const VERSION = "0.3.5";
  const CACHE_KEY = "chat2api:model-state:v1";
  const AUTO_KEY = "__CHAT2API_MODEL_AUTOMATION__";
  const REASONING = {
    instant: ["极速", "instant", "fast"],
    medium: ["中", "medium"],
    high: ["高", "high"],
    auto: ["智能", "自动", "auto", "automatic"],
  };
  const FAMILIES = ["gpt-5.6-sol", "gpt-5.5", "gpt-5.3", "o3"];

  const normalize = value => String(value || "").replace(/[✓✔︎✔√]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const visible = el => { if (!el) return false; const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden"; };
  const label = el => String(el?.getAttribute?.("aria-label") || el?.innerText || el?.textContent || "").replace(/\s+/g," ").trim();

  function requestedParts(model) {
    const id = normalize(model);
    if (!id || id === "default" || id === "chatgpt-web") return { isDefault:true, family:"", reasoning:"" };
    for (const family of FAMILIES.sort((a,b)=>b.length-a.length)) {
      if (id === family) return { isDefault:false, family, reasoning:"" };
      if (id.startsWith(family + "-")) return { isDefault:false, family, reasoning:id.slice(family.length+1) };
    }
    return { isDefault:false, family:id, reasoning:"" };
  }

  function composerRoot(){return [...document.querySelectorAll("form[data-type='unified-composer'], form")].find(el=>visible(el)&&el.querySelector("#prompt-textarea,[contenteditable='true'],textarea"))||null;}
  function pill(){const root=composerRoot(); if(!root)return null; return [...root.querySelectorAll("button")].filter(visible).map(el=>({el,text:label(el),cls:String(el.className||"")})).sort((a,b)=>(/composer-pill/i.test(b.cls)?100:0)-(/composer-pill/i.test(a.cls)?100:0))[0]?.el||null;}
  function reasoningFromLabel(value){const n=normalize(value); for(const [id,aliases] of Object.entries(REASONING)){if(aliases.some(a=>n===normalize(a)||n.startsWith(normalize(a)+" ")))return id;} return "";}
  function readCache(){try{return JSON.parse(sessionStorage.getItem(CACHE_KEY)||"null")||{};}catch(_){return {};}}
  function writeCache(value){try{sessionStorage.setItem(CACHE_KEY,JSON.stringify({...value,updated_at:Date.now()}));}catch(_){}}
  function actualModel(family,reasoning){return family ? `${family}${reasoning?`-${reasoning}`:""}` : "default";}

  function probe(requestedModel="default"){
    const started=performance.now(); const parts=requestedParts(requestedModel); const cached=readCache(); const currentReasoning=reasoningFromLabel(label(pill())) || cached.reasoning || "";
    const family=!cached.dirty ? (cached.family||"") : "";
    const familyMatch=!parts.family || family===parts.family;
    const reasoningMatch=!parts.reasoning || currentReasoning===parts.reasoning;
    const trusted=Boolean(!cached.dirty && family);
    return {
      router_version:VERSION, requested_model:requestedModel, requested_family:parts.family||null, requested_reasoning:parts.reasoning||null,
      actual_family:family||null, actual_reasoning:currentReasoning||null, actual_model:actualModel(family,currentReasoning),
      family_match:familyMatch, reasoning_match:reasoningMatch, cache_trusted:trusted, dirty:Boolean(cached.dirty),
      zero_op:parts.isDefault || (familyMatch&&reasoningMatch&&trusted), state_source:parts.isDefault?"composer-default":(trusted?"session-cache+composer":"composer+untrusted-cache"),
      state_detect_ms:Math.round((performance.now()-started)*10)/10,
    };
  }

  document.addEventListener("click", event=>{
    if(globalThis[AUTO_KEY])return; const root=composerRoot(); const target=event.target?.closest?.("button"); if(!root||!target||!root.contains(target))return;
    const text=normalize(`${label(target)} ${target.className||""}`); if(/composer-pill|高级|模型|model|极速|智能|中|高/.test(text)){const c=readCache(); writeCache({...c,dirty:true,dirty_reason:"manual-composer-control"});}
  },true);

  chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
    if(message.type==="chat2api.model.probe.v6"){sendResponse({ok:true,data:probe(message.model)});return false;}
    if(message.type==="chat2api.model.automation.v6"){globalThis[AUTO_KEY]=Boolean(message.active);sendResponse({ok:true});return false;}
    if(message.type==="chat2api.model.commit.v6"){
      const p=requestedParts(message.model); const currentReasoning=reasoningFromLabel(label(pill()))||p.reasoning||"";
      writeCache({family:p.family||message.family||"",reasoning:currentReasoning||message.reasoning||"",dirty:false});
      sendResponse({ok:true,data:probe(message.model)});return false;
    }
    if(message.type==="chat2api.model.invalidate.v6"){const c=readCache();writeCache({...c,dirty:true,dirty_reason:message.reason||"invalidated"});sendResponse({ok:true});return false;}
    return false;
  });
})();
