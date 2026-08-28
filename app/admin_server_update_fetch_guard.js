(()=>{
  if(window.__chat2apiServerUpdateFetchGuard)return;
  window.__chat2apiServerUpdateFetchGuard=true;

  const nativeFetch=window.fetch.bind(window);
  const baseApi=typeof window.api==="function"?window.api:null;
  const POLL_TIMEOUT_MS=3000;
  let authReloadScheduled=false;

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

  function scheduleAuthRecovery(){
    if(authReloadScheduled)return;
    authReloadScheduled=true;
    const reconnect=document.getElementById("updReconnect");
    if(reconnect){
      reconnect.style.display="";
      reconnect.textContent="管理员登录会话已失效，正在自动重新加载登录界面；更新任务仍会在服务器后台继续。";
    }
    window.setTimeout(()=>window.location.reload(),350);
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

  // admin_v17 captures the browser's native fetch before this guard is injected,
  // and its global api() helper therefore bypasses a later window.fetch wrapper.
  // The server-update UI prefers api(), so wrap that helper as well and route only
  // the status endpoint through the guarded cookie-authenticated fetch above.
  if(baseApi&&!baseApi.__chat2apiServerUpdatePollGuard){
    const guardedApi=async function chat2apiApiWithUpdatePollGuard(path,opt={}){
      if(!isServerUpdateStatusRequest(path))return baseApi(path,opt);

      const headers=opt.body===undefined?{}:{"Content-Type":"application/json"};
      const response=await window.fetch(path,{
        method:opt.method||"GET",
        headers,
        body:opt.body===undefined?undefined:JSON.stringify(opt.body),
        credentials:"same-origin",
        cache:"no-store",
      });
      const text=await response.text();
      let data={};
      try{data=text?JSON.parse(text):{};}catch(_error){data={detail:text};}
      if(!response.ok){
        const error=new Error(data.detail||`${response.status} ${text}`);
        error.status=response.status;
        if(response.status===401||response.status===403)scheduleAuthRecovery();
        throw error;
      }
      return data;
    };
    guardedApi.__chat2apiServerUpdatePollGuard=true;
    window.api=guardedApi;
  }
})();
