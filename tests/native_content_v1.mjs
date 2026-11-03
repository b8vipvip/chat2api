import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code = readFileSync("chrome_extension/content_native_content_v1.js","utf8");
const sent = [];
class Element {
  constructor(tagName, children=[]) { this.tagName=tagName; this.childNodes=children;
    this.isConnected=true; this.parentElement=null;
    for(const c of children) if(c instanceof Element)c.parentElement=this;
  }
  getBoundingClientRect(){return {width:100,height:100};}
  matches(s){return false;}
  closest(s){return null;}
  querySelectorAll(selector){
    const out=[];
    const walk=node=>{
      for(const child of node.childNodes||[]){
        if(child instanceof Element){
          if(selector==="img" && child.tagName==="IMG")out.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return out;
  }
}
const text={nodeType:3,nodeValue:"Hello safe rich text"};
const img=new Element("IMG");
const node=new Element("DIV",[text,img]);
const ctx={Element,Node:{TEXT_NODE:3},Date,setTimeout,clearTimeout,TextEncoder,
 getComputedStyle:()=>({display:"flex",visibility:"visible",flexDirection:"row",
    gap:"8px",color:"rgb(0, 0, 0)",backgroundImage:"url(https://secret.example/token)"}),
 chrome:{runtime:{sendMessage:async m=>{sent.push(m);return {ok:true,committed:true}}}},
};
runInNewContext(code,ctx);
const capture=ctx.__CHAT2API_NATIVE_CONTENT_V1__;
assert.ok(capture);
const active={requestId:"req_native1234567",submitStartedAt:Date.now(),lastCandidateNode:node};
await capture.capture(active,true);
assert.equal(sent.length,1);
const payload=sent[0].event;
assert.equal(payload.type,"chat.native.snapshot");
assert.equal(payload.snapshot.root.children[0].text,"Hello safe rich text");
assert.equal(payload.snapshot.root.children[1].media_index,0);
assert.ok(!JSON.stringify(payload).includes("secret.example"));
// The full reply can consist of several sibling cards while the request-v6
// candidate points only at a paragraph in the first card. A verified fresh
// assistant heading scope must include all sibling cards and their images.
const scopedSent=[];
const scopeNode=new Element("DIV",[
  new Element("DIV",[{nodeType:3,nodeValue:"GameFlow category card"}]),
  new Element("IMG"),
  new Element("DIV",[{nodeType:3,nodeValue:"Second game example"}]),
]);
const scopedCtx={...ctx,__CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{
  replyRoots:()=>({scope:"roleless_reply_siblings",roots:[scopeNode],boundary:"end_of_reply_parent"})
},chrome:{runtime:{sendMessage:async m=>{scopedSent.push(m);return {ok:true,committed:true};}}}};
delete scopedCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,scopedCtx);
const scopedCandidate=scopeNode.childNodes[0];
await scopedCtx.__CHAT2API_NATIVE_CONTENT_V1__.capture({
  requestId:"req_scopedrich12345",submitStartedAt:Date.now(),lastCandidateNode:scopedCandidate
},true);
assert.equal(scopedSent.length,1);
const scopedRoot=scopedSent[0].event.snapshot.root;
assert.equal(scopedRoot.children[0].children.length,3,
  "sibling groups and images cannot be omitted just because the candidate is a nested paragraph");
assert.equal(scopedRoot.children[0].children[1].media_index,0);
// Modern ChatGPT layout may have no "ChatGPT said" H4. request-v6 can
// positively verify this request's own container by its finish toolbar.
// The visual sidecar must use exactly the same trusted scope as the DOM
// evidence recorder, otherwise the latter sees images but MV3C gets none.
const verifiedSent=[];
const verifiedCtx={...ctx,__CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{
  replyRoots:()=>({scope:"verified_reply_container",
    roots:[scopeNode],boundary:"same_request_verified_finish_action"}),
},chrome:{runtime:{sendMessage:async msg=>{verifiedSent.push(msg);return {ok:true,committed:true}}}}};
delete verifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,verifiedCtx);
await verifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__.capture({
  requestId:"req_verifiedrich12345",submitStartedAt:Date.now(),
  lastCandidateNode:scopeNode.childNodes[0],
},true);
assert.equal(verifiedSent.length,1);
assert.equal(verifiedSent[0].event.snapshot.root.children[0].children.length,3);
assert.equal(verifiedSent[0].event.snapshot.root.children[0].children[1].media_index,0);

// Browser computed styles may pass more than the server's 32-key safe limit.
// Preserve color/grid first and ensure a safe snapshot is still published.
const styleCtx={...ctx,getComputedStyle:()=>({
  display:"grid",gridTemplateColumns:"1fr 1fr",flexDirection:"row",
  flexWrap:"nowrap",justifyContent:"start",alignItems:"center",
  objectFit:"cover",textAlign:"center",fontWeight:"700",whiteSpace:"pre-wrap",
  wordBreak:"break-word",overflowWrap:"anywhere",verticalAlign:"middle",
  textDecorationLine:"underline",borderStyle:"solid",alignSelf:"auto",
  justifySelf:"auto",color:"#123456",backgroundColor:"#abcdef",
  borderColor:"#654321",gap:"8px",rowGap:"8px",columnGap:"8px",
  padding:"8px",marginTop:"8px",marginBottom:"8px",marginLeft:"8px",
  marginRight:"8px",width:"120px",minWidth:"10px",maxWidth:"200px",
  height:"90px",minHeight:"10px",maxHeight:"190px",borderRadius:"4px",
  borderWidth:"1px",letterSpacing:"1px",fontSize:"16px",lineHeight:"20px",
})};
delete styleCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,styleCtx);
const styleTree=styleCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_stylebudget12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[{nodeType:3,nodeValue:"styled"}]),
});
assert.ok(styleTree);
assert.ok(Object.keys(styleTree.root.style).length<=32);
assert.equal(styleTree.root.style.color,"#123456");
assert.equal(styleTree.root.style.backgroundColor,"#abcdef");
assert.equal(styleTree.root.style.gridTemplateColumns,"1fr 1fr");

