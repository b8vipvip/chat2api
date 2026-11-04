import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const code=readFileSync("chrome_extension/content_native_message_identity_v1.js","utf8");
const NodeFlags={DOCUMENT_POSITION_PRECEDING:2,DOCUMENT_POSITION_FOLLOWING:4};
class Element {
  constructor(tag,attrs={},children=[]) {
    this.tagName=tag.toUpperCase();this.nodeType=1;this.attrs=attrs;
    this.childNodes=children;this.parentElement=null;this.isConnected=true;
    for(const child of children)child.parentElement=this;
  }
  getAttribute(key){return Object.prototype.hasOwnProperty.call(this.attrs,key)?this.attrs[key]:null;}
  get dataset(){return {};}
  matches(selector){
    return selector.split(",").some(part=>{
      const s=part.trim(),name=s.match(/^[a-z][a-z0-9-]*/i)?.[0];
      if(name&&name.toUpperCase()!==this.tagName)return false;
      const clauses=[...s.matchAll(/\[([\w-]+)(?:([*^$~|]?=)['"]?([^'"\]]+)['"]?)?\]/g)];
      if(!name&&!clauses.length)return false;
      return clauses.every(([,key,op,value])=>{
        const attr=this.getAttribute(key);
        return attr!=null&&(!op||op==="="?attr===value:
          op==="$="?attr.endsWith(value):op==="^="?attr.startsWith(value):
          op==="*="?attr.includes(value):false);
      });
    });
  }
  querySelectorAll(selector){
    const found=[];
    const visit=node=>{for(const child of node.childNodes){
      if(child.matches(selector))found.push(child);visit(child);
    }};
    visit(this);return found;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){
    for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;
    return null;
  }
  compareDocumentPosition(other){
    const root=this.getRoot(),list=[];
    const visit=node=>{list.push(node);for(const c of node.childNodes)visit(c)};
    visit(root);
    const a=list.indexOf(this),b=list.indexOf(other);
    return a<b?NodeFlags.DOCUMENT_POSITION_FOLLOWING:NodeFlags.DOCUMENT_POSITION_PRECEDING;
  }
  getRoot(){let root=this;while(root.parentElement)root=root.parentElement;return root;}
  getBoundingClientRect(){return {width:800,height:300,left:0,top:0};}
  get textContent(){return this.childNodes.map(x=>x.textContent).join("");}
  get innerText(){return this.textContent;}
}
const p=text=>new Element("p",{},[new Element("span",{"data-text":text})]);
const heading=role=>new Element("h4",{"data-conversation-role":role});
function assistant(id,sourceId=id){
  const d=new Element("div",{"data-dil-message-id":sourceId});
  const md=new Element("div",{"data-markdown-text-style":"assistant-message",
    "data-dil-source-message-id":id},[d,p("assistant answer")]);
  const selection=new Element("div",{"data-chatgpt-selection-message-id":id},[md]);
  const wrapper=new Element("div",{"data-chatgpt-search-message-ids":id+" "+id},
    [heading("assistant"),selection]);
  return {wrapper,selection,md};
}
function user(id){
  const content=new Element("div",{"data-chatgpt-search-message-ids":id},
    [heading("user"),p("user prompt")]);
  const unit=new Element("section",{"data-chatgpt-search-unit-key":"fallback-turn:0:user"},[content]);
  return {unit,content};
}
const a=assistant("msg-old"),u=user("user-one"),b=assistant("msg-latest");
const legacy=new Element("article",{"data-message-author-role":"assistant",
  "data-message-id":"legacy"},[p("legacy")]);
const root=new Element("main",{},[a.wrapper,u.unit,legacy,b.wrapper]);
const doc={querySelectorAll:s=>root.querySelectorAll(s)};
const getComputedStyle=()=>({display:"block",visibility:"visible"});
const identity=new Function("Element","Node","document","getComputedStyle",
  code+"\nreturn globalThis.__CHAT2API_NATIVE_MESSAGE_IDENTITY_V1__;")(
  Element,NodeFlags,doc,getComputedStyle);
assert.ok(identity);
const nodes=identity.assistantMessages();
assert.deepEqual(nodes,[a.selection,legacy,b.selection],
  "Native and legacy assistant nodes retain DOM order");
assert.equal(identity.resolveAssistantSelection(b.md),b.selection);
assert.equal(identity.messageId(b.md),"msg-latest");
const users=identity.userMessages();
assert.deepEqual(users,[u.unit],"User heading and outer unit are deduplicated");
assert.equal(identity.isUser(u.content),true);
assert.equal(identity.isUser(b.selection),false);
assert.equal(identity.turnOf(b.md),b.selection);
const broken=assistant("selection-id","different-dil-id");
root.childNodes.push(broken.wrapper);broken.wrapper.parentElement=root;
assert.equal(identity.resolveAssistantSelection(broken.selection),null,
  "Cross-message DIL source must not verify a reply");
assert.deepEqual(identity.assistantMessages(),[a.selection,legacy,b.selection],
  "A mismatched selection is not promoted to a current reply");
console.log("Native identity: message scope, user separation, DOM order and mismatch gate passed");
