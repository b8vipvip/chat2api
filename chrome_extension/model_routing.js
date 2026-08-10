(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseDiscoverModels = discoverModels;
  const DEFAULT_MODEL_IDS = new Set(["default", "chatgpt-web", ""]);

  async function sendCachedExtensionStatus() {
    const settings = await config(); const tabs = await chatTabs(); let bound = null;
    if (Number.isInteger(settings.boundTabId)) bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
    return trySendSocket({type:"extension.status",metadata:{extension_version:chrome.runtime.getManifest().version,tab_count:tabs.length,bound_tab_id:bound?.id||null,bound_url:bound?.url||"",bound_title:bound?.title||"",models:Array.isArray(settings.models)?settings.models:[],current_model:settings.currentModel||"default",capabilities:["text","vision","file-understanding","image-generation","model-selection","diagnostics","estimated-token-usage"]}});
  }
  sendExtensionStatus = async function(){ return sendCachedExtensionStatus(); };
  async function persistSelectedModel(data,requestedModel){const settings=await config();const models=Array.isArray(data?.models)&&data.models.length?data.models:(settings.models||[]);const currentModel=data?.current_model||data?.actual_model||requestedModel||"default";await chrome.storage.local.set({models,currentModel,modelsUpdatedAt:Date.now(),lastRequestedModel:requestedModel||"default",lastModelSelectionError:"",modelRouterVersion:data?.router_version||"0.3.5",modelSelectionStrategy:data?.selection_strategy||"",lastModelDiagnostics:data||{}});await sendCachedExtensionStatus();}
  async function sendWithScript(tabId,message,files){try{const response=await chrome.tabs.sendMessage(tabId,message);if(response)return response;}catch(_){}await chrome.scripting.executeScript({target:{tabId},files});await sleep(160);return chrome.tabs.sendMessage(tabId,message);}
  const probeState=(tabId,model)=>sendWithScript(tabId,{type:"chat2api.model.probe.v6",model},["content_model_v6.js"]);
  const setAutomation=(tabId,active)=>sendWithScript(tabId,{type:"chat2api.model.automation.v6",active},["content_model_v6.js"]);
  const commitState=(tabId,model)=>sendWithScript(tabId,{type:"chat2api.model.commit.v6",model},["content_model_v6.js"]);
  const sendModelPrepare=(tabId,model)=>sendWithScript(tabId,{type:"chat2api.model.prepare.v5",model},["content_model_v5.js"]);
  const sendModelDiscover=tabId=>sendWithScript(tabId,{type:"chat2api.models.discover.v5"},["content_model_v5.js"]);

  discoverModels = async function discoverModelsHybrid(tab,force=false){const settings=await config();if(!force&&settings.modelsUpdatedAt&&Date.now()-Number(settings.modelsUpdatedAt)<300000)return{models:settings.models||[],current_model:settings.currentModel||"default"};try{await ensureContent(tab.id);const response=await sendModelDiscover(tab.id);if(!response?.ok)throw new Error(response?.error||"Model discovery failed");await persistSelectedModel(response.data||{},response.data?.current_model||"default");return response.data||{};}catch(error){await chrome.storage.local.set({lastModelSelectionError:String(error?.message||error)});return baseDiscoverModels(tab,force);}};

  async function prepareRequestedModel(tab,requestedModel){
    const totalStarted=Date.now();const model=String(requestedModel||"default").trim().toLowerCase()||"default";const probeStarted=Date.now();const probe=await probeState(tab.id,model);const before=probe?.data||{};const stateDetectMs=Date.now()-probeStarted;
    if(DEFAULT_MODEL_IDS.has(model)){const diagnostics={...before,requested_model:model||"default",zero_op:true,model_switched:false,reasoning_switched:false,selection_strategy:"default-no-ui",state_detect_ms:before.state_detect_ms??stateDetectMs,model_selection_ms:0,model_prepare_ms:Date.now()-totalStarted};await chrome.storage.local.set({lastRequestedModel:model||"default",lastModelSelectionError:"",modelSelectionStrategy:"default-no-ui",lastModelDiagnostics:diagnostics});return{model:"default",prepared:false,executionModel:"chatgpt-web",diagnostics};}
    if(probe?.ok&&before.zero_op){const diagnostics={...before,requested_model:model,zero_op:true,model_switched:false,reasoning_switched:false,selection_strategy:"state-match-zero-op",state_detect_ms:before.state_detect_ms??stateDetectMs,model_selection_ms:0,model_prepare_ms:Date.now()-totalStarted};await persistSelectedModel(diagnostics,model);return{model,prepared:false,executionModel:"chatgpt-web",data:diagnostics,diagnostics};}
    const requestedFamily=before.requested_family||model.split("-").slice(0,model.startsWith("gpt-5.6-sol")?3:2).join("-");const requestedReasoning=before.requested_reasoning||null;const selectionStarted=Date.now();await setAutomation(tab.id,true);let response;try{response=await sendModelPrepare(tab.id,model);}finally{await setAutomation(tab.id,false).catch(()=>{});}if(!response?.ok)throw new Error(response?.error||`Unable to select requested ChatGPT model: ${model}`);const committed=await commitState(tab.id,model);const after=committed?.data||{};
    const diagnostics={...(response.data||{}),...after,requested_model:model,requested_family:after.requested_family||requestedFamily,requested_reasoning:after.requested_reasoning||requestedReasoning,zero_op:false,model_switched:!before.actual_family||before.actual_family!==after.actual_family,reasoning_switched:Boolean(requestedReasoning)&&before.actual_reasoning!==after.actual_reasoning,state_source:after.state_source||"post-selection-commit",state_detect_ms:before.state_detect_ms??stateDetectMs,model_selection_ms:Date.now()-selectionStarted,model_prepare_ms:Date.now()-totalStarted,selection_strategy:(response.data||{}).selection_strategy||"hybrid-v5+state-v6"};await persistSelectedModel(diagnostics,model);return{model,prepared:true,executionModel:"chatgpt-web",data:diagnostics,diagnostics};
  }

  async function prepareAttachments(tabId, attachments) {
    if (!Array.isArray(attachments) || !attachments.length) return {};
    const response = await sendWithScript(tabId,{type:"chat2api.attach.prepare",attachments},["content_multimodal.js"]);
    if (!response?.ok) throw new Error(response?.error || "Unable to attach files to ChatGPT");
    return response.data || {};
  }

  handleServerMessage = async function handleRequestDrivenModelRouting(message){
    if(message.type!=="chat.request")return baseHandleServerMessage(message);
    const requestedModel=String(message.options?.model||"default").trim()||"default";const routingStarted=Date.now();
    try{const tab=await resolveTargetTab();const tabReadyMs=Date.now()-routingStarted;await ensureContent(tab.id);const prepared=await prepareRequestedModel(tab,requestedModel);const attachmentDiagnostics=await prepareAttachments(tab.id,message.attachments||[]);const diagnostics={...(prepared.diagnostics||{}),...attachmentDiagnostics,tab_ready_ms:tabReadyMs,routing_ms:Date.now()-routingStarted,tab_id:tab.id};
      await trySendSocket({type:"chat.diagnostics",request_id:message.request_id,diagnostics});
      const response=await chrome.tabs.sendMessage(tab.id,{type:"chat2api.request",requestId:message.request_id,prompt:message.prompt,options:{...(message.options||{}),model:prepared.executionModel,requested_model:requestedModel,model_prepared:prepared.prepared,model_selection_strategy:diagnostics.selection_strategy,chat2api_diagnostics:diagnostics}});if(!response?.ok)throw new Error(response?.error||"ChatGPT tab rejected the request");
    }catch(error){const text=String(error?.message||error);await chrome.storage.local.set({lastRequestedModel:requestedModel,lastModelSelectionError:text});await trySendSocket({type:"chat.error",request_id:message.request_id,error:text});}
  };
})();