// Enqueue-only ACK must NOT claim that a native snapshot is durable.
const pendingCtx={...ctx,chrome:{runtime:{sendMessage:async()=>({ok:true})}}};
delete pendingCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,pendingCtx);
const pendingActive={requestId:"req_ackmissing123456",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[{nodeType:3,nodeValue:"safe text"}])};
await pendingCtx.__CHAT2API_NATIVE_CONTENT_V1__.capture(pendingActive,true);
assert.equal(pendingActive.nativeCaptureFailure,"native-transport-incomplete");
assert.equal(pendingActive.nativeRevision,undefined);
assert.equal(pendingActive.nativeChunkComplete,false);

await capture.capture(active,false);
assert.equal(sent.length,1);
console.log("Native rich observer: bounded capture, no DOM URLs, independent request status");

// Native table geometry includes empty columns and multi-value padding. A
// missing middle TD moves later values into the wrong column even when all
// text and the table count still match.
const tableCtx={...ctx,getComputedStyle:()=>({display:"block",visibility:"visible",
  boxSizing:"border-box",borderCollapse:"collapse",borderSpacing:"0px",
  padding:"8px 8px 8px 0px",marginLeft:"-395px",marginRight:"-395px",
  borderWidth:"0px 0px 1px",fontSize:"14px",fontWeight:"400",lineHeight:"20px"})};
