import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../chrome_extension/content_native_tool_stream_v63.js", import.meta.url),
  "utf8",
);

function harness() {
  const events = [];
  let listener = null;
  let stopClicked = false;
  const active = {requestId:"req_native_tool_guard_1",cancelled:false};
  const stopButton = {
    disabled:false,
    getAttribute:() => null,
    click() { stopClicked = true; },
  };
  const context = {
    chrome:{
      runtime:{
        sendMessage:async message => {
          if (message?.type === "chat2api.event") events.push(message.event);
          return {ok:true};
        },
      },
    },
    document:{
      querySelector:() => stopButton,
    },
    addEventListener(type, callback) {
      if (type === "message") listener = callback;
    },
    __CHAT2API_REQUEST_CONTENT_V6__:{active},
  };
  vm.createContext(context);
  vm.runInContext(source, context, {filename:"content_native_tool_stream_v63.js"});
  const globalObject = vm.runInContext("globalThis", context);
  return {
    events, active,
    get stopClicked() { return stopClicked; },
    dispatch(detail) {
      assert.equal(typeof listener, "function");
      listener({source:globalObject,data:{
        source:"chat2api-native-tool-stream-v63",
        revision:63,
        ...detail,
      }});
    },
  };
}

test("native GitHub tool call aborts the Worker request and emits an audit event", async () => {
  const h = harness();
  h.dispatch({
    phase:"tool-call",
    tool_name:"github.search_repositories",
    call_id:"native-call-1",
    arguments:"{}",
    status:"running",
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.active.cancelled, true);
  assert.equal(h.stopClicked, true);
  assert.equal(h.active.nativeExternalToolViolation.provider, "github");
  const audit = h.events.find(event => event?.type === "chat.diagnostics");
  assert.ok(audit);
  assert.equal(audit.diagnostics.native_external_tool_violation, true);
  assert.equal(audit.diagnostics.native_external_tool_abort_requested, true);
  assert.equal(audit.diagnostics.native_external_tool_prevented_before_execution, false);
});

test("ordinary built-in web tools are not blocked by the external-account guard", async () => {
  const h = harness();
  h.dispatch({
    phase:"tool-call",
    tool_name:"web.run",
    call_id:"web-call-1",
    arguments:"{}",
    status:"running",
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.active.cancelled, false);
  assert.equal(h.stopClicked, false);
  assert.equal(h.events.some(event => event?.type === "chat.diagnostics"), false);
  assert.equal(h.events.some(event => event?.type === "chat.tool.call"), true);
});
