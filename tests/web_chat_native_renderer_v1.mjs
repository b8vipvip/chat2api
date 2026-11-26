import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../app/chat_native_renderer_v1.js", import.meta.url), "utf8");
class FakeElement {
  constructor(tagName) { this.tagName=tagName; this.childNodes=[]; this.style={}; this.attributes={}; this.listeners={}; }
  appendChild(node) { this.childNodes.push(node); return node; }
  replaceChildren(...nodes) { this.childNodes=nodes; }
  setAttribute(name,value) { this.attributes[name]=value; }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  click() { return this.listeners.click?.(); }
}
const document = {
  createElement:(tag)=>new FakeElement(tag),
  createElementNS:(_namespace,tag)=>new FakeElement(tag),
  createTextNode:(text)=>({nodeType:3,textContent:String(text)}),
};
const clipboardWrites = [];
const window = {location:{origin:"https://chat.example"},navigator:{clipboard:{
  writeText:async value=>{clipboardWrites.push(value);}
}}};
vm.runInNewContext(source, {window, URL, document});
const renderer = window.Chat2APINativeRendererV1;
assert(renderer && renderer.validateSnapshot);
assert.match(source,/createElementNS/);
assert.doesNotMatch(source,/\.innerHTML\s*=/);

const good = {
  version:1, revision:3, finality:"verified-final",
  assets:["https://chat.example/api/media/media_123456/token_12345678"],
  files:[
    {filename:"voice.mp3",url:"https://chat.example/api/generated-files/abcdef/token_12345678",
      bytes:120,mime_type:"audio/mpeg",status:"ready"},
    {filename:"clip.mp4",url:"https://chat.example/api/generated-files/ghijkl/token_abcdefgh",
      bytes:240,mime_type:"video/mp4",status:"ready"}
  ],
  root:{tag:"div",children:[
    {tag:"svg",attrs:{viewBox:"0 0 10 10"},children:[
      {tag:"path",attrs:{d:"M0 0 L10 10",stroke:"#123456"}}
    ]},
    {tag:"math",math_attrs:{display:"block",displaystyle:"true",scriptlevel:"0",mathvariant:"normal"},children:[
      {tag:"mrow",children:[
        {tag:"mfrac",math_attrs:{linethickness:"0.08em",bevelled:"false"},
          children:[{tag:"mi",text:"x"},{tag:"mi",text:"y"}]},
        {tag:"mo",math_attrs:{stretchy:"true",largeop:"true",lspace:"0.2em"},text:"="},
        {tag:"mn",text:"1"}
      ]},
      {tag:"mtable",math_attrs:{columnalign:"left center",rowspacing:"0.5em 1em",frame:"solid",equalrows:"true"},
        children:[{tag:"mtr",math_attrs:{rowalign:"center"},children:[
          {tag:"mtd",math_attrs:{columnspan:"2",rowspan:"2",columnalign:"center"},
            children:[{tag:"mrow",children:[{tag:"mi",text:"a"}]}]}
        ]}]},
      {tag:"mfenced",math_attrs:{open:"(",close:")",separators:",;"},
        children:[{tag:"mphantom",children:[{tag:"mn",text:"0"}]}]},
      {tag:"semantics",children:[{tag:"mrow",children:[{tag:"mi",text:"x"}]},
        {tag:"annotation",math_attrs:{encoding:"application/x-tex"},text:"\\frac{x}{y}"}]}
    ]},
    {tag:"a",href:"https://example.org/citation",children:[{tag:"span",text:"citation"}]}
  ]}
};
assert.equal(renderer.validateSnapshot(good),true);
const interactive = structuredClone(good);
interactive.root.children.push(
  {tag:"details",open:true,children:[
    {tag:"summary",text:"展开更多信息"},
    {tag:"p",text:"折叠内容仍保留在原生快照中"}
  ]},
  {tag:"button",text:"复制代码",action:"copy-text",action_value:"print('hello')"}
);
assert.equal(renderer.validateSnapshot(interactive),true);
const missingButtonAction = structuredClone(interactive);
delete missingButtonAction.root.children.at(-1).action;
delete missingButtonAction.root.children.at(-1).action_value;
assert.equal(renderer.validateSnapshot(missingButtonAction),false);
const unsafeButtonAction = structuredClone(interactive);
unsafeButtonAction.root.children.at(-1).action = "execute-script";
assert.equal(renderer.validateSnapshot(unsafeButtonAction),false);
const actionOnText = structuredClone(interactive);
actionOnText.root.children[0].action = "copy-text";
actionOnText.root.children[0].action_value = "x";
assert.equal(renderer.validateSnapshot(actionOnText),false);
const summaryOutsideDisclosure = structuredClone(interactive);
summaryOutsideDisclosure.root.children.push({tag:"summary",text:"不允许游离 summary"});
assert.equal(renderer.validateSnapshot(summaryOutsideDisclosure),false);
const invalidDisclosureState = structuredClone(interactive);
invalidDisclosureState.root.children.at(-2).open = "true";
assert.equal(renderer.validateSnapshot(invalidDisclosureState),false);
const validZeroRowSpan = structuredClone(good);
validZeroRowSpan.root.children.push({tag:"table",children:[{tag:"tbody",children:[{tag:"tr",children:[{tag:"td",row_span:0,text:"span"}]}]}]});
assert.equal(renderer.validateSnapshot(validZeroRowSpan),true);
const invalidOneRowSpan = structuredClone(good);
invalidOneRowSpan.root.children.push({tag:"table",children:[{tag:"tbody",children:[{tag:"tr",children:[{tag:"td",row_span:1,text:"span"}]}]}]});
assert.equal(renderer.validateSnapshot(invalidOneRowSpan),false);
assert.equal(renderer.safeMediaUrl(good.assets[0]),good.assets[0]);
assert.equal(renderer.safeMediaUrl("https://evil.example/api/media/media_123456/token_12345678"),"");
assert.equal(renderer.safeMediaUrl("https://chat.example/evil.svg"),"");
assert.equal(renderer.safeGeneratedFileUrl("https://chat.example/api/generated-files/abcdef/short"),"");
assert.equal(renderer.safeGeneratedFileUrl("https://chat.example/api/generated-files/abcdef/token_12345678"),"https://chat.example/api/generated-files/abcdef/token_12345678");
assert.equal(renderer.safeExternalLinkUrl("https://example.org/citation"),"https://example.org/citation");
assert.equal(renderer.safeExternalLinkUrl("javascript:alert(1)"),"");
assert.equal(renderer.safeExternalLinkUrl("https://127.0.0.1/admin"),"");
assert.equal(renderer.safeExternalLinkUrl("https://chat.example/internal"),"");
assert.equal(renderer.safeExternalLinkUrl("http://example.org/insecure"),"");

