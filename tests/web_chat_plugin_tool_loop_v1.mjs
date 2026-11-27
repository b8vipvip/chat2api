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
  const requests = [];
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
    conversations:[],activeConversationId:"conversation-1",
  };
  const context = {
    state,
    saveHistory(){},
    renderAll(){},
    renderMessages(){},
    renderPluginCatalog(){},
    persistPluginSelection(){},
    updatePluginPickerButton(){},
    loadPluginCatalog:async () => {},
    setConnection(){},
    recordClientDiagnostic(){},
    id:() => "call-" + (++nextId),
    normalizeToolReferences(value) { return Array.isArray(value) ? value.slice(0,8) : []; },
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
      requests.push(body);
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
      "executePluginToolCall,runAutonomousPluginToolLoop,buildPluginPlannerPrompt}; })()",
    context,
  );
  return {
    api,state,calls,requests,
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
  assert.deepEqual(names,[
    "github.search_repositories","github.get_repository","github.list_issues",
    "github.get_issue","github.list_pull_requests","github.get_pull_request",
    "github.get_pull_request_files","github.list_commits","github.search_code","github.get_file_contents",
    "github.list_issue_comments","github.list_pull_reviews","github.list_pull_review_comments","github.list_pull_comments","github.list_branches","github.get_commit","github.list_directory",
    "github.search_issues","github.list_releases","github.list_tags","github.list_workflow_runs","github.list_workflow_jobs","github.list_pull_review_threads",
    "github.list_pull_review_thread_comments",
  ]);
});

test("plugin planner explicitly forbids Worker-native GitHub and connected apps", () => {
  const h = makeHarness({selectedPlugins:["github"]});
  const prompt = h.api.buildPluginPlannerPrompt(
    h.api.selectedPluginToolDefinitions(), 1, 0,
  );
  assert.ok(prompt.includes("Worker 当前 ChatGPT 页面显示原生 Apps、GitHub 或其他连接器"));
  assert.ok(prompt.includes("所有 GitHub/Gmail 访问必须且只能通过本轮明确列出的 chat2api 工具"));
  assert.ok(prompt.includes("可用工具定义"));
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


test("model chains GitHub code search into a grounded file read", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.search_code","arguments":{"query":"repo:acme/project README.md"}}]}',
      '{"tool_calls":[{"name":"github.get_file_contents","arguments":{"repository":"acme/project","path":"README.md","ref":"feature/parser-fix"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      if (tool === "search_code") {
        assert.deepEqual(JSON.parse(JSON.stringify(args)),{query:"repo:acme/project README.md"});
        return {data:[{
          repository:"acme/project",path:"README.md",ref:"feature/parser-fix",
          url:"https://github.com/acme/project/blob/feature/parser-fix/README.md",sha:"a".repeat(40),
        }]};
      }
      assert.equal(tool,"get_file_contents");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{
        repository:"acme/project",path:"README.md",ref:"feature/parser-fix",
      });
      return {data:[{
        repository:"acme/project",path:"README.md",
        url:"https://github.com/acme/project/blob/feature/parser-fix/README.md",
        content:"# Project\n\nThis is the readme.",
        truncated:false,
      }]};
    },
  });
  const messages = [{role:"user",content:"请阅读 acme/project 的 README.md"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.plannerRequests,3);
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row => row[1]),["search_code","get_file_contents"]);
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("This is the readme.")));
  assert.ok(h.assistant.toolEvents.every(event => event.status === "completed"));
});

test("GitHub file reads reject paths not returned by same-turn code search", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.get_file_contents","arguments":{"repository":"acme/project","path":"README.md"}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async () => {
      throw new Error("must not read an ungrounded path");
    },
  });
  const messages = [{role:"user",content:"读取 acme/project 的文件"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.toolCalls,1);
  assert.equal(h.calls.length,0);
  assert.equal(h.assistant.toolEvents[0].status,"failed");
  assert.match(h.assistant.toolEvents[0].output,/必须先通过本轮 GitHub 代码搜索/);
});