delete tableCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,tableCtx);
const table=new Element("TABLE",[new Element("TBODY",[
  new Element("TR",[new Element("TD",[{nodeType:3,nodeValue:"left"}]),
    new Element("TD"),new Element("TD",[{nodeType:3,nodeValue:"right"}])]),
  new Element("TR",[new Element("TH"),new Element("TH",[{nodeType:3,nodeValue:"header"}])]),
])]);
const tableTree=tableCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_tablelayout12345",submitStartedAt:Date.now(),lastCandidateNode:table,
});
assert.equal(tableTree.root.children[0].children[0].children.length,3);
assert.equal(tableTree.root.children[0].children[0].children[1].tag,"td");
assert.equal(tableTree.root.children[0].children[1].children[0].tag,"th");
assert.equal(tableTree.root.style.padding,"8px 8px 8px 0px");
assert.equal(tableTree.root.style.boxSizing,"border-box");
assert.equal(tableTree.root.style.borderCollapse,"collapse");
assert.equal(tableTree.root.style.borderSpacing,"0px");
assert.equal(tableTree.root.style.marginLeft,"-395px");
assert.equal(tableTree.root.style.marginRight,"-395px");
assert.equal(tableTree.root.style.borderWidth,"0px 0px 1px");
assert.ok(Object.keys(tableTree.root.style).length<=32);
console.log("Native table observer: empty cells, box sizing, collapsed borders and bounded padding preserved");

// Geometry-sensitive properties must remain inside the 32-property budget
// even when computed styles supply every other supported declaration.
const denseStyles={
  display:"block",visibility:"visible",boxSizing:"border-box",
  borderCollapse:"collapse",borderSpacing:"0px",borderWidth:"0px 0px 1px",
  marginLeft:"-395px",marginRight:"-395px",padding:"8px 8px 8px 0px",
  width:"768px",minWidth:"0px",maxWidth:"1558px",height:"100px",
  minHeight:"0px",maxHeight:"1000px",borderRadius:"4px",
  gridTemplateColumns:"1fr 1fr",flexDirection:"row",flexWrap:"wrap",
  justifyContent:"start",alignItems:"center",objectFit:"cover",
  textAlign:"left",color:"#222222",backgroundColor:"#ffffff",
  borderColor:"#dddddd",fontWeight:"400",fontSize:"14px",
  lineHeight:"20px",whiteSpace:"normal",wordBreak:"normal",
  overflowWrap:"normal",gap:"8px",rowGap:"4px",columnGap:"4px",
  marginTop:"0px",marginBottom:"0px"
};
const denseCtx={...ctx,getComputedStyle:()=>denseStyles};
delete denseCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,denseCtx);
const denseTable=denseCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_dense_table1234",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("TABLE",[new Element("TBODY",[
    new Element("TR",[new Element("TD",[{nodeType:3,nodeValue:"data"}])])
  ])])
});
assert.equal(denseTable.root.style.marginLeft,"-395px");
assert.equal(denseTable.root.style.marginRight,"-395px");
assert.equal(denseTable.root.style.borderWidth,"0px 0px 1px");
assert.ok(Object.keys(denseTable.root.style).length<=32);
console.log("Native dense style: table geometry prioritized under 32 keys");

// Replayed native v1.3.2 example: three thumbnail columns reported as
// 250.656px 250.672px 250.672px. Older two-decimal CSS validation dropped
// the tracks and stacked the images vertically.
const gridCtx={...ctx,getComputedStyle:()=>({
  display:"grid",visibility:"visible",
  gridTemplateColumns:"250.656px 250.672px 250.672px",
  gridTemplateRows:"200.531px",width:"768px",
  height:"200.531px",gap:"8px",boxSizing:"border-box"
})};
delete gridCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,gridCtx);
const gridSnapshot=gridCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_fractionalgrid123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("DIV",[{nodeType:3,nodeValue:"tile 1"}]),
    new Element("DIV",[{nodeType:3,nodeValue:"tile 2"}]),
    new Element("DIV",[{nodeType:3,nodeValue:"tile 3"}])
  ])
});
assert.equal(gridSnapshot.root.style.gridTemplateColumns,
  "250.656px 250.672px 250.672px");
assert.equal(gridSnapshot.root.style.gridTemplateRows,"200.531px");
assert.equal(gridSnapshot.root.style.height,"200.531px");
assert.ok(Object.keys(gridSnapshot.root.style).length<=32);
console.log("Native fractional grid tracks retain three side-by-side images");

