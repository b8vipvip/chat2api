import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code=readFileSync("chrome_extension/content_rich_response_v69.js","utf8");
class Element {
  constructor(tagName,children=[],codeLines=null) {
    this.tagName=tagName;
    this.childNodes=children;
    this.codeLines=codeLines;
    for(const child of children)if(child instanceof Element)child.parentElement=this;
  }
  matches(selector){
    return selector===".cm-editor,.monaco-editor" && this.codeLines!==null;
  }
  getBoundingClientRect(){return {width:600,height:180};}
  getAttribute(){return null;}
  get children(){return this.childNodes.filter(n=>n instanceof Element);}
  get textContent(){
    return this.childNodes.map(node=>node instanceof Element?
      node.textContent:String(node.nodeValue||"")).join("");
  }
  querySelectorAll(selector){
    if(this.codeLines!==null && selector===".cm-content .cm-line")
      return this.codeLines.map(text=>({textContent:text}));
    return [];
  }
  querySelector(selector){
    if(selector==="code")return this.childNodes.find(x=>x instanceof Element&&x.tagName==="CODE")||null;
    return null;
  }
  cloneNode(deep){
    return new Element(this.tagName,deep?this.childNodes.map(child=>
      child instanceof Element?child.cloneNode(true):{...child}):[],
      this.codeLines===null?null:[...this.codeLines]);
  }
}
const ctx={Element,Node:{TEXT_NODE:3},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
};
runInNewContext(code,ctx);
const rich=ctx.__CHAT2API_RICH_RESPONSE_V69__;
const editor=new Element("DIV",[],["import math","","def check(value):","    return math.isfinite(value)"]);
const root=new Element("DIV",[editor]);
const md=rich.extractMarkdown(root);
assert.ok(md.startsWith("```\nimport math\n\ndef check(value):"),md);
assert.ok(md.includes("    return math.isfinite(value)"));
assert.ok(md.endsWith("```"),md);
const ticks=new Element("PRE",[new Element("CODE",[{nodeType:3,nodeValue:"hello ``` quoted"}])]);
const raw=rich.extractMarkdown(ticks);
assert.ok(raw.startsWith("````\n") && raw.endsWith("````"),raw);
console.log("CodeMirror exported as fenced block; embedded fences escaped");
