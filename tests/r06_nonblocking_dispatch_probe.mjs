import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// Execute the actual request-v6 dispatch-stage helpers, not a second copy of
// their logic. Chrome extension diagnostics may wait indefinitely for a
// background runtime response; such a delay must never pause request startup.
const source = fs.readFileSync("chrome_extension/content_request_v6.js", "utf8");
const start = source.indexOf("  function dispatchElapsedMs(");
const end = source.indexOf("  function dispatchEnter(element) {", start);
assert.ok(start >= 0 && end > start, "request-v6 dispatch helpers must exist");

let now = 100;
const diagnosticReports = [];
const sandbox = {
  performance: { now: () => ++now },
  diagnostic: (_active, stage, extra) => {
    diagnosticReports.push({ stage, extra });
    return new Promise(() => {}); // unresolved background diagnostic ACK
  },
};
const dispatchStage = vm.runInNewContext(
  source.slice(start, end) + "\nrunDispatchStage;", sandbox,
  { timeout: 2000 },
);
const active = { dispatchTimingStartedAt: 90, dispatchStageEvidence: {} };

async function withoutDiagnosticWait(operation) {
  const timeout = new Promise(resolve => setTimeout(() => resolve({ timeout: true }), 200));
  return Promise.race([operation(), timeout]);
}

let promptWrites = 0;
const promptResult = await withoutDiagnosticWait(() =>
  dispatchStage(active, "write_prompt", async () => {
    promptWrites += 1;
    return "prompt-written";
  }),
);
assert.equal(promptResult, "prompt-written", "prompt write cannot wait for diagnostics");
assert.equal(promptWrites, 1);
assert.equal(active.dispatchStageEvidence.write_prompt.status, "completed");

const skipped = await withoutDiagnosticWait(() =>
  dispatchStage(active, "resolve_blocking_interruption", null),
);
assert.equal(skipped, undefined, "optional readiness should not wait for diagnostics");
assert.equal(active.dispatchStageEvidence.resolve_blocking_interruption.status, "skipped");

const failed = await withoutDiagnosticWait(() =>
  dispatchStage(active, "submit_and_confirm", async () => {
    throw new TypeError("send rejected");
  }).then(() => "unexpected-success", error => error.message),
);
assert.equal(failed, "send rejected", "original submission error must propagate");
assert.equal(active.dispatchStageEvidence.submit_and_confirm.status, "failed");
assert.equal(active.dispatchStageEvidence.submit_and_confirm.error_type, "TypeError");

const reportStates = diagnosticReports
  .filter(report => report.stage === "dispatch-stage")
  .map(report => report.extra.request_v6_dispatch_stage_evidence_v1);
assert.ok(reportStates.length >= 5, "started, completed, skipped, failed stages captured");
assert.ok(reportStates.some(x => x.write_prompt?.status === "started"));
assert.ok(reportStates.some(x => x.write_prompt?.status === "completed"));
assert.ok(reportStates.some(x => x.submit_and_confirm?.status === "started"));
assert.ok(reportStates.some(x => x.submit_and_confirm?.status === "failed"));
assert.equal(reportStates.find(x => x.write_prompt?.status === "started").write_prompt.status, "started",
  "prior stage snapshots must not mutate when a later state is recorded");

console.log("R-06 nonblocking browser dispatch diagnostics: pass");