test("GitHub issue, pull request and file path arguments are strictly validated", () => {
  const h = makeHarness({selectedPlugins:["github"]});
  const definitions = h.api.selectedPluginToolDefinitions();
  const byName = Object.fromEntries(definitions.map(item => [item.name,item]));
  assert.deepEqual(
    JSON.parse(JSON.stringify(h.api.validatePluginToolArguments(
      byName["github.get_issue"],{repository:"acme/project",issue_number:12},
    ))),
    {repository:"acme/project",issue_number:12},
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(h.api.validatePluginToolArguments(
      byName["github.get_pull_request"],{repository:"acme/project",pull_number:13},
    ))),
    {repository:"acme/project",pull_number:13},
  );
  assert.throws(
    () => h.api.validatePluginToolArguments(
      byName["github.get_issue"],{repository:"acme/project",issue_number:true},
    ),
    /正整数/,
  );
  assert.throws(
    () => h.api.validatePluginToolArguments(
      byName["github.get_file_contents"],{repository:"acme/project",path:"../secrets"},
    ),
    /安全的相对路径/,
  );
});


test("model chains GitHub Issue listing into a detail read using returned numbers", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.list_issues","arguments":{"repository":"acme/project","state":"open"}}]}',
      '{"tool_calls":[{"name":"github.get_issue","arguments":{"repository":"acme/project","issue_number":12}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      if (tool === "list_issues") {
        return {data:[{number:12,title:"Parser fails",state:"open",url:"https://github.com/acme/project/issues/12"}]};
      }
      assert.equal(tool,"get_issue");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{repository:"acme/project",issue_number:12});
      return {data:[{number:12,title:"Parser fails",state:"open",body:"Reproduction steps"}]};
    },
  });
  const messages = [{role:"user",content:"查看 acme/project 的开放 Issue，并读取第一条详情"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row => row[1]),["list_issues","get_issue"]);
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("Reproduction steps")));
});

test("model chains GitHub Pull Request listing into a detail read using returned numbers", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.list_pull_requests","arguments":{"repository":"acme/project","state":"open"}}]}',
      '{"tool_calls":[{"name":"github.get_pull_request","arguments":{"repository":"acme/project","pull_number":15}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      if (tool === "list_pull_requests") {
        return {data:[{number:15,title:"Improve parser",state:"open",url:"https://github.com/acme/project/pull/15"}]};
      }
      assert.equal(tool,"get_pull_request");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{repository:"acme/project",pull_number:15});
      return {data:[{number:15,title:"Improve parser",state:"open",merged:false,changed_files:2}]};
    },
  });
  const messages = [{role:"user",content:"检查 acme/project 的开放 PR"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.toolCalls,2);
  assert.deepEqual(
    h.calls.map(row => row[1]),["list_pull_requests","get_pull_request"],
    "PR detail tool was not dispatched: " + JSON.stringify(h.assistant.toolEvents),
  );
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("changed_files")));
});


test("GitHub tools stay unavailable until the connected account matches admin OAuth scopes", () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    catalog:[{
      id:"github",enabled:true,configured:true,connected:true,
      reauthorization_required:true,reauthorization_mode:"upgrade",
    }],
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(h.api.selectedPluginToolDefinitions())),
    [],
  );
});


test("revoked GitHub OAuth token asks for reconnect without clearing the Web Chat user", async () => {
  const revoked = new Error("GitHub 授权已失效，请重新连接账户");
  revoked.status = 409;
  const h = makeHarness({
    selectedPlugins:["github"],
    toolHandler:async () => { throw revoked; },
  });
  const definitions = h.api.selectedPluginToolDefinitions();
  const result = await h.api.executePluginToolCall(
    {name:"github.search_repositories",arguments:{query:"chat2api"}},
    definitions,h.assistant,
  );
  assert.equal(result.result.ok,false);
  assert.match(result.result.error,/GitHub 授权已失效/);
  assert.equal(h.state.user.user_id,"test-user-1");
  assert.equal(h.state.pluginCatalog[0].reauthorization_required,true);
  assert.equal(h.state.pluginCatalog[0].reauthorization_mode,"reconnect");
  assert.deepEqual(h.state.selectedPlugins,[]);
  assert.equal(h.assistant.toolEvents[0].status,"failed");
});