// Real 0db39fd4 reply kept a 423.125px, visible but empty Grid asset
// reservation. Silently dropping the box moved subsequent headings ~439px.
const blankGridCtx={...ctx,getComputedStyle:()=>({
  display:"grid",visibility:"visible",width:"768px",height:"423.125px",
  gridTemplateColumns:"250.656px 250.656px 250.656px",
  gridTemplateRows:"423.125px",marginTop:"16px"
})};
delete blankGridCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,blankGridCtx);
const emptyGrid=new Element("DIV");
emptyGrid.getBoundingClientRect=()=>({width:768,height:423.125});
const blankGridResult=blankGridCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_blankgrid_123456",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"before"}]),
    emptyGrid,
    new Element("P",[{nodeType:3,nodeValue:"after"}])
  ])
});
assert.equal(blankGridResult.root.children.length,3);
assert.equal(blankGridResult.root.children[1].tag,"div");
assert.equal(blankGridResult.root.children[1].style.height,"423.125px");
console.log("Native layout: bounded empty Grid remains between adjacent text blocks");

// Real fa937767 fixture has a 17px blank block + 16px top margin
// between the last operating-plan table and the next section header.
// This is layout, not extra reply text.
const blankBlockCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"768px",height:"17px",
  marginTop:"16px",lineHeight:"17px"
})};
delete blankBlockCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,blankBlockCtx);
const spacer=new Element("DIV");
spacer.getBoundingClientRect=()=>({width:768,height:17});
const blankBlockResult=blankBlockCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_blankblock_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"prior table"}]),
    spacer,
    new Element("H2",[{nodeType:3,nodeValue:"next section"}])
  ])
});
assert.equal(blankBlockResult.root.children.length,3);
assert.equal(blankBlockResult.root.children[1].tag,"div");
assert.equal(blankBlockResult.root.children[1].style.marginTop,"16px");
const blankBlockNoMargin={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"768px",height:"17px",
  marginTop:"0px"
})};
delete blankBlockNoMargin.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,blankBlockNoMargin);
const noMarginSpacer=new Element("DIV");
noMarginSpacer.getBoundingClientRect=()=>({width:768,height:17});
const noMarginResult=blankBlockNoMargin.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_nomargin_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"prior table"}]),
    noMarginSpacer,
    new Element("H2",[{nodeType:3,nodeValue:"next section"}])
  ])
});
assert.equal(noMarginResult.root.children.length,3,
  "measured blank box between content blocks must preserve its geometry");
// A 21-table / 28-MathML live fixture has TWO genuinely empty,
 // visible 448px chart reservations with 16px top margins. Previous 48px
 // cutoff shifted all subsequent headings upward by 928px.
const chartCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"768px",height:"448px",
  marginTop:"16px"
})};
delete chartCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,chartCtx);
const chartSpacer=new Element("DIV");
chartSpacer.getBoundingClientRect=()=>({width:768,height:448});
const chartTree=chartCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_emptychart_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("H3",[{nodeType:3,nodeValue:"monthly revenue chart"}]),
    chartSpacer,
    new Element("H3",[{nodeType:3,nodeValue:"business revenue calculation"}]),
  ])
});
assert.equal(chartTree.root.children.length,3);
assert.equal(chartTree.root.children[1].style.height,"448px");
const tooLargeCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"768px",height:"9000px",
  marginTop:"16px"
})};
delete tooLargeCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,tooLargeCtx);
const excessive=new Element("DIV");
excessive.getBoundingClientRect=()=>({width:768,height:9000});
const rejected=tooLargeCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_bigblank_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"before"}]),
    excessive,
    new Element("P",[{nodeType:3,nodeValue:"after"}]),
  ])
});
assert.equal(rejected.root.children.length,2,"out-of-budget empty area stays excluded");
// An 800px slot is within the general layout budget. It must not be
// rejected just because historical examples happened to be 17px/448px.
const scalableCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"768px",height:"800px",marginTop:"16px"
})};
delete scalableCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,scalableCtx);
const scalable=new Element("DIV");
scalable.getBoundingClientRect=()=>({width:768,height:800});
const scalableSnapshot=scalableCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_geometry_123456",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[new Element("P",[{nodeType:3,nodeValue:"before"}]),scalable])
});
assert.equal(scalableSnapshot.root.children.length,2);
assert.equal(scalableSnapshot.root.children[1].style.height,"800px");

