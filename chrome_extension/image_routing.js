(() => {
  const baseHandleServerMessage = handleServerMessage;
  const IMAGES_URL = "https://chatgpt.com/images/";

  async function ensureImageController(tabId) {
    try { const response=await chrome.tabs.sendMessage(tabId,{type:"chat2api.image.ping"}); if(response?.ok)return; } catch(_) {}
    await chrome.scripting.executeScript({target:{tabId},files:["content_multimodal.js","content_image.js"]});
    await sleep(200);
    const response=await chrome.tabs.sendMessage(tabId,{type:"chat2api.image.ping"});
    if(!response?.ok)throw new Error("ChatGPT Images controller did not respond");
  }

  async function imageTab() {
    const tabs=await chrome.tabs.query({url:["https://chatgpt.com/images/*","https://www.chatgpt.com/images/*"]});
    let tab=tabs.find(item=>Number.isInteger(item.id));
    if(!tab){tab=await chrome.tabs.create({url:IMAGES_URL,active:true});}
    if(!tab?.id)throw new Error("Chrome did not create the ChatGPT Images tab");
    const deadline=Date.now()+30000;let lastError=null;
    while(Date.now()<deadline){
      try{const current=await chrome.tabs.get(tab.id);if((current.url||current.pendingUrl||"").includes("/images")){await ensureImageController(tab.id);return current;}}catch(error){lastError=error;}
      await sleep(250);
    }
    throw lastError||new Error("Timed out waiting for ChatGPT Images");
  }

  async function prepareReferences(tabId,attachments){
    if(!Array.isArray(attachments)||!attachments.length)return{};
    const response=await chrome.tabs.sendMessage(tabId,{type:"chat2api.attach.prepare",attachments});
    if(!response?.ok)throw new Error(response?.error||"Unable to attach reference files on ChatGPT Images");
    return response.data||{};
  }

  handleServerMessage = async function handleImageRouting(message) {
    if(message.type!=="image.request"&&message.type!=="image.cancel")return baseHandleServerMessage(message);
    if(message.type==="image.cancel"){
      try{const tab=await imageTab();await chrome.tabs.sendMessage(tab.id,{type:"chat2api.image.cancel",requestId:message.request_id});}
      catch(error){await trySendSocket({type:"image.cancelled",request_id:message.request_id,reason:String(error?.message||error)});}
      return;
    }
    const started=Date.now();
    try{
      const tab=await imageTab();
      const refs=await prepareReferences(tab.id,message.attachments||[]);
      const diagnostics={route:"chatgpt-images",tab_id:tab.id,tab_ready_ms:Date.now()-started,...refs};
      await trySendSocket({type:"image.diagnostics",request_id:message.request_id,diagnostics});
      const response=await chrome.tabs.sendMessage(tab.id,{type:"chat2api.image.request",requestId:message.request_id,prompt:message.prompt,options:message.options||{}});
      if(!response?.ok)throw new Error(response?.error||"ChatGPT Images rejected the request");
    }catch(error){await trySendSocket({type:"image.error",request_id:message.request_id,error:String(error?.message||error)});}
  };
})();
