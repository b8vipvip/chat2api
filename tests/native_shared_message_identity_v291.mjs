import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

// Synthetic DOM representative of the previously OBSERVED Windows 8-image,
// 3-table assistant sample. This is NOT exported private page text, an
// actual DOM screenshot, nor proof of pixel-for-pixel similarity.
class El {
  constructor(tag, attrs={}, children=[]) {
    this.nodeType=1;this.tagName=tag.toUpperCase();this.attrs={...attrs};
    this.childNodes=[];this.parentElement=null;this.isConnected=true;
    for(const child of children)this.append(child);
  }
  append(child){this.childNodes.push(child);child.parentElement=this;return child;}
  getAttribute(key){return Object.prototype.hasOwnProperty.call(this.attrs,key)
    ?String(this.attrs[key]):null;}
  contains(target){for(let n=target;n;n=n.parentElement)if(n===this)return true;return false;}
  matches(selector){
    return selector.split(",").some(raw=>{
      const s=raw.trim();
      const tag=s.match(/^[a-z][\w-]*/i)?.[0];
      if(tag&&tag.toUpperCase()!==this.tagName)return false;
      const attrs=[...s.matchAll(/\[([\w-]+)(?:([*^$~|]?=)['"]?([^'"\]]+)['"]?)?\]/g)];
      if(!tag&&!attrs.length)return false;
      return attrs.every(([,key,op,value])=>{
        const actual=this.getAttribute(key);
        return actual!==null&&(!op||(op==="="&&actual===value));
      });
    });
  }
  closest(selector){for(let n=this;n;n=n.parentElement)if(n.matches(selector))return n;return null;}
  querySelectorAll(selector){
    const result=[];const walk=node=>{
      for(const child of node.childNodes){
        if(child.matches(selector))result.push(child);
        walk(child);
      }
    };walk(this);return result;
  }
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  getBoundingClientRect(){return {width:800,height:100,top:0,left:0};}
}
const idOld="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
const idNew="2115ac8a-f0a0-4d81-a392-a8e02bbc9971";
const idOther="cccccccc-cccc-4ccc-cccc-cccccccccccc";
const idStale="dddddddd-dddd-4ddd-dddd-dddddddddddd";
function reply(id,{images=8,tables=3,badDil=false,user=false}={}){
  const paragraph=new El("p");
  const tableNodes=Array.from({length:tables},()=>new El("table",{},[
    new El("thead",{},[new El("tr",{},[new El("th")])]),
    new El("tbody",{},[new El("tr",{},[new El("td")])]),
  ]));
  const imageNodes=Array.from({length:images},()=>new El("img",{src:"https://private.invalid/?token=never-export"}));
  const code=new El("pre",{},[new El("code")]);
  const sequence=[new El("h2"),paragraph,...tableNodes,...imageNodes,code,new El("blockquote")];
  const dil=new El("div",{"data-dil-message-id":badDil?idOld:id},sequence);
  const md=new El("div",{"data-markdown-text-style":"assistant-message"},[dil]);
  const selection=new El("section",{"data-chatgpt-selection-message-id":id},[md]);
  const heading=new El("h4",{"data-conversation-role":"assistant"});
  const wrap=new El("article",{"data-chatgpt-search-message-ids":id},
    user?[heading,selection,new El("h4",{"data-conversation-role":"user"})]
        :[heading,selection]);
  return {wrap,heading,selection,paragraph,md};
}
const old=reply(idOld,{images:1,tables:1}),fresh=reply(idNew);
const stale=reply(idStale);
// Its Markdown is not yet hydrated when the new request starts.
stale.selection.childNodes=[];
const main=new El("main",{},[old.wrap,stale.wrap]);
const document={querySelectorAll:query=>main.querySelectorAll(query)};
const context={document,Element:El,getComputedStyle:()=>({display:"block",visibility:"visible"})};
runInNewContext(readFileSync("chrome_extension/content_native_message_identity_v291.js","utf8"),context);
const shared=context.__CHAT2API_NATIVE_MESSAGE_IDENTITY_V291__;
assert.equal(shared.version,291);
const baseline=shared.baseline(document);
assert.equal(baseline.ids.has(idOld),true);
assert.equal(baseline.ids.has(idStale),true,
  "hidden/unhydrated historical selection IDs still belong to the baseline");