const unsafeLink = structuredClone(good);
unsafeLink.root.children.push({tag:"a",href:"javascript:alert(1)",text:"unsafe"});
assert.equal(renderer.validateSnapshot(unsafeLink),false);
const unsafeHrefOnDiv = structuredClone(good);
unsafeHrefOnDiv.root.href = "https://example.org";
assert.equal(renderer.validateSnapshot(unsafeHrefOnDiv),false);
const unsafeMathAttribute = structuredClone(good);
unsafeMathAttribute.root.children[1].math_attrs = {onclick:"alert(1)"};
assert.equal(renderer.validateSnapshot(unsafeMathAttribute),false);
const unsafeMathValue = structuredClone(good);
unsafeMathValue.root.children[1].children[0].children[0].math_attrs = {
  linethickness:"0.08em;position:fixed"
};
assert.equal(renderer.validateSnapshot(unsafeMathValue),false);
const wrongMathAttributeScope = structuredClone(good);
wrongMathAttributeScope.root.children[1].math_attrs = {stretchy:"true"};
assert.equal(renderer.validateSnapshot(wrongMathAttributeScope),false);

const renderedHost = new FakeElement("host");
assert.equal(renderer.renderNativeSnapshot(renderedHost,interactive,[
  {id:"turn0search0",url:"https://docs.python.org/3/",title:"Python docs",snippet:"Official documentation"},
  {id:"bad<script>",url:"https://evil.example/source",title:"Unsafe ID"},
  {id:"turn0private",url:"https://127.0.0.1/private",title:"Private URL"},
]),true);
function findTag(node,tag) {
  if (node && node.tagName === tag) return node;
  for (const child of node?.childNodes || []) {
    const found = findTag(child,tag);
    if (found) return found;
  }
  return null;
}
const renderedDisclosure = findTag(renderedHost,"details");
const renderedCopyButton = findTag(renderedHost,"button");
assert.ok(renderedDisclosure);
assert.equal(renderedDisclosure.open,true);
assert.ok(renderedCopyButton);
assert.equal(renderedCopyButton.type,"button");
await renderedCopyButton.click();
assert.deepEqual(clipboardWrites,["print('hello')"]);
assert.equal(renderedCopyButton.textContent,"已复制");
const renderedMath = findTag(renderedHost,"math");
const renderedFraction = findTag(renderedHost,"mfrac");
const renderedOperator = findTag(renderedHost,"mo");
const renderedTable = findTag(renderedHost,"mtable");
const renderedCell = findTag(renderedHost,"mtd");
const renderedFence = findTag(renderedHost,"mfenced");
const renderedAnnotation = findTag(renderedHost,"annotation");
assert.equal(renderedMath.attributes.display,"block");
assert.equal(renderedMath.attributes.displaystyle,"true");
assert.equal(renderedMath.attributes.scriptlevel,"0");
assert.equal(renderedFraction.attributes.linethickness,"0.08em");
assert.equal(renderedFraction.attributes.bevelled,"false");
assert.equal(renderedOperator.attributes.stretchy,"true");
assert.equal(renderedOperator.attributes.lspace,"0.2em");
assert.equal(renderedTable.attributes.columnalign,"left center");
assert.equal(renderedTable.attributes.rowspacing,"0.5em 1em");
assert.equal(renderedCell.attributes.columnspan,"2");
assert.equal(renderedCell.attributes.rowspan,"2");
assert.equal(renderedFence.attributes.open,"(");
assert.equal(renderedFence.attributes.close,")");
assert.equal(renderedAnnotation.attributes.encoding,"application/x-tex");
const renderedLink = findTag(renderedHost,"a");
assert.ok(renderedLink);
assert.equal(renderedLink.href,"https://example.org/citation");
assert.equal(renderedLink.target,"_blank");
assert.equal(renderedLink.rel,"noopener noreferrer nofollow");
assert.equal(renderedLink.referrerPolicy,"no-referrer");
function findAll(node,predicate,result=[]) {
  if (predicate(node)) result.push(node);
  for (const child of node?.childNodes || []) findAll(child,predicate,result);
  return result;
}
const sourceCards = findAll(renderedHost,node=>node.className==="native-source-reference");
assert.equal(sourceCards.length,1);
assert.equal(sourceCards[0].href,"https://docs.python.org/3/");
assert.equal(sourceCards[0].target,"_blank");
assert.equal(sourceCards[0].rel,"noopener noreferrer nofollow");
assert.equal(sourceCards[0].referrerPolicy,"no-referrer");
assert.equal(sourceCards[0].childNodes[0].textContent,"[1] Python docs");
assert.equal(sourceCards[0].childNodes[1].textContent,"Official documentation");
const renderedAudio = findTag(renderedHost,"audio");
const renderedVideo = findTag(renderedHost,"video");
assert.ok(renderedAudio);
assert.ok(renderedVideo);
assert.equal(renderedAudio.controls,true);
assert.equal(renderedAudio.preload,"metadata");
assert.equal(renderedAudio.src,"https://chat.example/api/generated-files/abcdef/token_12345678");
assert.equal(renderedAudio.autoplay,undefined);
assert.equal(renderedVideo.controls,true);
assert.equal(renderedVideo.playsInline,true);
assert.equal(renderedVideo.preload,"metadata");
assert.equal(renderedVideo.src,"https://chat.example/api/generated-files/ghijkl/token_abcdefgh");
assert.equal(renderedVideo.autoplay,undefined);

const unsafeTag = structuredClone(good);
unsafeTag.root.children[0].tag = "script";
assert.equal(renderer.validateSnapshot(unsafeTag),false);
const unsafeAttribute = structuredClone(good);
unsafeAttribute.root.children[0].attrs = {onload:"alert(1)"};
assert.equal(renderer.validateSnapshot(unsafeAttribute),false);
const unsafeStyle = structuredClone(good);
unsafeStyle.root.style = {backgroundImage:"url(https://evil.example/x)"};
assert.equal(renderer.validateSnapshot(unsafeStyle),false);
const unsafeDepth = {version:1,revision:1,finality:"verified-final",assets:[],files:[],root:{tag:"div"}};
let current = unsafeDepth.root;
for(let i=0;i<66;i++){current.children=[{tag:"div"}];current=current.children[0];}
assert.equal(renderer.validateSnapshot(unsafeDepth),false);
console.log("native-renderer-validation-passed");