test("revoked Gmail OAuth token also asks for reconnect without clearing the Web Chat user", async () => {
  const revoked = new Error("Gmail 授权已失效，请重新连接账户");
  revoked.status = 409;
  const h = makeHarness({
    selectedPlugins:["gmail"],
    toolHandler:async () => { throw revoked; },
  });
  const definitions = h.api.selectedPluginToolDefinitions();
  const result = await h.api.executePluginToolCall(
    {name:"gmail.search_messages",arguments:{query:"from:example.com"}},
    definitions,h.assistant,
  );
  assert.equal(result.result.ok,false);
  assert.equal(h.state.user.user_id,"test-user-1");
  assert.equal(h.state.pluginCatalog[1].reauthorization_required,true);
  assert.equal(h.state.pluginCatalog[1].reauthorization_mode,"reconnect");
  assert.deepEqual(h.state.selectedPlugins,[]);
});


test("model chains GitHub Pull Request listing into changed-file patch retrieval", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.list_pull_requests","arguments":{"repository":"acme/project","state":"open"}}]}',
      '{"tool_calls":[{"name":"github.get_pull_request_files","arguments":{"repository":"acme/project","pull_number":15,"page":2}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      if (tool === "list_pull_requests") {
        return {data:[{number:15,title:"Improve parser",state:"open",url:"https://github.com/acme/project/pull/15"}]};
      }
      assert.equal(tool,"get_pull_request_files");
      assert.deepEqual(JSON.parse(JSON.stringify(args)),{repository:"acme/project",pull_number:15,page:2});
      return {data:[{
        number:15,page:2,has_more:true,filename:"src/parser.py",status:"modified",
        additions:3,deletions:1,changes:4,
        url:"https://github.com/acme/project/blob/main/src/parser.py",
        patch_available:true,
        patch:"@@ -1,2 +1,3 @@\n-old\n+new",
      }]};
    },
  });
  const messages = [{role:"user",content:"检查 acme/project 的开放 PR，并查看变更内容"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row => row[1]),["list_pull_requests","get_pull_request_files"]);
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("src/parser.py")));
  assert.ok(messages.some(item => item.role === "tool" && item.content.includes("@@ -1,2 +1,3 @@")));
});



test("GitHub issue pagination continues only when the previous page reports has_more", async () => {
  const h = makeHarness({
    selectedPlugins:["github"],
    planner:[
      '{"tool_calls":[{"name":"github.list_issues","arguments":{"repository":"acme/project","state":"open","page":1}}]}',
      '{"tool_calls":[{"name":"github.list_issues","arguments":{"repository":"acme/project","state":"open","page":2}}]}',
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(provider,"github");
      assert.equal(tool,"list_issues");
      if (args.page === 1) {
        return {
          data:[{number:12,title:"Issue 12",state:"open",url:"https://github.com/acme/project/issues/12"}],
          pagination:{page:1,per_page:20,has_more:true},
        };
      }
      assert.equal(args.page,2);
      return {
        data:[{number:13,title:"Issue 13",state:"open",url:"https://github.com/acme/project/issues/13"}],
        pagination:{page:2,per_page:20,has_more:false},
      };
    },
  });
  const messages = [{role:"user",content:"请检查 acme/project 的全部开放 Issue"}];
  const result = await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key-1",h.recoveryApi,"req-planner-1",
  );
  assert.equal(result.plannerRequests,3);
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row => row[2].page),[1,2]);
  const toolMessages = messages.filter(item => item.role === "tool");
  assert.equal(toolMessages.length,2);
  assert.ok(toolMessages[0].content.includes('"has_more":true'));
  assert.ok(toolMessages[1].content.includes('"has_more":false'));
});

test("GitHub pagination rejects booleans, fractional pages and pages above the cap", () => {
  const h = makeHarness({selectedPlugins:["github"]});
  const definition = h.api.selectedPluginToolDefinitions()
    .find(item => item.name === "github.search_repositories");
  for (const page of [true,1.5,101,0]) {
    assert.throws(
      () => h.api.validatePluginToolArguments(definition,{query:"chat2api",page}),
      /分页页码/,
    );
  }
});


