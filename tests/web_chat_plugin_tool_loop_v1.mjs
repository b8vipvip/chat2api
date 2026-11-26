import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("app/chat_client_v1.js", "utf8");
const start = source.indexOf("  function selectedPluginToolDefinitions() {");
const end = source.indexOf("  function renderPluginCatalog() {", start);
assert.ok(start >= 0 && end > start, "plugin tool loop source anchors must exist");
const helperSource = source.slice(start, end);

function makeHarness({selectedPlugins = ["github"], catalog = null, planner = [], toolHandler = null} = {}) {
  let nextId = 0;
  let nextRequest = 0;
  const calls = [];
  const state = {
    selectedPlugins,
    pluginCatalog:catalog || [
      {id:"github",enabled:true,configured:true,connected:true},
      {id:"gmail",enabled:true,configured:true,connected:true},
    ],
    activeRequestRecord:null,
    abortController:null,
    selectedModel:"gpt-6",
    user:{user_id:"test-user-1"},
  };
  const context = {
    state,
    saveHistory(){},
    renderAll(){},
    renderMessages(){},
    setConnection(){},
    recordClientDiagnostic(){},
    id:() => "call-" + (++nextId),
    pluginContextRows(provider, toolName, rows) {
      if (provider === "gmail") {
        return rows.slice(0,3).map(row => ({
          message_id:String(row.id || row.message_id || ""),
          thread_id:String(row.thread_id || ""),
          subject:String(row.subject || ""),
          from:String(row.from || ""),
          date:String(row.date || ""),
          snippet:String(row.snippet || ""),
          body_text:String(row.body_text || "").slice(0,5000),
        }));
      }
      return rows.slice(0,10);
    },
    callPluginTool:async (...args) => {
      calls.push(args);
      return toolHandler ? toolHandler(...args) : {data:[]};
    },
    fetch:async (url, options) => {
      assert.equal(url, "/api/user/chat/completions");
      const body = JSON.parse(options.body);
      assert.equal(body.stream, false);
      assert.ok(body.messages.some(row =>
        row.role === "system" && String(row.content).includes("可用工具定义")
      ));
      const next = planner.shift();
      assert.ok(next !== undefined, "unexpected extra planner request");
      return {ok:true,status:200,json:async() => ({
        choices:[{message:{content:next}}],
      })};
    },
  };
  const api = vm.runInNewContext(
    "(() => { " + helperSource + "\nreturn {" +
      "selectedPluginToolDefinitions,parsePluginToolPlan,validatePluginToolArguments," +
      "runAutonomousPluginToolLoop}; })()",
    context,
  );
  return {
    api,state,calls,
    assistant:{id:"assistant-1",requestId:"req-visible",pending:true,toolEvents:[],lastEventId:0},
    recoveryApi:{createRequestId:() => "req-plan-" + (++nextRequest)},
  };
}

test("only selected and connected plugins are exposed to the model", () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    catalog:[
      {id:"github",enabled:true,configured:true,connected:true},
      {id:"gmail",enabled:true,configured:true,connected:true},
    ],
  });
  const names = JSON.parse(JSON.stringify(
    h.api.selectedPluginToolDefinitions().map(item => item.name)
  ));
  assert.deepEqual(names,["github.search_repositories","github.list_issues"]);
});

test("model autonomously chooses GitHub search, receives tool results, then stops", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.search_repositories","arguments":{"query":"chat2api"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      assert.equal(tool,"search_repositories");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{query:"chat2api"});
      return {data:[{
        full_name:"b8vipvip/chat2api",
        description:"ChatGPT-compatible API bridge",
        url:"https://github.com/b8vipvip/chat2api",
        stars:123,
        language:"Python",
      }]};
    },
  });
  const messages = [{role:"user",content:"请检查 chat2api 仓库"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.plannerRequests,2);
  assert.equal(result.toolCalls,1);
  assert.equal(h.calls.length,1);
  assert.equal(h.assistant.toolEvents[0].status,"completed");
  const toolMessage = messages.find(item => item.role === "tool");
  assert.ok(toolMessage);
  assert.match(toolMessage.content,/b8vipvip\/chat2api/);
  assert.equal(h.state.activeRequestRecord.request_id,"req-plan-1");
});

test("Gmail search IDs are carried into a second model-selected get_message call", async () => {
  const h = makeHarness({
    selectedPlugins:["gmail"],
    planner:[
      '{"tool_calls":[{"name":"gmail.search_messages","arguments":{"query":"from:billing subject:invoice"}}]}',
      '{"tool_calls":[{"name":"gmail.get_message","arguments":{"message_id":"msg-123"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"gmail");
      if (tool === "search_messages") {
        return {data:[{
          id:"msg-123",thread_id:"thread-1",subject:"Invoice 123",
          from:"billing@example.com",snippet:"Invoice attached",
        }]};
      }
      assert.equal(tool,"get_message");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{message_id:"msg-123"});
      return {data:[{
        id:"msg-123",subject:"Invoice 123",from:"billing@example.com",
        body_text:"The invoice total is $42.00.",
      }]};
    },
  });
  const messages = [{role:"user",content:"查找账单邮件并读取金额"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.plannerRequests,3);
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row => row[1]),["search_messages","get_message"]);
  assert.equal(h.assistant.toolEvents.length,2);
  assert.ok(h.assistant.toolEvents.every(event => event.status === "completed"));
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("$42.00")));
});

test("a guessed Gmail message ID is rejected unless returned by this turn's search", async () => {
  const h = makeHarness({
    selectedPlugins:["gmail"],
    planner:[
      '{"tool_calls":[{"name":"gmail.get_message","arguments":{"message_id":"guessed-id"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async () => {
      throw new Error("must not call provider with a guessed message ID");
    },
  });
  const messages = [{role:"user",content:"读取一封邮件"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.toolCalls,1);
  assert.equal(h.calls.length,0);
  assert.equal(h.assistant.toolEvents[0].status,"failed");
  assert.match(h.assistant.toolEvents[0].output,/必须先通过本轮 Gmail 搜索/);
});

test("unselected tools and path-traversal repository arguments fail closed", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"gmail.search_messages","arguments":{"query":"invoice"}}]}',
      '{"tool_calls":[{"name":"github.list_issues","arguments":{"repository":"../private"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async () => {
      throw new Error("must not call an unselected or invalid tool");
    },
  });
  const messages = [{role:"user",content:"查询邮件和仓库 Issue"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.plannerRequests,3);
  assert.equal(result.toolCalls,2);
  assert.equal(h.calls.length,0);
  assert.equal(h.assistant.toolEvents.length,2);
  assert.ok(h.assistant.toolEvents.every(event => event.status === "failed"));
});

test("tool arguments reject extra fields and malformed planner JSON", () => {
  const h = makeHarness({selectedPlugins:["github"]});
  const definition = h.api.selectedPluginToolDefinitions()[0];
  assert.throws(
    () => h.api.validatePluginToolArguments(definition,{query:"chat2api",owner:"other"}),
    /未允许的字段/,
  );
  assert.throws(() => h.api.parsePluginToolPlan("not-json"), /JSON|Unexpected token/);
});
