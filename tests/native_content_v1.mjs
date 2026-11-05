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
          if(selector.split(",").map(v=>v.trim().toUpperCase()).includes(child.tagName))out.push(child);
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

// Real Windows hot-1.0.4 and Linux v1.3.11 exports contain these computed
// CSS Color 4 values; previously the Worker silently dropped card colors.
const modernStyleValues={
  oklab:"oklab(0.159065 0.00000723451 0.00000317395 / 0.08)",
  srgb:"color(srgb 0.952941 0.952941 0.952941 / 0.82)",
  oklabAccent:"oklab(0.683029 -0.16619 0.102668 / 0.15)"
};
const modernCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",
  color:modernStyleValues.oklabAccent,
  backgroundColor:modernStyleValues.oklab,
  borderColor:modernStyleValues.srgb
})};
delete modernCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,modernCtx);
const modernResult=modernCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_moderncolor12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[{nodeType:3,nodeValue:"styled card"}])
});
assert.equal(modernResult.root.style.color,modernStyleValues.oklabAccent);
assert.equal(modernResult.root.style.backgroundColor,modernStyleValues.oklab);
assert.equal(modernResult.root.style.borderColor,modernStyleValues.srgb);
const forbiddenStyleCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",
  backgroundColor:"color(srgb 0 0 0);background:url(https://evil.invalid/pixel)",
  borderColor:"oklab(var(--secret) 0 0)",
  color:"url(https://evil.invalid/pixel)"
})};
delete forbiddenStyleCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,forbiddenStyleCtx);
const forbiddenStyle=forbiddenStyleCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_moderncolorbad1",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[{nodeType:3,nodeValue:"style attack"}])
});
assert.ok(!forbiddenStyle.root.style.backgroundColor);
assert.ok(!forbiddenStyle.root.style.color);
assert.ok(!forbiddenStyle.root.style.borderColor);

// Windows v1.0.4: 42 table cells have multi-side padding; 57 have CSS
// fractional widths. Linux v1.3.11: 372 and 648, respectively. Without
// this allowlist, all these visual styles silently disappear in delivery.
const tableStyleCtx={...ctx,getComputedStyle:()=>({
  display:"table-cell",visibility:"visible",padding:"8px 8px 8px 0px",
  width:"159.219px",height:"36.5px",color:"rgb(13, 13, 13)"
})};
delete tableStyleCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,tableStyleCtx);
const tableStyleSnap=tableStyleCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_tablecellstyle12",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("TH",[{nodeType:3,nodeValue:"教学负责"}])
});
assert.equal(tableStyleSnap.root.style.padding,"8px 8px 8px 0px");
assert.equal(tableStyleSnap.root.style.width,"159.219px");
assert.equal(tableStyleSnap.root.style.height,"36.5px");
const tableCssAttackCtx={...ctx,getComputedStyle:()=>({
  display:"table-cell",visibility:"visible",
  padding:"8px 8px 8px 0px;position:absolute",
  width:"159.219px;background:url(https://evil.invalid)"
})};
delete tableCssAttackCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,tableCssAttackCtx);
const tableCssAttack=tableCssAttackCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_badtablecss123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("TD",[{nodeType:3,nodeValue:"普通文本"}])
});
assert.equal(tableCssAttack.root.style.padding,undefined);
assert.equal(tableCssAttack.root.style.width,undefined);

 
// Previously, an empty <td>/<th> returned null and shifted the following
// cells one column to the left. Native rich tables must retain their slots,
// including intentional blanks and merged headers.
const emptyCell=new Element("TD");
const mergedHeader=new Element("TH",[{nodeType:3,nodeValue:"合并表头"}]);
mergedHeader.colSpan=3;mergedHeader.rowSpan=2;
const remainingCell=new Element("TD",[{nodeType:3,nodeValue:"保留原列"}]);
const emptyHeader=new Element("TH");
const tableTree=new Element("TABLE",[
  new Element("THEAD",[new Element("TR",[mergedHeader,emptyHeader])]),
  new Element("TBODY",[new Element("TR",[emptyCell,remainingCell])])
]);
const tableFidelity=capture.snapshot({
  requestId:"req_tablegeometry12345",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[tableTree])
});
const tableRoot=tableFidelity.root.children[0];
assert.equal(tableRoot.tag,"table");
const headerCells=tableRoot.children[0].children[0].children;
assert.equal(headerCells.length,2,"empty header cell must remain in-place");
assert.equal(headerCells[0].col_span,3);
assert.equal(headerCells[0].row_span,2);
assert.equal(headerCells[1].tag,"th");
assert.equal(headerCells[1].children,undefined);
const bodyCells=tableRoot.children[1].children[0].children;
assert.equal(bodyCells.length,2,"empty body cell must remain in-place");
assert.equal(bodyCells[0].tag,"td");
assert.equal(bodyCells[1].children[0].text,"保留原列");
const invalidSpanCell=new Element("TD",[{nodeType:3,nodeValue:"normal"}]);
invalidSpanCell.colSpan=9999;invalidSpanCell.rowSpan=-5;
const ignoredSpans=capture.snapshot({
  requestId:"req_tablebadspan12",submitStartedAt:Date.now(),
  lastCandidateNode:invalidSpanCell
});
assert.equal(ignoredSpans.root.col_span,undefined);
assert.equal(ignoredSpans.root.row_span,undefined);