console.log("Native generic empty layout: 17px, 448px and guarded >8k sizes");

console.log("Native bounded table-to-heading spacer retains 33px layout without extra text");

// Tail of 0db39fd4: a separate 17px blank block between an ordered
// list and H2 uses no positive margin; the prior rule excluded it.
const listGapCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",width:"742px",height:"17px",
  marginTop:"0px"
})};
delete listGapCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,listGapCtx);
const listGap=new Element("DIV");
listGap.getBoundingClientRect=()=>({width:742,height:17});
listGap.previousElementSibling={matches:sel=>sel.includes("ol")};
listGap.nextElementSibling={matches:sel=>sel.includes("h2")};
const listGapReply=listGapCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_listgap_123456",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("OL",[new Element("LI",[{nodeType:3,nodeValue:"one"}])]),
    listGap,
    new Element("H2",[{nodeType:3,nodeValue:"next heading"}])
  ])
});
assert.equal(listGapReply.root.children.length,3);
assert.equal(listGapReply.root.children[1].tag,"div");
console.log("Native list-heading 17px spacer preserved without capturing generic blanks");

// v1.3.6 21-table history contains MathML formulas with native fractions
// and subscripts. MathML tokens require direct text, never injected HTML.
const mathNamespace="http://www.w3.org/1998/Math/MathML";
const formulaCtx={...ctx,getComputedStyle:()=>({
  display:"block math",visibility:"visible",fontSize:"19.36px",
  height:"13px",width:"25px",color:"rgb(13, 13, 13)"
})};
delete formulaCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,formulaCtx);
const token=(tag,value)=>{
  const e=new Element(tag.toUpperCase(),[{nodeType:3,nodeValue:value}]);
  e.namespaceURI=mathNamespace;e.textContent=value;return e;
};
const mi=token("mi","q"),mn=token("mn","26"),mo=token("mo","=");
const frac=new Element("MFRAC",[mi,mn]);frac.namespaceURI=mathNamespace;
const row=new Element("MROW",[mo,frac]);row.namespaceURI=mathNamespace;
const math=new Element("MATH",[row]);math.namespaceURI=mathNamespace;
const formula=formulaCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_mathtest_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[math])
});
assert.equal(formula.root.children[0].tag,"math");
const mrow=formula.root.children[0].children[0];
assert.equal(mrow.tag,"mrow");
assert.equal(mrow.children[0].tag,"mo");
assert.equal(mrow.children[0].text,"=");
assert.equal(mrow.children[1].tag,"mfrac");
assert.equal(mrow.children[1].children[0].text,"q");
assert.equal(mrow.children[1].children[1].text,"26");
assert.equal(formula.root.children[0].style.display,"block math");
const spoofed=new Element("MI",[{nodeType:3,nodeValue:"not math"}]);
const spoofedResult=formulaCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_mathspoof_12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[spoofed])
});
assert.notEqual(spoofedResult.root.children[0].tag,"mi",
  "HTML custom elements must never gain MathML privileges");
console.log("Native MathML: safe namespace, direct operator text and fractions");

function candidateFor(children) {
  return {requestId:"req_boundedtest1234",submitStartedAt:Date.now(),
    lastCandidateNode:new Element("DIV",children)};
}
// Native snapshots must not silently omit trailing content. The canonical
// Markdown path remains the sole complete answer when DOM caps are reached.
const overNodes=candidateFor(Array.from({length:90},(_,group)=>
  new Element("DIV",Array.from({length:80},(_,i)=>({
    nodeType:3,nodeValue:"paragraph "+group+"-"+i,
  })))));
