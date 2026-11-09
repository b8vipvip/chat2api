import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync('chrome_extension/content_native_dom_evidence_v1.js', 'utf8');
const TEXT = 3;
class Element {
  constructor(tag, children = []) {
    this.tagName = tag;
    this.nodeType = 1;
    this.childNodes = children;
    this.parentElement = null;
    this.isConnected = true;
    for (const child of children) if (child instanceof Element) child.parentElement = this;
  }
  matches(selector) {
    return this.tagName === 'MAIN' && selector.includes('main');
  }
  contains(node) {
    return node === this || this.childNodes.some(c => c instanceof Element && c.contains(node));
  }
  closest(selector) {
    let at = this;
    while (at) {
      if (selector.includes('main') && at.tagName === 'MAIN') return at;
      at = at.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    if (!selector.includes('user')) return null;
    return this.childNodes.find(c => c instanceof Element && c.tagName === 'USER') ?? null;
  }
  querySelectorAll(selector) {
    const found=[];
    const search=root=>{
      for(const child of root.childNodes){
        if(!(child instanceof Element))continue;
        if(selector==="img" && child.tagName==="IMG")found.push(child);
        search(child);
      }
    };
    search(this);
    return found;
  }
  getBoundingClientRect() { return { left: 20, top: 20, width: 260, height: 80 }; }
}
const word = {nodeType: TEXT, nodeValue:'One card paragraph'};
const p = new Element('P',[word]);
const img = new Element('IMG');
img.naturalWidth=500;img.naturalHeight=300;img.complete=true;
img.src='https://cdn.example.invalid/path?credential=never-export';
const first = new Element('DIV',[p,img]);
const second = new Element('DIV',[new Element('P',[{nodeType:TEXT,nodeValue:'Other category'}])]);
const verified = new Element('DIV',[first,second]);
const main = new Element('MAIN',[verified]);
const events=[];
const scope={
  document:{querySelectorAll:()=>[]},
  Element,Node:{TEXT_NODE:TEXT},Date,setTimeout,clearTimeout,
  getComputedStyle:()=>({display:'grid',visibility:'visible',gap:'12px',
    fontSize:'16px',fontWeight:'400',gridTemplateColumns:'120px 120px',
    flexDirection:'row',textAlign:'left'}),
  chrome:{runtime:{sendMessage:async message=>{events.push(message);return {ok:true}}}},
};
runInNewContext(source,scope);
runInNewContext(readFileSync('chrome_extension/content_native_message_identity_v291.js','utf8'),scope);
const probe=scope.__CHAT2API_NATIVE_DOM_EVIDENCE_V1__;
const active={
 requestId:'req_requestscoped123',submitStartedAt:1,lastCandidateNode:p,
 nativeVerifiedReplyScope:verified,baselineRolelessHeadings:new Set()
};
const verifiedScope=probe.replyRoots(active);
assert.equal(verifiedScope.scope,'verified_reply_container');
assert.equal(verifiedScope.roots.length,1);
const report=probe.captureData(active);
assert.equal(report.complete,true);
assert.equal(report.counts.images,1);
assert.equal(report.counts.grids>=1,true);
assert.ok(JSON.stringify(report).includes('Other category'),'second card included');
assert.ok(!JSON.stringify(report).includes('credential=never-export'));
await probe.capture(active,'before_terminal');
assert.equal(events.length,1);
assert.equal(events[0].event.type,'chat.native.dom_evidence');
assert.equal(events[0].event.report.scope,'verified_reply_container');

// 17b4 captured exactly 3,200 nodes then lost the private evidence tree.
// A still request-scoped 3,600-node answer must be complete under the new
// bounded cap; exceeding 6,400 nodes is still rejected rather than trimmed.
const manySections=new Element('DIV',Array.from({length:1800},(_,i)=>
  new Element('P',[{nodeType:TEXT,nodeValue:'plant detail '+i}])));
const largeVerified=new Element('DIV',[manySections]);
const largeMain=new Element('MAIN',[largeVerified]);
const full=probe.captureData({...active,lastCandidateNode:manySections,
  nativeVerifiedReplyScope:largeVerified});
assert.equal(full.complete,true,'bounded 3,600-node reply must be complete');
assert.ok(full.counts.nodes>3200);
assert.equal(full.root.children[0].children[0].children.length,1800);

// 3a882b: old probe stopped at 6,400 nodes and discarded private tree.
// A bounded 8,000+ node reply must keep its last original table and image.
const manyDeep=new Element('DIV',Array.from({length:4000},(_,i)=>
  new Element('P',[{nodeType:TEXT,nodeValue:'section '+i}])));
const bigEvidence=new Element('DIV',[manyDeep,new Element('TABLE',[
  new Element('TR',[new Element('TD',[{nodeType:TEXT,nodeValue:'last row'}])]),
]),new Element('IMG')]);
const bigPage=new Element('MAIN',[bigEvidence]);
const bigger=probe.captureData({...active,lastCandidateNode:manyDeep,
  nativeVerifiedReplyScope:bigEvidence});
assert.equal(bigger.complete,true,"8,000 nodes should not be clipped at 6,400");
assert.ok(bigger.counts.nodes>8000);
assert.equal(bigger.counts.tables,1);
assert.equal(bigger.counts.images,1);

// The final native snapshot and private evidence share a bounded depth.
let nested=new Element('P',[{nodeType:TEXT,nodeValue:'last deep reply section'}]);
for(let i=0;i<38;i++)nested=new Element('DIV',[nested]);
const deepVerified=new Element('DIV',[nested]);
const deepPage=new Element('MAIN',[deepVerified]);
const deepReport=probe.captureData({...active,lastCandidateNode:nested,
  nativeVerifiedReplyScope:deepVerified});
assert.equal(deepReport.complete,true);
assert.ok(JSON.stringify(deepReport).includes('last deep reply section'));
for(let i=0;i<30;i++)nested=new Element('DIV',[nested]);
const overVerified=new Element('DIV',[nested]);
const overPage=new Element('MAIN',[overVerified]);
assert.equal(probe.captureData({...active,lastCandidateNode:nested,
  nativeVerifiedReplyScope:overVerified}).complete,false,"over-depth evidence must still fail closed");

// 9d090b: image previews are wrapped in <button>. They are request-scoped
// raster evidence, not UI control text. Never export click action labels.
const btnImg=new Element('IMG');btnImg.naturalWidth=900;btnImg.naturalHeight=600;
btnImg.complete=true;btnImg.src="https://secret.example/cdn?token=private";
const btn=new Element('BUTTON',[btnImg,{nodeType:TEXT,nodeValue:'Download secret control'}]);
const btnVerified=new Element('DIV',[btn,new Element('P',[{nodeType:TEXT,nodeValue:'safe reply'}])]);
const btnPage=new Element('MAIN',[btnVerified]);
const buttons=probe.captureData({...active,lastCandidateNode:btnVerified.childNodes[1],
  nativeVerifiedReplyScope:btnVerified});
assert.equal(buttons.complete,true);
assert.equal(buttons.counts.images,1,"button-hosted raster should be visible to both probes");
assert.equal(buttons.root.children[0].children[0].tag,"figure");
assert.ok(!JSON.stringify(buttons).includes("Download secret control"));
assert.ok(!JSON.stringify(buttons).includes("secret.example"));

// A button preview can contain a full SVG chart and a Canvas, not only
// <img>. The private observer must count both graphics without leaking SVG
// vector attribute URLs, Canvas pixels, or action labels. It must also retain
// the entire sibling reply if the roleless ChatGPT heading precedes buttons.
const buttonSvg=new Element("SVG",[
  new Element("PATH"),new Element("G",[new Element("PATH")]),
]);
const buttonCanvas=new Element("CANVAS");
const buttonGraphic=new Element("BUTTON",[buttonSvg,buttonCanvas,
  {nodeType:TEXT,nodeValue:"Download protected button action"}]);
buttonGraphic.matches=selector=>selector.includes("button");
const afterGraphic=new Element("P",[
  {nodeType:TEXT,nodeValue:"Important final explanation after the chart"},
]);
const graphicContainer=new Element("DIV",[buttonGraphic,afterGraphic]);
const graphicPage=new Element("MAIN",[graphicContainer]);
const buttonReport=probe.captureData({...active,lastCandidateNode:afterGraphic,
  nativeVerifiedReplyScope:graphicContainer});
assert.ok(buttonReport.complete);
assert.equal(buttonReport.counts.svg,1,
  "SVG descendants must not be counted as independent chart roots");
assert.equal(buttonReport.counts.canvas,1,
  "Canvas within a button must appear as an unsupported graphic fact");
assert.ok(JSON.stringify(buttonReport).includes("Important final explanation"));
assert.ok(!JSON.stringify(buttonReport).includes("Download protected button action"));
const chartFigure=buttonReport.root.children[0].children[0];
assert.equal(chartFigure.tag,"figure");
assert.equal(chartFigure.children.length,2);

// Roleless assistant scope: the old blocked-button test stopped before a
// SVG-only or Canvas-only chart and lost all later sibling text as well.
const h4=new Element("H4");
h4.innerText="ChatGPT said";
const svgOnlyButton=new Element("BUTTON",[new Element("SVG")]);
svgOnlyButton.matches=selector=>selector.includes("button");
const canvasOnlyButton=new Element("BUTTON",[new Element("CANVAS")]);
canvasOnlyButton.matches=selector=>selector.includes("button");
const tailP=new Element("P",[{nodeType:TEXT,nodeValue:"Final section retained"}]);
const rolelessHolder=new Element("DIV",[
  h4,svgOnlyButton,canvasOnlyButton,tailP
]);
const rolelessPage=new Element("MAIN",[rolelessHolder]);
rolelessPage.querySelectorAll=selector=>selector==="h4"?[h4]:[];
h4.nextElementSibling=svgOnlyButton;
svgOnlyButton.nextElementSibling=canvasOnlyButton;
canvasOnlyButton.nextElementSibling=tailP;
tailP.nextElementSibling=null;
const rolelessScope=probe.replyRoots({
  ...active,lastCandidateNode:tailP,nativeVerifiedReplyScope:null,
  networkStreamSeen:false,baselineRolelessHeadings:new Set(),
});
assert.equal(rolelessScope.scope,"roleless_reply_siblings");
assert.equal(rolelessScope.roots.length,3,
  "both graphics buttons and trailing paragraph belong to the same reply");
const rolelessReport=probe.captureData({
  ...active,lastCandidateNode:tailP,nativeVerifiedReplyScope:null,
  networkStreamSeen:false,baselineRolelessHeadings:new Set(),
});
assert.equal(rolelessReport.complete,true);
assert.equal(rolelessReport.counts.svg,1);
assert.equal(rolelessReport.counts.canvas,1);
assert.ok(JSON.stringify(rolelessReport).includes("Final section retained"));

// A same-request reply may render SVG/Canvas/image content in an OPEN
// ShadowRoot, with <slot> assigning its text outside the shadow's childNodes.
// This probe must report the real graphic counts without exporting pixels or
// the original image URL. Closed shadows are deliberately never traversed.
const shadowSvg=new Element('SVG');
const shadowCanvas=new Element('CANVAS');
const shadowImage=new Element('IMG');
shadowImage.naturalWidth=640;shadowImage.naturalHeight=480;
shadowImage.complete=true;
shadowImage.src='https://cdn.example.invalid/image?secret=do-not-export';
const shadowSlot=new Element('SLOT');
shadowSlot.assignedNodes=()=>[new Element('P',[
  {nodeType:TEXT,nodeValue:'Slotted explanation'}
])];
const shadowHost=new Element('CHART-CARD');
shadowHost.shadowRoot={mode:'open',
  childNodes:[shadowSvg,shadowCanvas,shadowImage,shadowSlot]};
const shadowParagraph=new Element('P',[
  {nodeType:TEXT,nodeValue:'Main explanation'}
]);
const shadowVerified=new Element('DIV',[shadowParagraph,shadowHost]);
const shadowMain=new Element('MAIN',[shadowVerified]);
const shadowReport=probe.captureData({...active,lastCandidateNode:shadowParagraph,
  nativeVerifiedReplyScope:shadowVerified});
assert.equal(shadowReport.complete,true);
assert.equal(shadowReport.counts.svg,1);
assert.equal(shadowReport.counts.svg_large,1);
assert.equal(shadowReport.counts.canvas,1);
assert.equal(shadowReport.counts.canvas_large,1);
assert.equal(shadowReport.counts.images,1);
assert.ok(JSON.stringify(shadowReport).includes('Slotted explanation'));
assert.ok(!JSON.stringify(shadowReport).includes('do-not-export'));
const closedShadowHost=new Element('CLOSED-CARD');
closedShadowHost.shadowRoot=null;
const closedVerified=new Element('DIV',[shadowParagraph,closedShadowHost]);
const closedMain=new Element('MAIN',[closedVerified]);
const closedReport=probe.captureData({...active,lastCandidateNode:shadowParagraph,
  nativeVerifiedReplyScope:closedVerified});
assert.equal(closedReport.counts.svg,0);
assert.equal(closedReport.counts.canvas,0);

// req_d4d49: a Mermaid graphic occupies about 768x789 original CSS px
// yet the private composed DOM tree has only inert empty DIV wrappers.
// Record this as a large opaque VISUAL CANDIDATE, not a real chart or pixel
// proof; do not leak CSS URL, iframe content or arbitrary page layout.
const opaqueLeaf=new Element('DIV');
opaqueLeaf.getBoundingClientRect=()=>({
  left:20,top:20,width:768,height:789,
});
const opaqueSection=new Element('DIV',[opaqueLeaf]);
const opaqueReply=new Element('DIV',[
  new Element('P',[{nodeType:TEXT,nodeValue:'以下Mermaid流程覆盖市场与审批。'}]),
  opaqueSection,
  new Element('P',[{nodeType:TEXT,nodeValue:'最终建议。'}]),
]);
const opaquePage=new Element('MAIN',[opaqueReply]);
const opaqueReport=probe.captureData({...active,lastCandidateNode:opaqueReply.childNodes[0],
  nativeVerifiedReplyScope:opaqueReply});
assert.equal(opaqueReport.complete,true);
assert.equal(opaqueReport.counts.opaque_large,1);
assert.equal(opaqueReport.counts.opaque_large_max_w,768);
assert.equal(opaqueReport.counts.opaque_large_max_h,789);
assert.equal(opaqueReport.counts.svg_large,0);
assert.equal(opaqueReport.counts.canvas_large,0);
assert.equal(opaqueReport.counts.images,0);
assert.ok(!JSON.stringify(opaqueReport).includes('iframe src='),
  "a candidate is a bounded geometry-only fact, never iframe contents");
assert.equal(report.counts.opaque_large,0,
  "normal 260x80 text and image cards must not be suspected opaque charts");

// Untrusted page-wide or cross-user scope cannot be promoted.
const mainScope=probe.captureData({...active,nativeVerifiedReplyScope:main});
assert.equal(mainScope.complete,false);
assert.equal(mainScope.scope,'candidate_only');
const contaminated=new Element('DIV',[first,new Element('USER')]);
const foreignPage=new Element('MAIN',[contaminated]);
const foreign=probe.captureData({...active,nativeVerifiedReplyScope:contaminated});
assert.equal(foreign.complete,false);
console.log('Native DOM evidence: verified sibling cards, excluded user/page scope, private URL redaction');

 
// The independent Windows collector identifies ChatGPT's new native message
// selection by a matching wrapper ID. Only a heading born AFTER this request
// with matching current network evidence may bind the full native reply.
const replyId='2115ac8a-f0a0-4d81-a392-a8e02bbc9971';
const freshParagraph=new Element('P',[{nodeType:TEXT,nodeValue:'陶艺运营方案'}]);
const currentMarkdown=new Element('DIV',[freshParagraph,new Element('TABLE'),new Element('SVG')]);
const assistantSelection=new Element('DIV',[currentMarkdown]);
const assistantHeading=new Element('H4');
const messageWrap=new Element('DIV',[assistantHeading,assistantSelection]);
const messagePage=new Element('MAIN',[messageWrap]);
const baseParagraphClosest=freshParagraph.closest.bind(freshParagraph);
freshParagraph.closest=sel=>sel.includes('data-chatgpt-selection-message-id')
  ?assistantSelection:baseParagraphClosest(sel);
assistantSelection.closest=sel=>sel.includes('data-chatgpt-search-message-ids')
  ?messageWrap:Element.prototype.closest.call(assistantSelection,sel);
assistantSelection.getAttribute=key=>key==='data-chatgpt-selection-message-id'?replyId:null;
assistantSelection.querySelector=selector=>selector.includes('data-markdown-text-style')
  ?currentMarkdown:null;
messageWrap.getAttribute=key=>key==='data-chatgpt-search-message-ids'?replyId:null;
messageWrap.querySelector=selector=>selector.includes("h4[data-conversation-role='assistant']")
  ?assistantHeading:null;
scope.document={querySelectorAll:sel=>sel.includes('data-chatgpt-selection-message-id')
  ?[assistantSelection]:[]};
const newMessageActive={...active,submitStartedAt:1000,baselineCapturedAt:900,
  baselineNativeMessageIds:new Set(),baselineNativeSelectionNodes:new Set(),
  lastCandidateNode:freshParagraph,nativeVerifiedReplyScope:null,
  baselineRolelessHeadings:new Set(),networkStreamSeen:true,
  networkObservedAt:1150};
const messageScope=probe.replyRoots(newMessageActive);
assert.equal(messageScope?.scope,'verified_native_assistant_selection');
assert.equal(messageScope.roots[0],assistantSelection);
const messageReport=probe.captureData(newMessageActive);
assert.equal(messageReport.complete,true);
assert.equal(messageReport.counts.tables,1);
assert.equal(messageReport.counts.svg,1);
assert.equal(messageReport.counts.svg_large,1);
assert.ok(JSON.stringify(messageReport).includes('陶艺运营方案'));
const oldHeading=probe.replyRoots({...newMessageActive,
  baselineRolelessHeadings:new Set([assistantHeading])});
assert.notEqual(oldHeading?.scope,'verified_native_assistant_selection',
  'historical ChatGPT messages may not be bound to a new request');
const noNetworkProof=probe.replyRoots({...newMessageActive,networkStreamSeen:false});
assert.notEqual(noNetworkProof?.scope,'verified_native_assistant_selection');
messageWrap.getAttribute=key=>key==='data-chatgpt-search-message-ids'?'old-different-id':null;
const wrongIdentity=probe.replyRoots(newMessageActive);
assert.notEqual(wrongIdentity?.scope,'verified_native_assistant_selection');
console.log('Native message ID: fresh heading + matching wrapper + network proof required');