test("GitHub directory traversal provides evidence for reading a file with the same ref", async () => {
  const h = makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.list_directory",arguments:{repository:"acme/project",ref:"main"}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_directory",arguments:{repository:"acme/project",path:"src",ref:"main"}}]}),
      JSON.stringify({tool_calls:[{name:"github.get_file_contents",arguments:{repository:"acme/project",path:"src/main.py",ref:"main"}}]}),
    ],
    toolHandler:async (provider, tool, args) => {
      if (tool === "list_directory") return {data:args.path ? [
        {type:"file",path:"src/main.py",name:"main.py",url:"https://github.com/acme/project/blob/main/src/main.py"},
      ] : [{type:"dir",path:"src",name:"src",url:"https://github.com/acme/project/tree/main/src"}]};
      assert.equal(tool,"get_file_contents");
      assert.equal(args.ref,"main");
      return {data:[{repository:"acme/project",path:"src/main.py",content:"print(123)",url:"https://github.com/acme/project/blob/main/src/main.py"}]};
    },
  });
  const result = await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project main 分支"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(result.toolCalls,3);
  assert.equal(h.calls.length,3);
  assert.match(h.assistant.toolEvents[2].resultPreview,/print\(123\)/);
  assert.equal(h.assistant.references.length,2);
});

test("GitHub comment, review and commit arguments are validated and resource-grounded", async () => {
  const h = makeHarness({
    planner:[
      JSON.stringify({tool_calls:[
        {name:"github.list_issue_comments",arguments:{repository:"acme/project",issue_number:17}},
        {name:"github.list_pull_review_comments",arguments:{repository:"acme/project",pull_number:18}},
        {name:"github.get_commit",arguments:{repository:"acme/project",sha:"abcdef1234567"}},
      ]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => ({data:[{id:123,number:17,body:"Discussion",url:"https://github.com/acme/project/issues/17#issuecomment-123"}]}),
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"查看 acme/project Issue #17 的评论"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0][1],"list_issue_comments");
  assert.equal(h.assistant.toolEvents[0].status,"completed");
  assert.equal(h.assistant.toolEvents[1].status,"failed");
  assert.equal(h.assistant.toolEvents[2].status,"failed");
  const defs=h.api.selectedPluginToolDefinitions();
  assert.throws(()=>h.api.validatePluginToolArguments(defs.find(d=>d.name==="github.list_directory"),{repository:"acme/project",path:"../secret"}),/相对路径/);
});


test("GitHub Actions workflow run results ground subsequent job inspection", async () => {
  const h = makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.list_workflow_runs",arguments:{repository:"acme/project",branch:"main",status:"completed"}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_workflow_jobs",arguments:{repository:"acme/project",run_id:987654321}}]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      if (tool === "list_workflow_runs") return {data:[{
        id:987654321,name:"CI",display_title:"Build",status:"completed",conclusion:"success",
        branch:"main",url:"https://github.com/acme/project/actions/runs/987654321",
      }],pagination:{page:1,per_page:20,has_more:false}};
      assert.equal(tool,"list_workflow_jobs");
      assert.equal(args.run_id,987654321);
      return {data:[{id:4,name:"Tests",status:"completed",conclusion:"success",
        steps:[{name:"pytest",status:"completed",conclusion:"success"}]}]};
    },
  });
  const result=await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project main 分支最近的 GitHub Actions"}],
    h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(result.toolCalls,2);
  assert.deepEqual(h.calls.map(row=>row[1]),["list_workflow_runs","list_workflow_jobs"]);
  assert.match(h.assistant.toolEvents[1].resultPreview,/pytest/);
});

test("GitHub issue search grounds issue and PR references without guessing", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.search_issues",arguments:{query:"is:open bug",repository:"acme/project"}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_threads",arguments:{repository:"acme/project",pull_number:12}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_threads",arguments:{repository:"acme/project",pull_number:12,cursor:"opaque-next"}}]}),
    ],
    toolHandler:async (provider,tool,args) => {
      if(tool==="search_issues") return {data:[{
        repository:"acme/project",number:12,title:"Fix regression",is_pull_request:true,
        url:"https://github.com/acme/project/pull/12",
      }]};
      return {data:[{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",resolved:false,
        path:"src/main.py",url:"https://github.com/acme/project/pull/12#discussion_r1",
        comments:[{body:"Please fix",url:"https://github.com/acme/project/pull/12#discussion_r1"}],
      }],pagination:{page:1,per_page:50,has_more:args.cursor===undefined,next_cursor:args.cursor===undefined?"opaque-next":null}};
    },
  });
  const result=await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"查找 acme/project 中的 open bug"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(result.toolCalls,3);
  assert.equal(h.calls[1][1],"list_pull_review_threads");
  assert.equal(h.calls[2][2].cursor,"opaque-next");
  assert.ok(h.assistant.references.some(row=>row.url.includes("discussion_r1")));
});

