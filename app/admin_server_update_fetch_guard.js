(()=>{
  if(window.__chat2apiServerUpdateFetchGuard)return;
  window.__chat2apiServerUpdateFetchGuard=true;

  const nativeFetch=window.fetch.bind(window);
  const POLL_TIMEOUT_MS=3000;

  function isServerUpdateStatusRequest(input){
    let raw="";
    if(typeof input==="string")raw=input;
    else if(input&&typeof input.url==="string")raw=input.url;
    if(!raw)return false;
    try{
      const url=new URL(raw,window.location.href);
      return url.origin===window.location.origin&&url.pathname==="/api/admin/server-update/status";
    }catch(_error){
      return false;
    }
  }

  window.fetch=function chat2apiFetchWithUpdatePollTimeout(input,init){
    if(!isServerUpdateStatusRequest(input))return nativeFetch(input,init);
    const options=init?{...init}:{};
    if(options.signal)return nativeFetch(input,options);

    const controller=new AbortController();
    const timer=window.setTimeout(()=>controller.abort(),POLL_TIMEOUT_MS);
    options.signal=controller.signal;
    return nativeFetch(input,options).finally(()=>window.clearTimeout(timer));
  };
})();