assert.ok(capture.snapshot(overNodes),"7k-node reply must now be captured without truncation");
await capture.capture(overNodes,true);
assert.equal(sent.length,2,"7k-node reply should be emitted");

// Production 062f needed >1400 safe text/layout nodes and 19 images.
// 24 groups × 80 text nodes is now admitted without cutting off later cards.
const bigOriginal=candidateFor(Array.from({length:24},(_,group)=>
  new Element("DIV",Array.from({length:80},(_,i)=>({
    nodeType:3,nodeValue:"big block "+group+"-"+i,
  })))));
const bigSnapshot=capture.snapshot(bigOriginal);
assert.ok(bigSnapshot && bigSnapshot.root.children.length===24,
  "1920+ nodes must be preserved as one complete native snapshot");

// 17b4 exceeded the former 3,200-node capture budget before the last
// tables and images had rendered. More than 3,200 real safe descendants now
// serialize fully; a 6,400+ result still fails closed without truncation.
const expanded17b4=candidateFor(Array.from({length:46},(_,group)=>
  new Element("DIV",Array.from({length:80},(_,i)=>({
    nodeType:3,nodeValue:"plant section "+group+"-"+i,
  })))));
const snapshot17b4=capture.snapshot(expanded17b4);
assert.ok(snapshot17b4 && snapshot17b4.root.children.length===46,
  "3,680-plus rich nodes must not fall back to the stale early revision");

// A complex reply that previously exceeded 420 nodes is still bounded but
// must now retain every safe text block instead of falling back prematurely.
const largeValid=candidateFor(Array.from({length:10},(_,group)=>
  new Element("DIV",Array.from({length:60},(_,i)=>({
    nodeType:3,nodeValue:"full block "+group+"-"+i,
  })))));
const expanded=capture.snapshot(largeValid);
assert.ok(expanded && expanded.root.children.length === 10,
  "600+ node rich page must be representable");

const overText=candidateFor([{nodeType:3,nodeValue:"A".repeat(300001)}]);
assert.equal(capture.snapshot(overText),null,"over 192K text must fail closed");
await capture.capture(overText,true);
assert.equal(sent.length,2);

// Production f7c8: full source DOM has 16+ nested presentation levels,
 // 4 images, a table and grid wrappers. Previously 14-depth rejected the
 // complete final reply and left an older 90-node/zero-image sidecar.
let nested=new Element("DIV",[new Element("IMG"),new Element("P",[
  {nodeType:3,nodeValue:"deep card text"},
])]);
for(let i=0;i<17;i++)nested=new Element("DIV",[nested]);
const deepReply=capture.snapshot(candidateFor([nested]));
assert.ok(deepReply,"bounded 17-deep visual cards must be serialized");
function findImgs(node) {
  return (node.tag==="img"?1:0)+(node.children||[]).reduce((n,child)=>n+findImgs(child),0);
}
assert.equal(findImgs(deepReply.root),1,"deep image ordinal must remain in final tree");
let finalDeep=new Element("P",[{nodeType:3,nodeValue:"final deep block"}]);
for(let i=0;i<38;i++)finalDeep=new Element("DIV",[finalDeep]);
assert.ok(capture.snapshot(candidateFor([finalDeep])),
  "2a43: complete 38-level rich reply must survive the final capture");
let overDeep=new Element("P",[{nodeType:3,nodeValue:"over-depth"}]);
for(let i=0;i<66;i++)overDeep=new Element("DIV",[overDeep]);
assert.equal(capture.snapshot(candidateFor([overDeep])),null,
  "more than 64 nested levels must still fail closed");

