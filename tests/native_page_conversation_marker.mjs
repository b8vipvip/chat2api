import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const source=readFileSync("scripts/native_page_diagnostic_v1.py","utf8");
const match=source.match(/CAPTURE = r"""([\\s\\S]*?)"""/);
assert.ok(match,"CAPTURE must exist");
const capture=match[1];
new Function(capture);
assert.ok(capture.includes('h4[data-conversation-role="assistant"]'));
assert.ok(capture.includes("[data-chatgpt-selection-message-id]"));
assert.ok(capture.includes("[data-dil-message-id]"));
assert.ok(Buffer.byteLength(source,"utf8")<200000);

const txt=value=>({nodeType:3,nodeValue:value,parentElement:null});
class Element {
  constructor(tag,children=[],attrs={}) {
    this.tagName=tag.toUpperCase();this.nodeType=1;this.childNodes=children;
    this.attributes=attrs;this.isConnected=true;
    this.naturalWidth=640;this.naturalHeight=360;this.currentSrc="";
    for(const child of children)child.parentElement=this;
  }
  getAttribute(name){return this.attributes[name]===undefined?null:String(this.attributes[name]);}
  get innerText(){return this.childNodes.map(c=>c.nodeType===3?c.nodeValue:c.innerText).join("");}
  get textContent(){return this.innerText;}
  matches(selector){
    return selector.split(",").some(raw=>{
      const value=raw.trim();if(!value)return false;
      const tag=value.match(/^[a-z][a-z0-9-]*/i)?.[0];
      if(tag&&tag.toUpperCase()!==this.tagName)return false;
      const attrs=[...value.matchAll(/\\[([\\w-]+)(?:\\s*([\\^\\$*~|]?=)\\s*["']?([^"'\\]]+)["']?)?\\]/g)];
      if(!tag&&!attrs.length)return false;
      return attrs.every(m=>{
        const actual=this.getAttribute(m[1]);if(actual===null)return false;
        if(!m[2])return true;
        if(m[2]==="=")return actual===m[3];
        if(m[2]==="$=")return actual.endsWith(m[3]);
        if(m[2]==="^=")return actual.startsWith(m[3]);
        if(m[2]==="*=")return actual.includes(m[3]);
        return false;
      });
    });
  }
  querySelectorAll(selector){
    const result=[];
    const visit=element=>{for(const child of element.childNodes){
      if(child.nodeType!==1)continue;
      if(child.matches(selector))result.push(child);
      visit(child);
    }};
    visit(this);return result;
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
  getBoundingClientRect(){return {left:0,top:0,right:768,bottom:3200,width:768,height:3200};}
}
function assistantMessage(id,text,marker=null,sourceId=id,dilId=id){
  const renderer=new Element("div",[new Element("p",[txt(text)])],{"data-dil-message-id":dilId});
  const source=new Element("div",[renderer],{"data-dil-source-message-id":sourceId});
  const markdown=new Element("div",[source],{"data-markdown-text-style":"assistant-message"});
  const attrs={
    "data-chatgpt-selection-message-id":id,
    "data-chatgpt-selection-conversation-id":"local-chatgpt:conversation-fixture"
  };
  if(marker)attrs["data-chat2api-native-request-id"]=marker;
  const selection=new Element("div",[markdown],attrs);
  const heading=new Element("h4",[txt("ChatGPT 说：")],{"data-conversation-role":"assistant"});
  const wrapper=new Element("div",[heading,selection],{"data-chatgpt-search-message-ids":id+" "+id});
  return {wrapper,selection};
}
function userMessage(id,text){
  const markdown=new Element("div",[new Element("p",[txt(text)])],{"data-markdown-text-style":"assistant-message"});
  const selection=new Element("div",[markdown],{"data-chatgpt-selection-message-id":id});
  const heading=new Element("h4",[txt("你说：")],{"data-conversation-role":"user"});
  return new Element("div",[heading,selection],{
    "data-chatgpt-search-message-ids":id,
    "data-chatgpt-search-unit-key":"fallback-turn-3:0:user"
  });
}
function run(children,expected="req_diag_1234567"){
  const main=new Element("main",children);
  const document={
    querySelector(selector){
      if(selector==="main,[role=\"main\"]")return main;
      return main.querySelector(selector);
    },
    querySelectorAll:selector=>main.querySelectorAll(selector)
  };
  const invoke=new Function("document","Element","Node","getComputedStyle","scrollX","scrollY","expected",
    "return ("+capture+")(expected)");
  return invoke(document,Element,{TEXT_NODE:3},()=>({
    display:"block",visibility:"visible",marginTop:"0px"
  }),0,0,expected);
}

const old=assistantMessage("old-message-id-123456","OLD_ASSISTANT_SENTINEL");
const latest=assistantMessage("latest-message-id-123456","LATEST_ASSISTANT_SENTINEL");
const result=run([old.wrapper,latest.wrapper]);
assert.equal(result.ok,true);
assert.equal(result.scope,"native-assistant-message-id");
assert.equal(result.boundary_verified,true);
assert.equal(result.boundary_evidence.assistant_role_marker,true);
assert.equal(result.boundary_evidence.wrapper_message_id_match,true);
assert.ok(JSON.stringify(result.root).includes("LATEST_ASSISTANT_SENTINEL"));
assert.ok(!JSON.stringify(result.root).includes("OLD_ASSISTANT_SENTINEL"),
  "Select the latest assistant message only, not the entire conversation");

const exact=assistantMessage("tagged-message-id-123456","REQUEST_BOUND_SENTINEL","req_live_123456");
const tagged=run([old.wrapper,exact.wrapper],"req_live_123456");
assert.equal(tagged.ok,true);
assert.equal(tagged.scope,"native-assistant-message-id");
assert.ok(JSON.stringify(tagged.root).includes("REQUEST_BOUND_SENTINEL"),
  "Prefer the exact request marker over the latest historical assistant message");

const mismatch=assistantMessage("selection-id-123456","MISMATCH_SENTINEL",null,
  "different-source-id-123456","different-dil-id-123456");
const rejected=run([mismatch.wrapper],"req_live_123456");
assert.equal(rejected.ok,false,"Mismatched selection/source/renderer IDs must fail closed");

const user=run([userMessage("user-message-id-123456","USER_PROMPT_SENTINEL")]);
assert.equal(user.ok,false,"A user-role wrapper must not become an assistant candidate");

console.log("Native ChatGPT message identity: latest reply, exact request binding, ID mismatch and user exclusion passed");
