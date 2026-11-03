import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const source=readFileSync("scripts/native_page_diagnostic_v1.py","utf8");
const pick=name=>{
  const match=source.match(new RegExp(name+" = r\"\"\"([\\s\\S]*?)\"\"\""));
  assert.ok(match,"Missing "+name);
  new Function(match[1]);
  return match[1];
};
const capture=pick("CAPTURE");
pick("SCREENSHOT_PREP");
pick("PAGE_PROBE");
assert.ok(!capture.includes("tables.length<3"),"No table-count-dependent fallback");
assert.ok(source.includes("boundary_verified"),"Never promote inferred history to a bound API reply");
assert.ok(Buffer.byteLength(source,"utf8")<200000,"Independent updater size limit");

const txt=value=>({nodeType:3,nodeValue:value,parentElement:null});
class Element {
  constructor(tag,children=[],attrs={}) {
    this.tagName=tag.toUpperCase();this.nodeType=1;this.childNodes=children;
    this.attributes=attrs;this.isConnected=true;
    this.naturalWidth=600;this.naturalHeight=400;this.currentSrc="";
    for(const c of children)c.parentElement=this;
  }
  get children(){return this.childNodes.filter(c=>c.nodeType===1);}
  get innerText(){return this.childNodes.map(c=>c.nodeType===3?c.nodeValue:c.innerText).join("");}
  get textContent(){return this.innerText;}
  matches(selector){
    return selector.split(",").some(s=>{
      s=s.trim();
      if(s.toUpperCase()===this.tagName)return true;
      if(s==='[data-message-author-role="user"]')return this.attributes.role==="user";
      if(s==='[data-testid="user-message"]')return this.attributes.testid==="user-message";
      if(s==='[contenteditable="true"]')return this.attributes.contenteditable===true;
      if(s==='[role="textbox"]')return this.attributes.role==="textbox";
      return false;
    });
  }
  querySelectorAll(sel){
    const out=[];
    const walk=el=>{for(const c of el.childNodes){
      if(c.nodeType!==1)continue;
      if(c.matches(sel))out.push(c);
      walk(c);
    }};
    walk(this);return out;
  }
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  closest(sel){for(let e=this;e;e=e.parentElement)if(e.matches(sel))return e;return null;}
  getBoundingClientRect(){return {width:768,height:3000,left:0,top:0,right:768,bottom:3000};}
}
const run=(reply,expected="req_diag_1234567")=>{
  const main=new Element("main",[new Element("section",[new Element("div",[reply])])]);
  const doc={querySelector:()=>main,querySelectorAll:s=>main.querySelectorAll(s)};
  const invoke=new Function("document","Element","Node","getComputedStyle",
    "scrollX","scrollY","expected","return ("+capture+")(expected)");
  return invoke(doc,Element,{TEXT_NODE:3},()=>({
    display:"block",visibility:"visible",marginTop:"0px"
  }),0,0,expected);
};
const paragraphs=Array.from({length:260},(_,i)=>new Element("p",[
  txt("Paragraph "+i+" "+("a".repeat(40))+". ")
]));
const oneTable=new Element("table",[new Element("tr",[
  new Element("td",[txt("revenue")])
])]);
const longReply=new Element("div",[
  new Element("h2",[txt("Quarterly results")]),...paragraphs,oneTable,
  new Element("form"),new Element("textarea",[txt("UNSENT_PRIVATE_DRAFT")])
]);
const one=run(longReply);
assert.equal(one.ok,true);
assert.equal(one.boundary_verified,false);
assert.equal(one.scope,"inferred-rich-content-candidate");
assert.equal(one.counts.tables,1);
assert.equal(one.counts.text_chars>11000,true);
assert.ok(!JSON.stringify(one).includes("UNSENT_PRIVATE_DRAFT"),
  "Unsent editor text must not be captured");
const noTable=run(new Element("div",[
  new Element("h2",[txt("Plain answer")]),
  ...Array.from({length:7},(_,i)=>new Element("p",[txt("Answer "+i+" "+("q".repeat(45)))]))
]));
assert.equal(noTable.ok,true,"The discovery path must not require any table");
assert.equal(noTable.counts.tables,0);
assert.equal(noTable.boundary_verified,false);
const short=run(new Element("div",[new Element("p",[txt("ok")])]));
assert.equal(short.ok,false,"Insufficient evidence must fail closed");
const liveId=run(longReply,"req_business_123456");
assert.equal(liveId.ok,false,"No inferred rich candidate for a live request marker");
const userRoot=new Element("div",[...paragraphs,oneTable],{role:"user"});
assert.equal(run(userRoot).ok,false,"Explicit user content must never be an assistant candidate");
console.log("Generic rich region: one-table, no-table, editor exclusion, user boundary, short fallback passed");
