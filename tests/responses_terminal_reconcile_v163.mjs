import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../chrome_extension/content_request_v6.js", import.meta.url), "utf8",
);
const start = source.indexOf("  function completeResponsesBridgeDocument(");
const end = source.indexOf("  async function monitor(active) {", start);
assert.ok(start > 0 && end > start, "terminal helpers must remain in request-v6");
const helpers = source.slice(start, end);

const full = JSON.stringify({
  bridge: "chat2api-responses-tool-v159",
  kind: "tool_calls",
  calls: [{ namespace: null, name: "exec_command", arguments: { cmd: "echo safe" } }],
});
const fragment = '"calls":[{"namespace":null,"name":"exec_command"';
assert.ok(full.includes(fragment));

async function reconcile({
  networkText = fragment,
  candidateText = full,
  responseProtocol = "responses-v108",
  sameTurn = true,
  captureThrows = false,
} = {}) {
  let now = 0;
  let reads = 0;
  const context = {
    JSON,
    Date: { now: () => (now += 300) },
    currentAssistantState: () => ({ isNew: sameTurn, latest: sameTurn ? {} : null }),
    finalNodeText: async () => {
      reads += 1;
      if (captureThrows) throw Error("DOM unavailable");
      return { text: candidateText };
    },
    delay: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(helpers, context);
  const result = await vm.runInContext(
    "reconcileResponsesTerminal({options:{response_protocol:" +
      JSON.stringify(responseProtocol) + "},cancelled:false}," +
      JSON.stringify(networkText) + ")",
    context,
  );
  return { result: JSON.parse(JSON.stringify(result)), reads };
}

const recovered = await reconcile();
assert.equal(recovered.result.text, full);
assert.equal(recovered.result.source, "validated-same-turn-dom-v163");
assert.equal(recovered.reads, 1);

for (const options of [
  { candidateText: full.replace("exec_command", "write_stdin") },
  { candidateText: full.slice(0, -1) },
  { candidateText: full, sameTurn: false },
  { candidateText: full, captureThrows: true },
  { networkText: '"calls":', candidateText: full },
]) {
  const actual = await reconcile(options);
  assert.equal(actual.result.text, options.networkText || fragment, "never promote unverified DOM evidence");
  assert.equal(actual.result.source, "network-sse-terminal-v57");
}

const validNetwork = await reconcile({ networkText: full });
assert.equal(validNetwork.result.text, full);
assert.equal(validNetwork.reads, 0, "complete SSE never consults DOM");
const ordinary = await reconcile({ responseProtocol: "plain-chat" });
assert.equal(ordinary.result.text, fragment);
assert.equal(ordinary.reads, 0, "non-Responses requests retain existing terminal behavior");
console.log("Responses terminal recovery accepts only complete same-turn overlapping bridge JSON");
