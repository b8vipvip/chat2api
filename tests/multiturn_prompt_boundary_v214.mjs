import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync("chrome_extension/content_request_v6.js","utf8");
const begin=src.indexOf("  function assistantOnlyReplyText(");
const end=src.indexOf("\n  async function emit(",begin);
assert.ok(begin>0&&end>begin);
const context={normalize(value){return String(value||"").replace(/[\u200B-\u200D\uFEFF]/g,"")
  .replace(/\s+/g," ").trim().normalize("NFC");}};
vm.runInNewContext(src.slice(begin,end)+"\nthis.strip=assistantOnlyReplyText;",context);
const strip=context.strip;
const oldA="项目当前最值得关注的设计是本地实时处理、云端音频理解和远程 GPU 重计算的混合架构，能够同时支持多模型服务商及音频理解。";
const oldB="在没有 GPU 的低配服务器上依然能够部署核心服务，而将计算成本较高的任务交给其他资源，并保留灵活的故障回退方式。";
const previous="# 项目架构报告\n\n"+oldA+"\n\n"+oldB+"\n\n其他事项：上游音频服务、客户端实现、部署管理。";
const recent="在全网找出20个同类功能的开源项目并列出差异。";
const prompt="External account-connected apps are disabled.\n\n[User]\n仓库地址请分析\n\n[Assistant]\n"+previous+"\n\n[User]\n"+recent;
const normalized=context.normalize(prompt);
const state={prompt,promptNormalized:normalized};
const reply="#### You said:\n\n[User]\n仓库地址请分析\n\n[Assistant]\n"+
  "#### You said:\n[User]\n仓库地址请分析\n\n#### ChatGPT said:\n\n"+previous+
  "\n\n[User]\n"+recent+"\n\n#### ChatGPT said:\n\n"+
  "# 20 个开源项目总览\n\n![图片](https://www.google.com/s2/favicons?domain=https://github.com&sz=32)GitHub\n\n| 项目 | 相似度 |\n| --- | --- |\n| Pipecat | 94 |";
const result=strip(state,reply);
assert.equal(result.multiTurn,true);
assert.equal(result.stripped,true);
assert.ok(result.corroborated>=2);
assert.ok(result.text.startsWith("# 20 个开源项目总览"));
assert.ok(!result.text.includes("#### You said"));
assert.ok(!result.text.includes(oldB));
assert.ok(result.text.includes("![图片](https://www.google.com/s2/favicons"));
const incomplete=strip(state,reply.slice(0,reply.lastIndexOf("#### ChatGPT said:")));
assert.equal(incomplete.pending,true);
assert.equal(incomplete.text,"");
const forged=reply.replace(recent,"不同于本次请求的用户输入");
assert.notEqual(strip(state,forged).stripped,true,"Cannot remove transcript without latest-user proof");
const normal="# 普通助手正文\n\n这是新的回复，不含任何用户回显。";
assert.equal(strip(state,normal).text,normal);
console.log("Multi-turn prompt-echo proof and citation markdown regression passed");