// Production 33da: the 24-month ceramics plan had one 111-child
// assistant reply container. The previous 80-child cap abandoned the
// completed ~2921-node source and left only an earlier 880-node snapshot.
const wide33da=candidateFor([new Element("DIV",Array.from({length:111},(_,i)=>
  i<9?new Element("IMG"):new Element("DIV",Array.from({length:20},(_,j)=>({
    nodeType:3,nodeValue:"section "+i+" line "+j,
  })))
))]);
const wide33daSnapshot=capture.snapshot(wide33da);
assert.ok(wide33daSnapshot,"111-child assistant block must not leave stale partial snapshot");
assert.equal(wide33daSnapshot.root.children[0].children.length,111,
  "last assistant sections must be kept in the same tree");
assert.equal(findImgs(wide33daSnapshot.root),9);
// 3a882b: a final ChatGPT reply can have thousands of direct siblings. Old
// 160-child handling failed the final capture while earlier rev 12 persisted.
const wideFinal=candidateFor(Array.from({length:1800},(_,i)=>
  new Element("P",[{nodeType:3,nodeValue:"section "+i}])));
const fullWide=capture.snapshot(wideFinal);
assert.ok(fullWide && fullWide.root.children.length===1800,
  "every ordered sibling must survive as a single native reply tree");
assert.equal(fullWide.root.children.at(-1).children[0].text,"section 1799");
assert.ok(wideFinal.nativeStructuralMaxChildren>=1800);
const overChildren=candidateFor(Array.from({length:8193},(_,i)=>({
  nodeType:3,nodeValue:"chunk "+i,
})));
assert.equal(capture.snapshot(overChildren),null,"over 8,192 children must fail closed");
assert.equal(overChildren.nativeStructuralFailure,"max-children");

// A generated raster may be wrapped by a ChatGPT preview <button>. Preserve
// only the raster in a safe inert figure, never copy click targets/toolbar text.
const buttonCard=new Element("BUTTON",[
  new Element("IMG"),{nodeType:3,nodeValue:"Download or delete"}
]);
const buttonPreview=capture.snapshot(candidateFor([buttonCard]));
assert.ok(buttonPreview, "button-hosted raster must not disappear");
assert.equal(buttonPreview.root.children[0].tag,"figure");
assert.equal(buttonPreview.root.children[0].children[0].tag,"img");
assert.equal(findImgs(buttonPreview.root),1);
assert.ok(!JSON.stringify(buttonPreview).includes("Download or delete"),
  "button action text must not leak into assistant rich content");
// Explicitly unverified paragraph-only reply scopes may never be promoted
// as if they were the whole assistant turn.
const unverifiedCtx={...ctx,__CHAT2API_NATIVE_DOM_EVIDENCE_V1__:{
  replyRoots:()=>({scope:"candidate_only",roots:[node],boundary:"reply-sibling-cap-reached"})
}};
delete unverifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,unverifiedCtx);
const unverified=candidateFor([new Element("P",[{nodeType:3,nodeValue:"partial"}])]);
assert.equal(unverifiedCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot(unverified),null);
assert.equal(unverified.nativeStructuralFailure,"reply-scope-unverified");

const actionOnly=capture.snapshot(candidateFor([
  new Element("BUTTON",[{nodeType:3,nodeValue:"Copy"}]),
  new Element("P",[{nodeType:3,nodeValue:"safe answer"}]),
]));
assert.ok(actionOnly && findImgs(actionOnly.root)===0);
assert.ok(!JSON.stringify(actionOnly).includes("Copy"));

const overImages=candidateFor(Array.from({length:41},()=>new Element("IMG")));
assert.equal(capture.snapshot(overImages),null,"over 40 images must fail closed");

// Optional intrinsic dimensions are a bounded numerical hint only.
// No upstream URL or image bytes are serialized by the DOM observer.
const dimensionImage=new Element("IMG");
dimensionImage.naturalWidth=900;dimensionImage.naturalHeight=900;
const dimensionSnapshot=capture.snapshot(candidateFor([dimensionImage]));
assert.equal(dimensionSnapshot.root.children[0].intrinsic_width,900);
assert.equal(dimensionSnapshot.root.children[0].intrinsic_height,900);