test("new GitHub tools reject unsafe filters, cursors and workflow IDs", () => {
  const h=makeHarness({selectedPlugins:["github"]});
  const defs=Object.fromEntries(h.api.selectedPluginToolDefinitions().map(item=>[item.name,item]));
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.search_issues"],{query:"x",repository:"../bad"}),/owner\/repository/);
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.list_workflow_runs"],{repository:"acme/project",status:"nope"}),/status/);
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.list_workflow_jobs"],{repository:"acme/project",run_id:true}),/run ID/);
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.list_pull_review_threads"],{repository:"acme/project",pull_number:12,cursor:"x".repeat(1025)}),/cursor/);
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.list_pull_review_thread_comments"],{
    repository:"acme/project",pull_number:12,thread_id:"PRRT_1",
  }),/cursor/);
  assert.throws(()=>h.api.validatePluginToolArguments(defs["github.list_pull_review_thread_comments"],{
    repository:"acme/project",pull_number:12,thread_id:"bad\nthread",cursor:"opaque",
  }),/thread ID/);
});


test("completed GitHub plugin evidence is available to later turns with stale-data warnings", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[
        {name:"github.list_workflow_jobs",arguments:{repository:"acme/project",run_id:987654321}},
        {name:"github.get_file_contents",arguments:{repository:"acme/project",path:"README.md",ref:"main"}},
      ]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      if (tool === "list_workflow_jobs") return {data:[{name:"Tests",status:"completed",conclusion:"success"}]};
      assert.equal(tool,"get_file_contents");
      assert.equal(args.ref,"main");
      return {data:[{repository:"acme/project",path:"README.md",content:"Previous README",url:"https://github.com/acme/project/blob/main/README.md"}]};
    },
  });
  h.state.conversations=[{
    id:"conversation-1",
    messages:[{
      id:"assistant-previous",role:"assistant",createdAt:"2026-11-27T09:00:00Z",
      toolEvents:[
        {
          toolName:"GitHub · list_issues",callId:"plugin_old",resultId:"plugin_old_result",
          status:"completed",arguments:'{"repository":"acme/project"}',output:"完成",
          resultPreview:'{"tool":"github.list_issues","results":[{"number":9,"title":"Previously found issue"}]}',
          references:[{url:"https://github.com/acme/project/issues/9",title:"Issue #9",snippet:"Earlier result"}],
        },
        {
          toolName:"GitHub · list_workflow_runs",callId:"plugin_run",resultId:"plugin_run_result",
          status:"completed",arguments:'{"repository":"acme/project"}',output:"完成",
          resultPreview:'{"tool":"github.list_workflow_runs","results":[{"id":987654321,"branch":"main"}]}',
        },
        {
          toolName:"GitHub · search_code",callId:"plugin_code",resultId:"plugin_code_result",
          status:"completed",arguments:'{"query":"repo:acme/project README.md"}',output:"完成",
          resultPreview:'{"tool":"github.search_code","results":[{"repository":"acme/project","path":"README.md","ref":"main","url":"https://github.com/acme/project/blob/main/README.md"}]}',
        },
      ],
    }],
  }];
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"根据刚才查到的 issue 和 CI 结果继续分析"}],h.assistant,"key",h.recoveryApi,"req",
  );
  const context=h.requests[0].messages.find(row=>row.role==="user" && String(row.content).includes("历史插件查询证据"));
  assert.ok(context,"previous completed plugin evidence must be included in planner context");
  assert.match(context.content,/Previously found issue/);
  assert.match(context.content,/旧结果|最新状态|重新查询/);
  assert.match(context.content,/外部不可信数据/);
  assert.equal(h.calls.length,2,"prior workflow and code-search evidence should authorize only their exact follow-up reads");
  assert.deepEqual(h.calls.map(row=>row[1]),["list_workflow_jobs","get_file_contents"]);
});


