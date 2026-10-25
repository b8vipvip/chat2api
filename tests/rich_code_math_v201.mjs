import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync(new URL("../chrome_extension/content_rich_response_v69.js",import.meta.url),"utf8");
const a=source.indexOf("  function inlineMarkdown(node) {");
const b=source.indexOf("  function inlineChildren(node) {",a);
assert.ok(a>0 && b>a);
class Elem {
  constructor(tag,text,cls=""){this.tagName=tag;this.textContent=text;this.className=cls;
    this.parentElement={tagName:"DIV"};this.classList={contains:name=>cls.split(" ").includes(name)};}
  querySelector(selector){return selector.includes("annotation")?this.annotation:null;}
  getAttribute(name){return name==="data-language"?"":"";}
}
const inline=runInNewContext(source.slice(a,b)+"\ninlineMarkdown",{
  Element:Elem,Node:{TEXT_NODE:3},
  normalizeInline:value=>String(value).replace(/\s+/g," "),
  languageOf:node=>node?.className?.includes("python")?"python":"",
});
test("standalone multiline Python <code> becomes fenced code block",()=>{
  const code=new Elem("CODE","from dataclasses import dataclass\nprint(123)","language-python");
  const result=inline(code);
  assert.ok(result.includes("```python\nfrom dataclasses import dataclass\nprint(123)\n```"));
});
test("KaTeX inline and display math retain LaTeX markers",()=>{
  const math=new Elem("SPAN","","katex");math.annotation={textContent:"R = p\\times n"};
  assert.equal(inline(math),"\\(R = p\\times n\\)");
  const display=new Elem("SPAN","","katex-display");display.annotation={textContent:"C = f + v"};
  assert.ok(inline(display).includes("\\[C = f + v\\]"));
});
