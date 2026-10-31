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
  querySelectorAll() { return []; }
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
  Element,Node:{TEXT_NODE:TEXT},Date,setTimeout,clearTimeout,
  getComputedStyle:()=>({display:'grid',visibility:'visible',gap:'12px',
    fontSize:'16px',fontWeight:'400',gridTemplateColumns:'120px 120px',
    flexDirection:'row',textAlign:'left'}),
  chrome:{runtime:{sendMessage:async message=>{events.push(message);return {ok:true}}}},
};
runInNewContext(source,scope);
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

// Untrusted page-wide or cross-user scope cannot be promoted.
const mainScope=probe.captureData({...active,nativeVerifiedReplyScope:main});
assert.equal(mainScope.complete,false);
assert.equal(mainScope.scope,'candidate_only');
const contaminated=new Element('DIV',[first,new Element('USER')]);
const foreignPage=new Element('MAIN',[contaminated]);
const foreign=probe.captureData({...active,nativeVerifiedReplyScope:contaminated});
assert.equal(foreign.complete,false);
console.log('Native DOM evidence: verified sibling cards, excluded user/page scope, private URL redaction');
