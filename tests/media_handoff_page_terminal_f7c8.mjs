import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const code=readFileSync('chrome_extension/content_request_v6.js','utf8');
const listeners=[];
const ctx={
  chrome:{runtime:{onMessage:{
    addListener:cb=>listeners.push(cb),
    removeListener:()=>{},
  }}},
  setTimeout,clearTimeout,
};
runInNewContext(code,ctx);
const terminal=ctx.__CHAT2API_REQUEST_CONTENT_V6__?.contract?.mediaHandoffTerminalReady;
assert.equal(typeof terminal,'function');

const now=40000;
const pending={
  networkStreamSeen:true,
  networkObservedAt:now-23000,
  networkTerminalAt:0, networkTerminalTransportDoneAt:0,
  networkTerminalStreamId:'',networkTerminalTransportStreamId:'',
};
// f7c8: image archive has already delivered 4 JPEGs; network observer is
// still on chunk 80, no EOF/terminal marker and page says 正在生成.
// Static images must not act as an answer completion signal.
assert.equal(terminal(pending,false,true,now),false,
  'visible upstream working status blocks early media completion');
assert.equal(terminal(pending,true,false,now),false,
  'visible Stop/Generating control blocks early media completion');
assert.equal(terminal({...pending,networkObservedAt:now-3200},false,false,now),false,
  'a recent active SSE stream blocks delivery even if controls pause');
assert.equal(terminal({...pending,networkObservedAt:now-8800},false,false,now),true,
  'genuinely quiet completed UI can deliver media without a terminal SSE marker');
assert.equal(terminal({
  ...pending,networkTerminalAt:100,networkTerminalTransportDoneAt:105,
  networkTerminalStreamId:'current',networkTerminalTransportStreamId:'current',
},false,false,now),true,
  'a clean transport terminal can deliver without waiting for quiet SSE');
assert.equal(terminal({networkStreamSeen:false},false,false,now),true,
  'ordinary image-only request without network observer can complete once UI is idle');
assert.equal(terminal({networkStreamSeen:true,networkObservedAt:0},false,false,now),false,
  'missing observer timestamp cannot fabricate a quiet network interval');
assert.ok(code.includes('media-handoff-await-page-terminal'),
  'blocked handoff should be available in diagnostics');
assert.ok(code.includes('media_terminal_blocked_by_page_liveness'),
  'diagnostics must name exact blocked-by-liveness reason');
assert.ok(!/if \(captureMedia && \(mediaReplyRoot \|\| sourceMedia\.length\) && sourceStable && !generating/.test(code),
  'old media-only completion bypass must be removed');
// Production 062f: SSE was silent ~68s, image URLs were stable, browser
// Stop button was absent; 247 separate rich blocks remained outside the
// final candidate and the newest finish toolbar was not after all blocks.
ctx.__CHAT2API_PROBE_SUPERVISOR_V1__ = {snapshot:()=>({
  scene_changed_block_count:569,
  scene_uncovered_block_count:247,
  scene_uncovered_block_chars:8268,
  scene_finish_after_all_blocks:false,
})};
const richPending={...pending,networkObservedAt:now-68662};
assert.equal(terminal(richPending,false,false,now),false,
  'a quiet SSE is not completion while large source reply remains uncovered');
assert.equal(richPending.mediaTerminalSceneFacts?.uncovered,247);
ctx.__CHAT2API_PROBE_SUPERVISOR_V1__ = {snapshot:()=>({
  scene_changed_block_count:569,scene_uncovered_block_count:0,
  scene_uncovered_block_chars:0,scene_finish_after_all_blocks:true,
})};
assert.equal(terminal(richPending,false,false,now+1500),true,
  'a new verified finish toolbar after all blocks permits completion');
ctx.__CHAT2API_PROBE_SUPERVISOR_V1__ = {snapshot:()=>({
  scene_changed_block_count:5,scene_uncovered_block_count:2,
  scene_uncovered_block_chars:24,scene_finish_after_all_blocks:false,
})};
assert.equal(terminal({...pending,networkObservedAt:now-9000},false,false,now+3000),true,
  'short independent image-only replies cannot be indefinitely held by a tiny uncovered footer');
console.log('f7c8 terminal guard: working page and active SSE cannot be completed by static web images');
