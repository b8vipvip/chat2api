import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("scripts/native_page_diagnostic_v1.py","utf8");
const match=source.match(/CAPTURE = r"""([\s\S]*?)"""/);
const probeMatch=source.match(/PAGE_PROBE = r"""([\s\S]*?)"""/);
assert.ok(match && probeMatch, "both versioned browser expressions must exist");
const js=match[1],probeJs=probeMatch[1];

class FakeElement {
  constructor(tag,text="") {
    this.tagName=tag.toUpperCase();
    this.nodeType=1; this.textContent=text;this.isConnected=true;
    this.childNodes=[];this.parentElement=null;
    this.nextElementSibling=null;
  }
  append(child) {
    child.parentElement=this;this.childNodes.push(child);
    if (this.childNodes.length>1) {
      const prior=this.childNodes[this.childNodes.length-2];
      if(prior instanceof FakeElement)prior.nextElementSibling=child;
    }
    return this;
  }
  matches(){return false;}
  closest(selector) {
    if(selector.includes("article")) {
      let n=this;
      while(n){if(n.tagName==="ARTICLE")return n;n=n.parentElement;}
    }
    return null;
  }
  querySelectorAll(selector){
    if(selector.includes("h4,h5,h6"))
      return this.childNodes.filter(x=>x.tagName==="H4");
    return [];
  }
  querySelector(){return null;}
  getBoundingClientRect(){return {x:0,y:0,left:0,top:0,right:800,bottom:120,width:800,height:120};}
}
const heading=new FakeElement("h4","ChatGPT said");
const paragraph=new FakeElement("p");
const textNode={nodeType:3,nodeValue:"A real assistant reply with a table and meaningful content"};
paragraph.append(textNode);
const article=new FakeElement("article").append(heading).append(paragraph);
const doc={
  readyState:"complete",
  querySelector:()=>null,
  querySelectorAll(selector) {
    if(selector.includes("main h4"))return [heading];
    if(selector.includes("data-message-author-role")||
       selector.includes('data-testid="assistant-message"'))return [];
    if(selector.includes('main,[role="main"]'))return [article];
    return [];
  }
};
const context={
  document:doc,Element:FakeElement,Node:{TEXT_NODE:3},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  scrollX:0,scrollY:0,
  location:{pathname:"/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b"},
  globalThis:{},String,Number,Math,Array,Set,
};
const captured=vm.runInNewContext(js+"('req_aabbccdd')",context);
assert.equal(captured.ok,true,JSON.stringify(captured));
assert.equal(captured.scope,"verified-roleless-turn");
assert.equal(captured.counts.text_chars,textNode.nodeValue.length);
assert.equal(captured.root.children[0].tag,"article");
assert.equal(captured.root.children[0].children[1].children[0].text,textNode.nodeValue);
const probe=vm.runInNewContext(probeJs,context);
assert.equal(probe.route,"conversation");
assert.equal(probe.verified_roleless_headings,1);
assert.equal(probe.assistant_roles,0);
assert.equal(probe.ready_state,"complete");
assert.ok(!Object.keys(probe).some(k=>/url|prompt|text|cookie|html/i.test(k)));
console.log("native page roleless historical turn and privacy-safe probe verified");