test("GitHub Issue search cannot invent a repository qualifier", async () => {
  const h=makeHarness({
    planner:[JSON.stringify({tool_calls:[{
      name:"github.search_issues",arguments:{query:"repo:unmentioned/secret is:open"},
    }]}),'{"tool_calls":[]}'],
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"搜索我有权限访问的 GitHub issue"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(h.calls.length,0);
  assert.equal(h.assistant.toolEvents[0].status,"failed");
  assert.match(h.assistant.toolEvents[0].output,/repo: 必须来自用户消息或已发现的仓库/);
});


test("GitHub code search cannot invent a repository qualifier", async () => {
  const h=makeHarness({
    planner:[JSON.stringify({tool_calls:[{
      name:"github.search_code",arguments:{query:"repo:unmentioned/secret token"},
    }]}),'{"tool_calls":[]}'],
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"搜索 GitHub 代码中的配置问题"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(h.calls.length,0);
  assert.equal(h.assistant.toolEvents[0].status,"failed");
  assert.match(h.assistant.toolEvents[0].output,/GitHub 搜索中的 repo:/);
});

test("GitHub review thread comments can continue from the exact thread and comment cursor", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.search_issues",arguments:{query:"is:open review",repository:"acme/project"}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_threads",arguments:{repository:"acme/project",pull_number:12}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_thread_comments",arguments:{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",cursor:"comment-cursor-1",
      }}]}),
    ],
    toolHandler:async (provider,tool,args) => {
      if (tool === "search_issues") return {data:[{
        repository:"acme/project",number:12,title:"Review issue",is_pull_request:true,
        url:"https://github.com/acme/project/pull/12",
      }]};
      if (tool === "list_pull_review_threads") return {data:[{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",resolved:false,
        path:"src/main.py",comments_truncated:true,comments_next_cursor:"comment-cursor-1",
        comments:[{id:44,body:"Please fix",url:"https://github.com/acme/project/pull/12#discussion_r44"}],
        url:"https://github.com/acme/project/pull/12#discussion_r44",
      }]};
      assert.equal(tool,"list_pull_review_thread_comments");
      assert.equal(args.thread_id,"PRRT_1");
      assert.equal(args.cursor,"comment-cursor-1");
      return {data:[{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",resolved:false,
        path:"src/main.py",comments_truncated:false,comments_next_cursor:null,
        comments:[{id:45,body:"Fixed in the next commit",in_reply_to_id:44,url:"https://github.com/acme/project/pull/12#discussion_r45"}],
        url:"https://github.com/acme/project/pull/12#discussion_r45",
      }],pagination:{page:1,per_page:50,has_more:false,next_cursor:null}};
    },
  });
  const result=await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project 的 PR #12 审查讨论"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(result.toolCalls,3);
  assert.deepEqual(h.calls.map(row=>row[1]),["search_issues","list_pull_review_threads","list_pull_review_thread_comments"]);
  assert.equal(h.assistant.toolEvents[2].status,"completed");
  assert.match(h.assistant.toolEvents[1].resultPreview,/PRRT_1/);
  assert.match(h.assistant.toolEvents[1].resultPreview,/comment-cursor-1/);
  assert.ok(h.assistant.references.some(row=>row.url.includes("discussion_r45")));
});


test("a prior review thread result authorizes only its own comment continuation", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_thread_comments",arguments:{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",cursor:"comment-cursor",
      }}]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(tool,"list_pull_review_thread_comments");
      assert.equal(args.thread_id,"PRRT_1");
      return {data:[{repository:"acme/project",pull_number:12,thread_id:"PRRT_1",
        comments:[{id:45,body:"Follow-up",url:"https://github.com/acme/project/pull/12#discussion_r45"}],
        url:"https://github.com/acme/project/pull/12#discussion_r45"}]};
    },
  });
  h.state.conversations=[{
    id:"conversation-1",
    messages:[{
      id:"assistant-previous",role:"assistant",createdAt:"2026-11-27T09:00:00Z",
      toolEvents:[{
        toolName:"GitHub · list_pull_review_threads",callId:"plugin_thread",resultId:"plugin_thread_result",
        status:"completed",arguments:'{"repository":"acme/project","pull_number":12}',output:"完成",
        resultPreview:'{"tool":"github.list_pull_review_threads","results":[{"repository":"acme/project","pull_number":12,"thread_id":"PRRT_1","comments_truncated":true,"comments_next_cursor":"comment-cursor"}]}',
      }],
    }],
  }];
  const result=await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"继续读取刚才的 PR 审查讨论"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(result.toolCalls,1);
  assert.equal(h.calls.length,1);
  assert.equal(h.assistant.toolEvents[0].status,"completed");
});