// Browser-calculated table box model previously disappeared even though the
// 1:1 reference contains collapsed borders and spacing-sensitive cells.
const boxCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",
  boxSizing:"border-box",borderCollapse:"collapse",borderSpacing:"0px 2.5px"
})};
delete boxCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,boxCtx);
const boxSnap=boxCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_tableboxmodel123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("TABLE",[
    new Element("TR",[new Element("TD")])
  ])
});
assert.equal(boxSnap.root.style.borderCollapse,"collapse");
assert.equal(boxSnap.root.style.borderSpacing,"0px 2.5px");
assert.equal(boxSnap.root.style.boxSizing,"border-box");
assert.equal(boxSnap.root.children[0].children[0].style.borderSpacing,undefined);
const injectedBoxCtx={...ctx,getComputedStyle:()=>({
  display:"block",visibility:"visible",
  borderCollapse:"collapse;display:none",
  borderSpacing:"0px 0px;url(https://evil.example)",
  boxSizing:"border-box;position:fixed"
})};
delete injectedBoxCtx.__CHAT2API_NATIVE_CONTENT_V1__;
runInNewContext(code,injectedBoxCtx);
const injectedBox=injectedBoxCtx.__CHAT2API_NATIVE_CONTENT_V1__.snapshot({
  requestId:"req_tableboxattack123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("TABLE",[new Element("TR",[new Element("TD")])])
});
assert.equal(injectedBox.root.style.borderCollapse,undefined);
assert.equal(injectedBox.root.style.borderSpacing,undefined);
assert.equal(injectedBox.root.style.boxSizing,undefined);





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

// Modern colors on computed SVG strokes must stay inert but visually faithful.
const modernStroke="color(srgb 0.952941 0.952941 0.952941 / 0.82)";
const modernVector=vector("svg",[
  vector("path",[],{d:"M0 10 L200 100",stroke:modernStroke})
],{width:"650",height:"300"},{width:650,height:300});
const modernSvg=capture.snapshot({requestId:"req_colorvector123",submitStartedAt:Date.now(),
  lastCandidateNode:new Element("DIV",[modernVector])});
assert.equal(modernSvg.root.children[0].children[0].attrs.stroke,modernStroke);

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

 
// Prior Windows v1.0.3 vs Chrome comparison found three standalone
// "\\n  " text nodes (9 characters) discarded inside pre/code. The formal
// request observer must retain them, not just the independent diagnostic.
const codeNative=new Element("PRE",[new Element("CODE",[
  new Element("SPAN",[{nodeType:3,nodeValue:"x=1"},{nodeType:3,nodeValue:"\n  "},
    {nodeType:3,nodeValue:"y=2"}])
])]);
const codeOnly=capture.snapshot({requestId:"req_codewhitespace123",
  submitStartedAt:Date.now(),lastCandidateNode:codeNative});
assert.ok(codeOnly);
const codeLeaves=codeOnly.root.children[0].children[0].children;
assert.equal(codeLeaves[0].text,"x=1");
assert.equal(codeLeaves[1].text,"\n  ");
assert.equal(codeLeaves[2].text,"y=2");
const ordinaryWhitespace=capture.snapshot({requestId:"req_outsidecode123",
  submitStartedAt:Date.now(),lastCandidateNode:new Element("P",[
    {nodeType:3,nodeValue:"plain"},{nodeType:3,nodeValue:"\n  "},
  ])});
assert.equal(ordinaryWhitespace.root.children.length,1,
  "Whitespace-only plain paragraph nodes still get filtered");
