import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source=readFileSync("chrome_extension/content_rich_response_v69.js","utf8");
class Element {}
const ctx={Element,Node:{TEXT_NODE:3},getComputedStyle:()=>({display:"block",visibility:"visible"})};
runInNewContext(source,ctx);
const rich=ctx.__CHAT2API_RICH_RESPONSE_V69__;
const before="以下程序仅使用Python标准库，可以直接保存为 microalgae_model.py，使用Python 3运行。\n\n支持修改电价、培养合格率、课程上座率、团体课程数量以及礼盒销量；包含严格数值范围校验。\n\nPython";
const after="程序采用期望人数和期望合格批次进行经营测算，实际排班、采购和样品分配应使用整数并保留安全余量。\n\n## 十、月度成本与12个月现金流";
const code="import math\n\ndef revenue(amount):\n    value = float(amount)\n    return math.floor(value)";
const fence="```python\n"+code+"\n```";
const image="![生成的图像](https://example.test/api/media/med_abc/secret)";
const sourceText="# 年度商业报告\n\n"+image+"\n\n"+before+"\n\n"+after+"\n\n| 月份 | 预算 |\n| --- | --- |\n| 1 | 5000 |";
const enriched="# 年度商业报告\n\n"+image+"\n\n"+before+"\n\n"+fence+"\n\n"+after+"\n\n| 月份 | 预算 |\n| --- | --- |\n| 1 | 5000 |";
const result=rich.spliceVerifiedCodeIntoCanonical(sourceText,enriched,[code]);
assert.equal(result.integrated,1);
assert.equal(result.reason,"verified-editor-anchor-insert");
assert.ok(result.text.includes("\n\n"+fence+"\n\n"));
assert.ok(result.text.indexOf(before)<result.text.indexOf(code));
assert.ok(result.text.indexOf(code)<result.text.indexOf(after));
assert.equal(result.text.match(/https:\/\/example\.test\/api\/media\//g)?.length,1);
assert.ok(result.text.includes("| 1 | 5000 |"));
assert.equal(rich.spliceVerifiedCodeIntoCanonical(result.text,enriched,[code]).text,result.text,
  "re-sending the same event cannot duplicate the verified code");
const repeated=sourceText+"\n\n"+before+"\n\n"+after;
const ambiguous=rich.spliceVerifiedCodeIntoCanonical(repeated,enriched,[code]);
assert.equal(ambiguous.integrated,0);
assert.equal(ambiguous.reason,"code-position-unverified");
assert.equal(ambiguous.text,repeated);
const untrusted=rich.spliceVerifiedCodeIntoCanonical(sourceText,
  enriched.replace("import math","import turtle"),[code]);
assert.equal(untrusted.integrated,0);
assert.equal(untrusted.text,sourceText);
console.log("v299 canonical code splice at exact unique assistant paragraphs preserves all archived images, formulas and tables");

const rawButUnfenced=sourceText.replace("\n\n"+after,
  "\n\n"+code+"\n\n"+after);
const falsePositive=rich.spliceVerifiedCodeIntoCanonical(rawButUnfenced,enriched,[code]);
assert.equal(falsePositive.integrated,0,
  "a CodeMirror substring in plain prose must not count as delivered code");
assert.equal(falsePositive.reason,"editor-code-plain-text-ambiguous");
assert.equal(falsePositive.text,rawButUnfenced);
const twoEditors=rich.spliceVerifiedCodeIntoCanonical(sourceText,enriched,[code,code]);
assert.equal(twoEditors.integrated,0,
  "identical text does not identify two separate editor positions");
assert.equal(twoEditors.reason,"duplicate-editor-text-ambiguous");
const doubleFence=sourceText.replace("\n\n"+after,
  "\n\n"+fence+"\n\n"+fence+"\n\n"+after);
const ambiguousFences=rich.spliceVerifiedCodeIntoCanonical(doubleFence,enriched,[code]);
assert.equal(ambiguousFences.integrated,0);
assert.equal(ambiguousFences.reason,"existing-code-fence-ambiguous");


// Regression: real Python may contain multiple intentional blank lines.
// Passing the enriched reply through normalizeOutput would remove one and
// prevent exact matching to the read-only full CodeMirror document.
const codeWithBlanks="import math\n\n\n\ndef revenue(amount):\n    return math.floor(amount)";
const rawEnriched=enriched.replace(code,codeWithBlanks);
const withBlankLines=rich.spliceVerifiedCodeIntoCanonical(
  sourceText,rawEnriched,[codeWithBlanks]);
assert.equal(withBlankLines.integrated,1);
assert.ok(withBlankLines.text.includes("import math\n\n\n\ndef revenue(amount):"),
  "verified Python blank lines must survive the canonical merge unchanged");
assert.equal(rich.spliceVerifiedCodeIntoCanonical(
  withBlankLines.text,rawEnriched,[codeWithBlanks]).text,withBlankLines.text);
assert.ok(source.includes("const enriched=blockMarkdown(clone);"),
  "full editor source may never be globally normalized before exact merge");
