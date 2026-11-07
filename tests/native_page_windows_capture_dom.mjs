import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/background_native_page_diagnostic_v1.js","utf8");
const start=source.indexOf("  function capturePage() {");
const stop=source.indexOf("\n  async function send(message)",start);
assert.ok(start>=0&&stop>start);
const captureSource="(()=>{\n"+source.slice(start,stop)+"\nreturn capturePage();\n})()";
const sharedSource=readFileSync("chrome_extension/content_native_message_identity_v291.js","utf8");
class TextNode {
  constructor(value){this.nodeType=3;this.nodeValue=value;this.parentElement=null;}
}
class Element {
  constructor(tag,attrs={},children=[]){
    this.nodeType=1;this.tagName=tag.toUpperCase();this.attrs=attrs;
    this.childNodes=children;this.parentElement=null;this.isConnected=true;
    this.complete=true;this.naturalWidth=640;this.naturalHeight=480;
    for(const child of children)child.parentElement=this;
  }
  getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?String(this.attrs[k]):null;}
  get textContent(){return this.childNodes.map(child=>child.nodeType===3?child.nodeValue:child.textContent).join("");}
  matches(selector) {
    return selector.split(",").some(raw=>{
      const s=raw.trim(),tag=s.match(/^[a-z][\w-]*/i)?.[0];
      if(tag&&tag.toUpperCase()!==this.tagName)return false;
      const attrs=[...s.matchAll(/\[([\w-]+)(?:([*^$~|]?=)['"]?([^'"\]]+)['"]?)?\]/g)];
      if(!tag&&!attrs.length)return false;
      return attrs.every(([,key,op,value])=>{
        const actual=this.getAttribute(key);
        if(actual===null)return false;
        if(!op)return true;
        if(op==="=")return actual===value;
        if(op==="$=")return actual.endsWith(value);
        return false;
      });
    });
  }
  closest(s){for(let n=this;n;n=n.parentElement)if(n.matches(s))return n;return null;}
  querySelectorAll(s){
    const result=[];const visit=n=>{
      for(const child of n.childNodes){
        if(child.nodeType===1){if(child.matches(s))result.push(child);visit(child);}
      }
    };visit(this);return result;
  }
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
  getBoundingClientRect(){return {width:640,height:200,left:0,top:0};}
}
const T=value=>new TextNode(value);
function message(id,text,role="assistant",dil=id) {
  const image=new Element("img");
  const table=new Element("table",{},[new Element("tr",{},[
    new Element("td",{},[T("first cell")])
  ])]);
  const md=new Element("div",{"data-markdown-text-style":"assistant-message"},[
    new Element("div",{"data-dil-message-id":dil},[
      new Element("p",{},[T(text)]),table,image
    ])
  ]);
  const selection=new Element("section",{"data-chatgpt-selection-message-id":id},[md]);
  const heading=new Element("h4",{"data-conversation-role":role},[T(role)]);
  return new Element("article",{"data-chatgpt-search-message-ids":id},[heading,selection]);
}
function execute(root){
  const document={
    readyState:"complete",
    querySelectorAll:s=>root.querySelectorAll(s)
  };
  const ctx={document,Node:{TEXT_NODE:3},Element,Date,
    getComputedStyle:()=>({display:"block",visibility:"visible",
      fontSize:"16px",lineHeight:"24px"})};
  runInNewContext(sharedSource,ctx);
  return runInNewContext(captureSource,ctx);
}
const user=message("u-user-1234","USER_ONLY_SENTINEL","user");
const first=message("a-first-1234","EARLY_ASSISTANT");
const last=message("a-last-1234","FINAL_ASSISTANT");
const main=new Element("main",{},[user,first,last]);
const captured=execute(main);
assert.equal(captured.ok,true);
assert.equal(captured.answer.message_id,"a-last-1234");
assert.equal(captured.answer.boundary_verified,true);
assert.equal(captured.answer.counts.images,1);
assert.equal(captured.answer.counts.tables,1);
assert.ok(JSON.stringify(captured.answer.root).includes("FINAL_ASSISTANT"));
assert.ok(!JSON.stringify(captured.answer.root).includes("EARLY_ASSISTANT"));
assert.ok(!JSON.stringify(captured.answer.root).includes("USER_ONLY_SENTINEL"));
assert.equal(captured.answer.request_bound,false,
  "History page never proves the original API request");
const mismatch=message("a-selection-123","SHOULD_NOT_CAPTURE","assistant","a-different-dil");
assert.equal(execute(new Element("main",{},[mismatch])).ok,false,
  "A mismatched DIL source message ID must fail closed");
// A visible generation stop control disqualifies an otherwise stable DOM.
const stopButton=new Element("button",{"data-testid":"stop-button"},[T("Stop")]);
const busyMain=new Element("main",{},[first,last,stopButton]);
const busy=execute(busyMain);
assert.equal(busy.ok,true);
assert.equal(busy.answer.probe.generation_active,true);
assert.equal(busy.answer.complete,false,
  "Do not call a response complete while ChatGPT still generates");
assert.ok(!JSON.stringify(busy.answer.root).includes("Stop"));
console.log("Windows native page capture: verified role+message scope, one table, one image, user exclusion and wrong-ID rejection");
