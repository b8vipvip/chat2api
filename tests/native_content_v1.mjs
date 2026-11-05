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

// Actual KaTeX MathML must never become HTML div descendants of <math>.
// The real regression fixture contains mtext unit labels and overscripts.
const mathNS="http://www.w3.org/1998/Math/MathML";
function mathEl(tag,children=[],textValue=null){
  const el=new Element(tag.toUpperCase(),children);
  el.namespaceURI=mathNS;
  if(textValue!==null)el.textContent=textValue;
  return el;
}
const mathTree=mathEl("math",[
  mathEl("mrow",[
    mathEl("msub",[mathEl("mi",[{nodeType:3,nodeValue:"N"}],"N"),
                    mathEl("mtext",[{nodeType:3,nodeValue:"每炉"}],"每炉")]),
    mathEl("mo",[{nodeType:3,nodeValue:"="}],"="),
    mathEl("mover",[mathEl("mo",[{nodeType:3,nodeValue:"∑"}],"∑"),
                    mathEl("mi",[{nodeType:3,nodeValue:"n"}],"n")]),
  ]),
]);
const mathReply=capture.snapshot({
  requestId:"req_mathunitsnative001",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[mathTree])
});
assert.equal(mathReply.root.children[0].tag,"math");
const units=mathReply.root.children[0].children[0].children[0].children[1];
assert.equal(units.tag,"mtext");
assert.equal(units.text,"每炉");
assert.equal(mathReply.root.children[0].children[0].children[2].tag,"mover");
const legacyUnit=mathEl("div",[{nodeType:3,nodeValue:"单位"}],"单位");
const legacyMath=capture.snapshot({
  requestId:"req_legacyunitnative001",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[legacyUnit])
});
assert.equal(legacyMath.root.children[0].tag,"mtext");
const htmlMathSpoof=capture.snapshot({
  requestId:"req_htmlspoofnative001",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("MROW",[{nodeType:3,nodeValue:"not MathML"}]),
  ])
});
assert.notEqual(htmlMathSpoof.root.children[0].tag,"mrow");



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


// A 30-month earnings chart may be a large SVG containing no HTML <img>.
// Capture only inert vector geometry, preserve axis labels and clip IDs, and
// never ship the source's links, event handlers or scripts.
const svgNs="http://www.w3.org/2000/svg";
function vector(tag,children=[],attributes={},size={width:50,height:20}){
  const el=new Element(tag.toUpperCase(),children);
  el.namespaceURI=svgNs;
  el.getAttribute=(name)=>attributes[name]??null;
  el.getBoundingClientRect=()=>size;
  if(tag==="text"||tag==="tspan")
    el.textContent=children.map(c=>c.nodeValue||"").join("");
  return el;
}
const axis=vector("text",[{nodeType:3,nodeValue:"75万元"}],
  {x:"24",y:"38",fill:"#333333","text-anchor":"end"});
const clip=vector("clippath",[
  vector("rect",[],{x:"0",y:"0",width:"600",height:"280"})
],{id:"recharts-clip"});
const defs=vector("defs",[clip]);
const line=vector("path",[],{d:"M0 220 L100 210 L200 185 L500 20",
  fill:"none",stroke:"#2288cc","stroke-width":"2"});
const graphics=vector("g",[line],{"clip-path":"url(#recharts-clip)"});
const chart=vector("svg",[defs,graphics,axis],{
  viewBox:"0 0 650 300",width:"650",height:"300"},
  {width:650,height:300});
const chartActive={requestId:"req_chartnative123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[chart])};
const chartCapture=capture.snapshot(chartActive);
assert.ok(chartCapture,"bounded SVG chart must be accepted");
const chartNode=chartCapture.root.children[0];
assert.equal(chartNode.tag,"svg");
assert.equal(chartNode.attrs.viewBox,"0 0 650 300");
assert.equal(chartNode.children[0].children[0].attrs.id,"recharts-clip");
assert.equal(chartNode.children[1].attrs["clip-path"],"url(#recharts-clip)");
assert.equal(chartNode.children[1].children[0].attrs.d,
  "M0 220 L100 210 L200 185 L500 20");
assert.equal(chartNode.children[2].text,"75万元");
assert.equal(chartNode.children[2].attrs.x,"24");
assert.equal(chartActive.nativeSvgChartsCaptured,1);
assert.equal(chartActive.nativeGraphicsUnsupported,0);
const poisonedPath=vector("path",[],{d:"M0 0L30 60",href:"https://evil.example",
  onclick:"alert(1)",fill:"url(https://evil.example/icon.svg)"});
const badChart=vector("svg",[poisonedPath],{width:"650",height:"300"},
  {width:650,height:300});
const safeChart=capture.snapshot({requestId:"req_safechart123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[badChart])});
const serialized=JSON.stringify(safeChart);
assert.ok(serialized.includes("M0 0L30 60"));
assert.ok(!serialized.includes("evil.example")&&!serialized.includes("onclick"));
assert.ok(!serialized.includes("alert(1)"));
const canvasActive={requestId:"req_canvasnative123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"Visible paragraph"}]),
    new Element("CANVAS"),
  ])};
const safeWithoutCanvas=capture.snapshot(canvasActive);
assert.ok(safeWithoutCanvas);
assert.equal(canvasActive.nativeCanvasOmitted,1);
assert.equal(canvasActive.nativeGraphicsUnsupported,1);
assert.ok(!JSON.stringify(safeWithoutCanvas).includes("canvas"));
const iconOnly=vector("svg",[vector("path",[],{d:"M0 0 L12 12"})],
  {width:"24",height:"24"},{width:24,height:24});
const iconCapture=capture.snapshot({requestId:"req_vectoricon123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[
    new Element("P",[{nodeType:3,nodeValue:"Text outside decorative icon"}]),iconOnly,
  ])});
assert.ok(iconCapture);
assert.equal(iconCapture.root.children.length,1,
  "tiny SVG controls should not be promoted into charts");