test("GitHub code search requires preserving the exact result branch when reading a file", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.search_code",arguments:{query:"repo:acme/project README.md"}}]}),
      JSON.stringify({tool_calls:[{name:"github.get_file_contents",arguments:{
        repository:"acme/project",path:"README.md",
      }}]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async (provider,tool,args) => {
      assert.equal(tool,"search_code");
      return {data:[{
        repository:"acme/project",path:"README.md",ref:"feature/review-fix",
        url:"https://github.com/acme/project/blob/feature/review-fix/README.md",
      }]};
    },
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project 的 README 文件"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(h.calls.length,1,"a search result on a feature branch must not silently read the default branch");
  assert.equal(h.assistant.toolEvents[1].status,"failed");
  assert.match(h.assistant.toolEvents[1].output,/准确的 repository\/path/);
});


test("GitHub review comment cursors cannot be guessed even after the thread is known", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.search_issues",arguments:{query:"is:open review",repository:"acme/project"}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_threads",arguments:{repository:"acme/project",pull_number:12}}]}),
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_thread_comments",arguments:{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",cursor:"guessed-cursor",
      }}]}),
    ],
    toolHandler:async (provider,tool,args) => {
      if (tool === "search_issues") return {data:[{
        repository:"acme/project",number:12,title:"Review issue",is_pull_request:true,
        url:"https://github.com/acme/project/pull/12",
      }]};
      if (tool === "list_pull_review_threads") return {data:[{
        repository:"acme/project",pull_number:12,thread_id:"PRRT_1",
        comments_truncated:true,comments_next_cursor:"real-cursor",
        url:"https://github.com/acme/project/pull/12",
      }]};
      assert.fail("a guessed review comment cursor must never reach the API");
    },
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project 的 PR #12 审查讨论"}],h.assistant,"key",h.recoveryApi,"req",
  );
  assert.equal(h.calls.length,2);
  assert.equal(h.assistant.toolEvents[2].status,"failed");
  assert.match(h.assistant.toolEvents[2].output,/comments_next_cursor/);
});


test("GitHub tool results explicitly report bounded or omitted records", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.search_repositories",arguments:{query:"chat2api"}}]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async () => ({data:Array.from({length:10},(_,i)=>({
      full_name:"acme/project-" + i,
      description:"x".repeat(1200),
      url:"https://github.com/acme/project-" + i,
    }))}),
  });
  const messages=[{role:"user",content:"搜索 chat2api 相关仓库"}];
  await h.api.runAutonomousPluginToolLoop(
    messages,h.assistant,"key",h.recoveryApi,"req",
  );
  const toolMessage=messages.find(row=>row.role==="tool");
  assert.ok(toolMessage);
  const result=JSON.parse(toolMessage.content);
  assert.equal(result.results_truncated,true);
  assert.ok(result.omitted_results_count > 0);
  assert.equal(h.assistant.toolEvents[0].status,"completed");
  assert.match(h.assistant.toolEvents[0].output,/未传给模型/);
});


test("GitHub review thread history previews retain every thread and its cursor", async () => {
  const h=makeHarness({
    planner:[
      JSON.stringify({tool_calls:[{name:"github.list_pull_review_threads",arguments:{
        repository:"acme/project",pull_number:12,
      }}]}),
      '{"tool_calls":[]}',
    ],
    toolHandler:async () => ({data:Array.from({length:10},(_,i)=>({
      repository:"acme/project",pull_number:12,thread_id:"PRRT_" + i,
      path:"src/file-" + i + ".py",resolved:false,outdated:false,
      comments_truncated:true,comments_next_cursor:"cursor-" + i,
    })),pagination:{page:1,per_page:10,has_more:false,next_cursor:null}}),
  });
  await h.api.runAutonomousPluginToolLoop(
    [{role:"user",content:"检查 acme/project 的 PR #12 全部审查线程"}],h.assistant,"key",h.recoveryApi,"req",
  );
  const preview=JSON.parse(h.assistant.toolEvents[0].resultPreview);
  assert.equal(preview.results.length,10);
  assert.equal(preview.results[9].thread_id,"PRRT_9");
  assert.equal(preview.results[9].comments_next_cursor,"cursor-9");
});