assert.equal(baseline.nodes.has(stale.selection),true);
stale.selection.append(stale.md);  // historical hydration after dispatch
main.append(fresh.wrap);
const active={
  requestId:"req_native_workerv291",submitStartedAt:100,
  baselineCapturedAt:99,networkStreamSeen:true,networkObservedAt:110,
  baselineNativeMessageIds:baseline.ids,
  baselineNativeSelectionNodes:baseline.nodes,
  baselineRolelessHeadings:new Set([old.heading]),
  baselineFinishActions:new Set(),
  lastCandidateNode:fresh.paragraph,
};
const result=shared.resolveRequest(active,fresh.paragraph,document,()=>true);
assert.equal(result.ok,true);
assert.equal(result.id,idNew);
assert.equal(result.node,fresh.selection);
assert.equal(result.candidate_count,1);
const historicalCandidate=shared.resolveRequest(active,old.paragraph,document,()=>true);
assert.equal(historicalCandidate.ok,true,
  "an old exact baseline assistant paragraph must not mask the new Message ID");
assert.equal(historicalCandidate.node,fresh.selection);
assert.deepEqual([...shared.collect(document,()=>true).map(item=>item.id)],[idOld,idStale,idNew]);
const structural=shared.structuralManifest(fresh.selection);
assert.equal(structural.ok,true);
assert.equal(structural.images,8);
assert.equal(structural.tables,3);
const reference=[...structural.ordered];
assert.deepEqual(reference.slice(0,5),["h2","p","table","table","table"]);
assert.equal(reference.at(-1),"blockquote");
assert.ok(!JSON.stringify(structural).includes("private.invalid"));
assert.ok(!JSON.stringify(structural).includes(idNew));
const missingImage=reply(idOther,{images:7,tables:3});
assert.notDeepEqual([...shared.structuralManifest(missingImage.selection).ordered],reference,
  "missing a picture is not accepted as a complete ordered block inventory");
const missingTable=reply(idOther,{images:8,tables:2});
assert.notDeepEqual([...shared.structuralManifest(missingTable.selection).ordered],reference);
const reordered=reply(idOther);
const mdRoot=reordered.md.querySelector("[data-dil-message-id]");
const move=mdRoot.childNodes.find(child=>child.tagName==="IMG");
mdRoot.childNodes.splice(mdRoot.childNodes.indexOf(move),1);
mdRoot.childNodes.splice(1,0,move);
assert.notDeepEqual([...shared.structuralManifest(reordered.selection).ordered],reference,
  "the same number of blocks in a different DOM order is not equivalent");

// The *Windows* selection proof alone never authorizes request ownership.
assert.equal(shared.resolveRequest({},fresh.paragraph,document,()=>true).ok,false);
assert.equal(shared.resolveRequest({...active,networkStreamSeen:false},fresh.paragraph,document,()=>true).ok,false);
assert.equal(shared.resolveRequest({...active,networkObservedAt:99},fresh.paragraph,document,()=>true).ok,false);
const oldHeadingRejection=shared.resolveRequest({...active,baselineRolelessHeadings:new Set([old.heading,fresh.heading])},fresh.paragraph,document,()=>true);
assert.equal(oldHeadingRejection.ok,false);
assert.equal(oldHeadingRejection.facts.historical_heading,1);
assert.equal(shared.resolveRequest({...active,baselineNativeMessageIds:new Set([idOld,idNew])},fresh.paragraph,document,()=>true).ok,false);
assert.equal(shared.resolveRequest({...active,baselineNativeSelectionNodes:new Set([fresh.selection])},fresh.paragraph,document,()=>true).ok,false);
assert.equal(shared.resolveRequest({...active,options:{mode:"voice"}},fresh.paragraph,document,()=>true).ok,false);

const another=reply(idOther);
main.append(another.wrap);
const ambiguous=shared.resolveRequest(active,fresh.paragraph,document,()=>true);
assert.equal(ambiguous.ok,false);
assert.equal(ambiguous.reason,"ambiguous-new-assistant-ids");
assert.equal(ambiguous.candidate_count,2);
const ownOther=shared.resolveRequest({...active,
  baselineNativeMessageIds:new Set([idOld,idNew,idStale]),
  baselineNativeSelectionNodes:new Set([old.selection,fresh.selection,stale.selection])},
  fresh.paragraph,document,()=>true);
