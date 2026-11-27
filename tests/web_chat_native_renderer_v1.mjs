import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../app/chat_native_renderer_v1.js", import.meta.url), "utf8");
class FakeElement {
  constructor(tagName) { this.tagName=tagName; this.childNodes=[]; this.style={}; this.attributes={}; }
  appendChild(node) { this.childNodes.push(node); return node; }
  replaceChildren(...nodes) { this.childNodes=nodes; }
  setAttribute(name,value) { this.attributes[name]=value; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes,name)?this.attributes[name]:null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name,callback) { this.events??={}; this.events[name]=callback; }
  click() { return this.events?.click?.(); }
}
const document = {
  createElement:(tag)=>new FakeElement(tag),
  createElementNS:(_namespace,tag)=>new FakeElement(tag),
  createTextNode:(text)=>({nodeType:3,textContent:String(text)}),
};
const window = {location:{origin:"https://chat.example"}};
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
assert.equal(renderer.renderNativeSnapshot(renderedHost,good,[
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


const interactive = {
  version:1,revision:4,finality:"verified-final",assets:[],files:[],
  root:{tag:"div",children:[
    {tag:"details",control:{kind:"disclosure",open:true},children:[
      {tag:"summary",text:"展开步骤"},
      {tag:"p",text:"安全的本地折叠内容"},
    ]},
    {tag:"input",style:{width:"240px"},control:{kind:"checkbox",label:"包含税费",disabled:false,checked:true}},
    {tag:"input",control:{kind:"radio",label:"按月统计",disabled:false,checked:true,group:"period"}},
    {tag:"input",control:{kind:"range",label:"透明度",disabled:false,min:0,max:100,step:5,value:45}},
    {tag:"select",control:{kind:"select",label:"图表类型",disabled:false,selected:"bar"},children:[
      {tag:"option",text:"柱状图",control:{kind:"option",value:"bar",disabled:false,selected:true}},
      {tag:"option",text:"折线图",control:{kind:"option",value:"line",disabled:false,selected:false}},
    ]},
    {tag:"button",control:{kind:"copy",label:"Copy code",text:"print('hello')\n"}},
  ]},
};
assert.equal(renderer.validateSnapshot(interactive),true);
const interactiveHost=new FakeElement("host");
let copied="";
const interactiveWindow = {};
const interactiveRendererSource = source;
const interactiveContext = {
  window:interactiveWindow,URL,document,
  navigator:{clipboard:{writeText:async value=>{copied=value;}}},
};
vm.runInNewContext(interactiveRendererSource,interactiveContext);
const interactiveRenderer=interactiveWindow.Chat2APINativeRendererV1;
assert.equal(interactiveRenderer.renderNativeSnapshot(interactiveHost,interactive),true);
assert.match(interactiveHost.childNodes[0].textContent,/本地预览/);
const renderedDetails=findTag(interactiveHost,"details");
assert.equal(renderedDetails.open,true);
const renderedInputs=findAll(interactiveHost,node=>node.tagName==="input");
assert.equal(renderedInputs.length,3);
assert.equal(renderedInputs[0].type,"checkbox");
assert.equal(renderedInputs[0].checked,true);
assert.equal(renderedInputs[0].attributes["aria-label"],"包含税费");
assert.equal(renderedInputs[1].type,"radio");
assert.equal(renderedInputs[1].checked,true);
assert.ok(renderedInputs[1].name.startsWith("native-"));
assert.equal(renderedInputs[2].type,"range");
assert.equal(renderedInputs[2].value,"45");
assert.equal(renderedInputs[2].min,"0");
assert.equal(renderedInputs[2].max,"100");
assert.equal(renderedInputs[2].step,"5");
const renderedSelect=findTag(interactiveHost,"select");
assert.equal(renderedSelect.value,"bar");
assert.equal(renderedSelect.childNodes.length,2);
const copyControl=findTag(interactiveHost,"button");
assert.equal(copyControl.type,"button");
assert.equal(copyControl.textContent,"Copy code");
copyControl.events.click();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(copied,"print('hello')\n");
assert.equal(copyControl.textContent,"已复制");

const unsafeControl=structuredClone(interactive);
unsafeControl.root.children.push({tag:"button",control:{kind:"run",label:"运行",text:"code"}});
assert.equal(renderer.validateSnapshot(unsafeControl),false);
const malformedSelect=structuredClone(interactive);
malformedSelect.root.children[4].control.selected="missing";
assert.equal(renderer.validateSnapshot(malformedSelect),false);
console.log("native-renderer-interactive-controls-passed");


const disclosureWithoutSummary = {
  version:1,revision:6,finality:"verified-final",assets:[],files:[],
  root:{tag:"details",control:{kind:"disclosure",open:false},children:[
    {tag:"p",text:"Legacy disclosure content remains renderable"},
  ]},
};
assert.equal(interactiveRenderer.validateSnapshot(disclosureWithoutSummary),true);
const emptyDisclosure = {
  version:1,revision:7,finality:"verified-final",assets:[],files:[],
  root:{tag:"details",control:{kind:"disclosure",open:false},children:[]},
};
assert.equal(interactiveRenderer.validateSnapshot(emptyDisclosure),true);
const tooManyOptions=structuredClone(interactive);
tooManyOptions.root.children[4].children=Array.from({length:65},(_,index)=>({
  tag:"option",text:`Option ${index}`,control:{
    kind:"option",value:`v${index}`,disabled:false,selected:index===0,
  },
}));
tooManyOptions.root.children[4].control.selected="v0";
assert.equal(interactiveRenderer.validateSnapshot(tooManyOptions),false);
const duplicateSummary=structuredClone(interactive);
duplicateSummary.root.children[0].children.splice(1,0,{tag:"summary",text:"第二个摘要"});
assert.equal(interactiveRenderer.validateSnapshot(duplicateSummary),false);
assert.equal(renderedInputs[0].style.width,"240px","captured input styles must survive rendering");

const chartSnapshot={
  version:1,revision:5,finality:"verified-final",assets:[],files:[],
  root:{tag:"svg",attrs:{viewBox:"0 0 640 320"},children:[
    {tag:"title",text:"季度收入趋势"},
    {tag:"desc",text:"按季度展示收入变化"},
    {tag:"path",attrs:{d:"M0 0 L640 320",stroke:"#123456"}},
  ]},
};
assert.equal(interactiveRenderer.validateSnapshot(chartSnapshot),true);
const chartHost=new FakeElement("host");
assert.equal(interactiveRenderer.renderNativeSnapshot(chartHost,chartSnapshot),true);
assert.equal(findTag(chartHost,"title").childNodes[0].textContent,"季度收入趋势");
assert.equal(findTag(chartHost,"desc").childNodes[0].textContent,"按季度展示收入变化");
console.log("native-renderer-svg-chart-tooltip-passed");

const actionable=structuredClone(interactive);
actionable.revision=8;
actionable.root.children[0].interaction_id="nc_8_1";
actionable.root.children[1].interaction_id="nc_8_2";
actionable.root.children[2].interaction_id="nc_8_3";
actionable.root.children[3].interaction_id="nc_8_4";
actionable.root.children[4].interaction_id="nc_8_5";
assert.equal(interactiveRenderer.validateSnapshot(actionable),true);
const actionHost=new FakeElement("host");
const interactionCalls=[];
const actionHandler=async payload=>{
  interactionCalls.push(payload);
  return {ok:true,revision:payload.revision+1};
};
assert.equal(interactiveRenderer.renderNativeSnapshot(actionHost,actionable,[],actionHandler),true);
assert.doesNotMatch(actionHost.childNodes[0].textContent,/本地预览/);
const actionInputs=findAll(actionHost,node=>node.tagName==="input");
actionInputs[0].checked=false;
await actionInputs[0].events.change();
actionInputs[2].value="50";
await actionInputs[2].events.change();
const actionSelect=findTag(actionHost,"select");
actionSelect.value="line";
await actionSelect.events.change();
const actionDetails=findTag(actionHost,"details");
actionDetails.open=false;
await actionDetails.events.toggle();
assert.deepEqual(interactionCalls.map(call=>[call.interaction_id,call.action,call.value]),[
  ["nc_8_2","set_checked",false],
  ["nc_8_4","set_value",50],
  ["nc_8_5","set_value","line"],
  ["nc_8_1","set_open",false],
]);
const badInteractionId=structuredClone(actionable);
badInteractionId.root.children[1].interaction_id="nc_bad";
assert.equal(interactiveRenderer.validateSnapshot(badInteractionId),false);
const interactionIdOnUnsupportedNode=structuredClone(actionable);
interactionIdOnUnsupportedNode.root.children[5].interaction_id="nc_8_6";
assert.equal(interactiveRenderer.validateSnapshot(interactionIdOnUnsupportedNode),false);
const duplicateInteractionId=structuredClone(actionable);
duplicateInteractionId.root.children[2].interaction_id="nc_8_2";
assert.equal(interactiveRenderer.validateSnapshot(duplicateInteractionId),false);
console.log("native-renderer-upstream-interaction-callback-passed");

const tabSnapshot={
  version:1,revision:9,finality:"verified-final",assets:[],files:[],
  root:{tag:"div",children:[
    {tag:"div",control:{kind:"tablist",group:"tg_1"},children:[
      {tag:"button",interaction_id:"nc_9_1",
        control:{kind:"tab",label:"Overview",disabled:false,selected:true,group:"tg_1"}},
      {tag:"button",interaction_id:"nc_9_2",
        control:{kind:"tab",label:"Details",disabled:false,selected:false,group:"tg_1"}},
    ]},
  ]},
};
assert.equal(interactiveRenderer.validateSnapshot(tabSnapshot),true);
const tabHost=new FakeElement("host");
const tabCalls=[];
assert.equal(interactiveRenderer.renderNativeSnapshot(tabHost,tabSnapshot,[],async payload=>{
  tabCalls.push(payload);
  return {ok:true,revision:payload.revision+1,persisted:true};
}),true);
assert.doesNotMatch(tabHost.childNodes[0].textContent,/本地预览/);
const tabList=findTag(tabHost,"div");
const renderedTabs=findAll(tabHost,node=>node.tagName==="button"&&node.attributes.role==="tab");
assert.equal(tabList.attributes.role,"tablist");
assert.equal(renderedTabs.length,2);
assert.equal(renderedTabs[0].attributes["aria-selected"],"true");
assert.equal(renderedTabs[1].attributes["aria-selected"],"false");
await renderedTabs[1].click();
assert.deepEqual(tabCalls.map(call=>[call.interaction_id,call.action,call.value,call.revision]),[
  ["nc_9_2","set_selected",true,9],
]);
assert.equal(renderedTabs[0].attributes["aria-selected"],"false");
assert.equal(renderedTabs[0].tabIndex,-1);
assert.equal(renderedTabs[1].attributes["aria-selected"],"true");
assert.equal(renderedTabs[1].tabIndex,0);
const localTabsHost=new FakeElement("host");
assert.equal(interactiveRenderer.renderNativeSnapshot(localTabsHost,tabSnapshot),true);
assert.match(localTabsHost.childNodes[0].textContent,/本地预览/);
const malformedTabGroup=structuredClone(tabSnapshot);
malformedTabGroup.root.children[0].control.group="unsafe";
assert.equal(interactiveRenderer.validateSnapshot(malformedTabGroup),false);
const arbitraryButton=structuredClone(tabSnapshot);
arbitraryButton.root.children[0].children[1].control.kind="run";
assert.equal(interactiveRenderer.validateSnapshot(arbitraryButton),false);
console.log("native-renderer-semantic-tab-actions-passed");


