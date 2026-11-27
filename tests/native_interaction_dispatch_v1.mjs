import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(
  new URL("../chrome_extension/conversation_dispatch.js", import.meta.url),
  "utf8",
);
const listeners=[];
const sent=[];
const socketEvents=[];
const removed=[];
const storageData={};
const tab={id:17,windowId:4,url:"https://chatgpt.com/"};
const context={
  chrome:{
    tabs:{
      get:async id=>id===tab.id?tab:null,
      sendMessage:async (id,message)=>{
        sent.push({id,message});
        return {ok:true,revision:Number(message.revision)+1};
      },
      onRemoved:{addListener:fn=>removed.push(fn)},
    },
    runtime:{onMessage:{addListener:fn=>listeners.push(fn)}},
    storage:{local:{set:async value=>Object.assign(storageData,value),get:async()=>({nativeRequestTabsV1:storageData.nativeRequestTabsV1})}},
  },
  isChatGptUrl:url=>String(url).startsWith("https://chatgpt.com/"),
  trySendSocket:async event=>{socketEvents.push(event);return true;},
  resolveTargetTab:async()=>tab,
  resolveTargetTabForRequest:async()=>tab,
  handleServerMessage:async message=>({handled:true,type:message.type}),
};
vm.createContext(context);
vm.runInContext(source,context,{filename:"conversation_dispatch.js"});
const requestId="req_native_dispatch_1234";
const dispatched=await context.handleServerMessage({
  type:"chat.request",
  request_id:requestId,
  routing:{api_key_id:"user-key"},
});
assert.equal(dispatched.handled,true);
const state=context.__CHAT2API_CONVERSATION_DISPATCH_V1__;
assert.equal(state.nativeRequestTabs.get(requestId).tabId,tab.id);
assert.equal(state.requestTabs.get(requestId).tabId,tab.id);

listeners[0]({
  type:"chat2api.event",
  event:{type:"chat.completed",request_id:requestId},
});
assert.equal(state.requestTabs.has(requestId),false);
assert.equal(state.nativeRequestTabs.has(requestId),true,
  "completed requests retain a bounded native action route");

assert.ok(Array.isArray(storageData.nativeRequestTabsV1));
assert.equal(storageData.nativeRequestTabsV1[0][0],requestId);

// Simulate an MV3 service-worker restart: the new background context has no
// in-memory route map, so it must restore only the bounded local tab binding.
const sentAfterRestart=[];
const socketEventsAfterRestart=[];
const removedAfterRestart=[];
const restartedContext={
  chrome:{
    tabs:{
      get:async id=>id===tab.id?tab:null,
      sendMessage:async (id,message)=>{
        sentAfterRestart.push({id,message});
        return {ok:true,revision:Number(message.revision)+1};
      },
      onRemoved:{addListener:fn=>removedAfterRestart.push(fn)},
    },
    runtime:{onMessage:{addListener:fn=>listeners.push(fn)}},
    storage:{local:{set:async value=>Object.assign(storageData,value),get:async()=>({nativeRequestTabsV1:storageData.nativeRequestTabsV1})}},
  },
  isChatGptUrl:url=>String(url).startsWith("https://chatgpt.com/"),
  trySendSocket:async event=>{socketEventsAfterRestart.push(event);return true;},
  resolveTargetTab:async()=>tab,
  resolveTargetTabForRequest:async()=>tab,
  handleServerMessage:async message=>({handled:true,type:message.type}),
};
vm.createContext(restartedContext);
vm.runInContext(source,restartedContext,{filename:"conversation_dispatch-restarted.js"});
const restartedState=restartedContext.__CHAT2API_CONVERSATION_DISPATCH_V1__;
assert.equal(restartedState.nativeRequestTabs.size,0);
const actionId="a".repeat(32);
const result=await restartedContext.handleServerMessage({
  type:"chat.native.interaction",
  request_id:requestId,
  action_id:actionId,
  revision:3,
  interaction_id:"nc_3_1",
  action:"set_checked",
  value:true,
});
assert.equal(result.ok,true);
assert.equal(restartedState.nativeRequestTabs.get(requestId).tabId,tab.id);
assert.equal(sentAfterRestart.length,1);
assert.equal(sentAfterRestart[0].id,tab.id);
assert.equal(sentAfterRestart[0].message.type,"chat2api.native.interaction");
assert.equal(socketEventsAfterRestart.length,1);
assert.equal(socketEventsAfterRestart[0].type,"chat.native.interaction.ack");
assert.equal(socketEventsAfterRestart[0].request_id,requestId);
assert.equal(socketEventsAfterRestart[0].action_id,actionId);
assert.equal(socketEventsAfterRestart[0].revision,3);
assert.equal(socketEventsAfterRestart[0].ok,true);

removedAfterRestart[0](tab.id);
assert.equal(restartedState.nativeRequestTabs.has(requestId),false);
assert.equal(storageData.nativeRequestTabsV1.length,0);
console.log("native-interaction-original-worker-tab-dispatch-passed");