assert.equal(ownOther.reason,"candidate-id-mismatch",
  "new but foreign assistant identity cannot replace an existing candidate");

const bad=reply(idOther,{badDil:true});
const contaminated=reply(idOther,{user:true});
assert.equal(shared.inspect(bad.selection,()=>true),null);
assert.equal(shared.inspect(contaminated.selection,()=>true),null);
// Rendered assistant widgets may contain controls, forms or textarea as
// *content*. They must not revoke a matching, request-owned assistant ID.
const interactive=reply(idOther,{images:1,tables:1});
interactive.md.querySelector("[data-dil-message-id]").append(
  new El("form",{},[new El("input"),new El("textarea")]));
assert.equal(shared.inspect(interactive.selection,()=>true)?.id,idOther,
  "nested rich answer form controls do not make assistant identity invalid");
// But an assistant-looking selection mounted INSIDE the page composer form
// must never be treated as a trusted assistant reply.
const composerLookalike=reply(idOther);
const composerForm=new El("form",{},[composerLookalike.wrap]);
assert.equal(shared.inspect(composerLookalike.selection,()=>true),null,
  "a selection under a composer/form ancestor is not trusted");
const wrongWrap=reply(idOther);
wrongWrap.wrap.attrs["data-chatgpt-search-message-ids"]=idOld;
assert.equal(shared.inspect(wrongWrap.selection,()=>true),null);

assert.equal(shared.structuralManifest(null).ok,false);
assert.equal(shared.structuralManifest(fresh.selection,3).reason,"structural-overflow");

// Run the REAL request-v6 binder, not merely a separately mocked method.
const request=readFileSync("chrome_extension/content_request_v6.js","utf8");
const begin=request.indexOf("  function bindNativeReplyScope(active, phase");
const end=request.indexOf("  // A redesigned reply can render",begin);
assert.ok(begin>=0&&end>begin);
const bindContext={
  ...context,globalThis:context,
  visible:()=>true,
  currentAssistantState:()=>({isNew:false,latest:old.paragraph,reason:"historical-turn"}),
  responseFinishActions:()=>[],
  normalize:value=>String(value||""),
  promptStillPresent:()=>false,
};
runInNewContext(request.slice(begin,end)+"\nthis.bind=bindNativeReplyScope;",bindContext);
main.childNodes.pop();  // remove the second fresh identity
const owned={...active,nativeVerifiedReplyScope:null,nativeMessageIdentityBound:false};
assert.equal(bindContext.bind(owned,"terminal"),true);
assert.equal(owned.nativeVerifiedReplyScope,fresh.selection);
assert.equal(owned.nativeMessageIdentityBound,true);
assert.equal(owned.nativeScopeGateReason,"verified");
assert.equal(owned.nativeBindingEvidence,"same-request-native-assistant-message-id-v291");
assert.equal(owned.lastCandidateNode,fresh.selection);
const denied={...owned,baselineNativeMessageIds:new Set([idOld,idNew,idStale]),
  nativeVerifiedReplyScope:null,nativeMessageIdentityBound:false,lastCandidateNode:old.paragraph};
assert.equal(bindContext.bind(denied,"terminal"),false);
assert.equal(denied.nativeMessageIdentityReason,"no-new-assistant-id");
// Real request-v6 binder must retain only finite, shape-only rejection
// counters, not any assistant ID, signed URL, prompt or HTML.
assert.equal(denied.nativeMessageIdentityFacts.historical_nodes,3);
assert.equal(denied.nativeMessageIdentityFacts.fresh_nodes,0);
const invalidFresh=reply(idOther);
invalidFresh.wrap.attrs["data-chatgpt-search-message-ids"]=idOld;
main.append(invalidFresh.wrap);
const badReport=shared.resolveRequest(denied,old.paragraph,document,()=>true);
assert.equal(badReport.reason,"no-new-assistant-id");
assert.equal(badReport.facts.bad_wrapper,1);
assert.equal(badReport.facts.eligible_fresh,0);
assert.equal(JSON.stringify(badReport.facts).includes(idOther),false);
main.childNodes.pop();

console.log("v291 shared Windows/request native identity, 8-image/3-table ordered manifest and negative gates passed");
