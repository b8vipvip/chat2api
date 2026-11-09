import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const src=readFileSync("chrome_extension/content_rich_response_v69.js","utf8");
class Element {
  constructor(tag="div",children=[],kind="") {
    this.tagName=tag.toUpperCase();this.nodeType=1;
    this.childNodes=[];this.parentElement=null;this.kind=kind;
    this.attributes={};this.isConnected=true;this.className="";
    children.forEach(child=>this.appendChild(child));
  }
  appendChild(child) {
    this.childNodes.push(child);
    if(child instanceof Element)child.parentElement=this;
    return child;
  }
  get children(){return this.childNodes.filter(c=>c instanceof Element);}
  get textContent(){return this.childNodes.map(c=>c instanceof Element
    ?c.textContent:String(c.nodeValue||"")).join("");}
  set textContent(v){this.childNodes=[{nodeType:3,nodeValue:String(v)}];}
  get innerText(){return this.textContent;}
  getBoundingClientRect(){return {width:600,height:350};}
  getAttribute(key){return this.attributes[key]||null;}
  setAttribute(key,value){this.attributes[key]=String(value);
    if(key==="class")this.className=String(value);}
  matches(selector){return selector.includes(".cm-editor")&&this.kind==="editor";}
  contains(other){for(let n=other;n;n=n.parentElement)if(n===this)return true;return false;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector) {
    const found=[];
    function visit(n){
      for(const c of n.childNodes||[]){
        if(!(c instanceof Element))continue;
        if(selector.includes(".cm-editor")&&c.kind==="editor")found.push(c);
        else if(selector==="img"&&c.tagName==="IMG")found.push(c);
        else if(selector==="button"&&c.tagName==="BUTTON")found.push(c);
        else if(selector==="code"&&c.tagName==="CODE")found.push(c);
        visit(c);
      }
    }
    visit(this);
    return found;
  }
  cloneNode(deep=true){
    const n=new Element(this.tagName,[],this.kind);
    n.className=this.className;n.attributes={...this.attributes};
    if(deep)this.childNodes.forEach(c=>n.appendChild(
      c instanceof Element?c.cloneNode(true):{...c}));
    return n;
  }
  replaceWith(next){
    const p=this.parentElement;
    if(!p)return;
    const index=p.childNodes.indexOf(this);
    if(index<0)return;
    p.childNodes[index]=next;next.parentElement=p;
    this.isConnected=false;this.parentElement=null;
  }
  remove(){if(!this.parentElement)return;
    this.parentElement.childNodes=this.parentElement.childNodes.filter(c=>c!==this);
    this.isConnected=false;this.parentElement=null;}
}
const text=s=>({nodeType:3,nodeValue:s});
const document={createElement:tag=>new Element(tag),
  createTextNode:s=>text(s)};
const ctx={Element,Node:{TEXT_NODE:3},document,
  getComputedStyle:()=>({display:"block",visibility:"visible"})};
runInNewContext(src,ctx);
const rich=ctx.__CHAT2API_RICH_RESPONSE_V69__;
const before="这份Python程序使用标准库模拟进货、库存以及月度经营现金流。";
const after="下面列出默认参数对应的经营测算结果和实际执行操作建议。";
const code="import math\n\ndef revenue(value):\n    return math.floor(value)";
const editor=new Element("div",[],"editor");
const root=new Element("div",[
  new Element("p",[text(before)]),editor,new Element("p",[text(after)])
]);
// runInNewContext has its own Map constructor, just like browser worlds.
// The fixture must instantiate the SAME realm's type as the rich reader.
const VMMap=runInNewContext("Map",ctx);
const docs=new VMMap([[editor,{source:"react-owned-code-v302",text:code}]]);
const prepared=rich.prepareVerifiedEditorMarkdown(root,docs);
assert.equal(prepared.ok,true,prepared.reason);
assert.equal(prepared.integrated,1);
assert.ok(prepared.enriched.includes("```python\n"+code+"\n```"),
  "prepared evidence must contain exact full verified source at its DOM slot");
const original=before+"\n\nPython\n\n"+after;
// Simulate the awaited native snapshot server ACK: React redraw replaces
// the original editor element after its state was proven, before final egress.
editor.replaceWith(new Element("div",[text("virtual view still loading")]));
assert.equal(editor.isConnected,false);
const live=rich.mergeVerifiedEditorMarkdown(original,root,docs);
assert.equal(live.integrated,0,"old post-ACK live DOM splice has lost the code card");
const restored=rich.spliceVerifiedCodeIntoCanonical(original,prepared.enriched,
  prepared.codeTexts);
assert.equal(restored.integrated,1,restored.reason);
assert.ok(restored.text.includes("```python\n"+code+"\n```"));
assert.ok(restored.text.indexOf(before)<restored.text.indexOf(code));
assert.ok(restored.text.indexOf(code)<restored.text.indexOf(after));
// Same text with ambiguous neighboring paragraphs cannot be forced into a
// different reply. Stage proof alone does not bypass unique anchors.
const duplicateBefore=original+"\n\n"+before+"\n\n"+after;
const rejected=rich.spliceVerifiedCodeIntoCanonical(duplicateBefore,
  prepared.enriched,prepared.codeTexts);
assert.equal(rejected.integrated,0);
assert.equal(rejected.reason,"code-position-unverified");
assert.equal(rejected.text,duplicateBefore);
assert.ok(src.includes("return {ok:true,enriched,codeTexts,integrated:evidence.integrated}"));
console.log("v311 prepared same-request editor code survives post-ACK React remount without guessing position");
