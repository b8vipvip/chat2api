import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../app/chat_native_renderer_v1.js", import.meta.url), "utf8");
class FakeElement {
  constructor(tagName) { this.tagName=tagName; this.childNodes=[]; this.style={}; this.attributes={}; }
  appendChild(node) { this.childNodes.push(node); return node; }
  replaceChildren(...nodes) { this.childNodes=nodes; }
  setAttribute(name,value) { this.attributes[name]=value; }
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
  files:[],
  root:{tag:"div",children:[
    {tag:"svg",attrs:{viewBox:"0 0 10 10"},children:[
      {tag:"path",attrs:{d:"M0 0 L10 10",stroke:"#123456"}}
    ]},
    {tag:"math",children:[{tag:"mrow",children:[{tag:"mi",text:"x"},{tag:"mo",text:"="},{tag:"mn",text:"1"}]}]},
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

const renderedHost = new FakeElement("host");
assert.equal(renderer.renderNativeSnapshot(renderedHost,good),true);
function findTag(node,tag) {
  if (node && node.tagName === tag) return node;
  for (const child of node?.childNodes || []) {
    const found = findTag(child,tag);
    if (found) return found;
  }
  return null;
}
const renderedLink = findTag(renderedHost,"a");
assert.ok(renderedLink);
assert.equal(renderedLink.href,"https://example.org/citation");
assert.equal(renderedLink.target,"_blank");
assert.equal(renderedLink.rel,"noopener noreferrer nofollow");
assert.equal(renderedLink.referrerPolicy,"no-referrer");

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
