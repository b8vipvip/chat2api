from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.responses_v108_patch import (
    _input_prompt,
    _query,
    _sources,
    _tool_config,
    install_responses_v108_patch,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_responses_v108_routes_and_native_web_search_contract() -> None:
    app = FastAPI()
    install_responses_v108_patch(app)
    methods = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    assert ("/v1/responses", "POST") in methods
    assert ("/v1/responses/{response_id}", "GET") in methods

    prompt = _input_prompt({
        "instructions": "Use primary sources.",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Find the latest OpenAI update."}]},
        ],
    })
    assert "Use primary sources." in prompt
    assert "Find the latest OpenAI update." in prompt

    tools, forced = _tool_config({"tools": [{"type": "web_search_preview"}], "tool_choice": "required"})
    assert tools == ["web_search"]
    assert forced is True

    with pytest.raises(NotImplementedError, match="external tool types"):
        _tool_config({"tools": [{"type": "function", "name": "fdex_smoke_echo"}]})


def test_responses_v108_normalizes_native_web_query_and_sources() -> None:
    assert _query('slow|site:openai.com September 2025|3650|openai.com\nlength|long') == "site:openai.com September 2025"
    assert _query('search("OpenAI today")') == "OpenAI today"

    metadata = {
        "search_result_groups": [
            {
                "type": "search_result_group",
                "domain": "openai.com",
                "entries": [
                    {"type": "search_result", "url": "https://openai.com/example", "title": "Example"},
                ],
            }
        ]
    }
    assert _sources(metadata) == [{"type": "url", "url": "https://openai.com/example", "title": "Example"}]


def test_responses_v108_emits_openai_responses_stream_event_names() -> None:
    source = read("app/responses_v108_patch.py")
    for token in (
        '"response.created"',
        '"response.output_item.added"',
        '"response.output_item.done"',
        '"response.content_part.added"',
        '"response.output_text.delta"',
        '"response.output_text.done"',
        '"response.content_part.done"',
        '"response.completed"',
        '"response.failed"',
        '"type": "web_search_call"',
        'responses_external_tools_pending',
        'response_protocol": "responses-v108"',
    ):
        assert token in source


def test_responses_model_routing_context_is_installed_before_responses_route() -> None:
    routing = read("app/responses_model_routing_v108_patch.py")
    entry = read("app/entry.py")
    assert 'scope.get("path") != "/v1/responses"' in routing
    assert "model_routing._MODEL_CONTEXT.set(target)" in routing
    assert "v13_patch._target_context.set(target)" in routing
    assert entry.index("install_responses_model_routing_v108_patch(app)") < entry.index("install_responses_v108_patch(app)")


def test_native_tool_stream_v63_parses_websocket_nested_sse_and_tool_lifecycle() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/native_tool_stream_main_v63.js", "utf8");
const posted = [];
const attributes = new Map();

class FakeWebSocket {
  constructor() { this.url = "wss://ws.chatgpt.com/p6/ws/user/test"; }
  send(_data) { return undefined; }
}

class FakeMessageEvent {
  constructor(target, data) { this.target = target; this.currentTarget = target; this._data = data; }
  get data() { return this._data; }
}

const context = {
  console,
  JSON,
  Math,
  String,
  Number,
  Boolean,
  Object,
  Array,
  Date,
  Map,
  Set,
  WeakMap,
  Promise,
  WebSocket: FakeWebSocket,
  MessageEvent: FakeMessageEvent,
  document: { documentElement: { setAttribute(name, value) { attributes.set(name, String(value)); } } },
  postMessage: message => posted.push(message),
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename: "native_tool_stream_main_v63.js"});

const ws = new context.WebSocket();
ws.send(JSON.stringify([{id: 1, command: {type: "subscribe", topic_id: "conversation-turn-test", offset: "0"}}]));

function nested(payload) {
  return `event: delta\ndata: ${JSON.stringify(payload)}\n\n`;
}
function inbound(innerPayload) {
  const frame = JSON.stringify([{
    type: "message",
    topic_id: "conversation-turn-test",
    payload: {type: "conversation-turn-stream", payload: innerPayload},
  }]);
  const event = new context.MessageEvent(ws, frame);
  void event.data;
}
function stream(payload) {
  inbound({type: "stream-item", encoded_item: nested(payload)});
}

stream({v: {message: {
  id: "call-native-1",
  author: {role: "assistant", metadata: {}},
  content: {content_type: "code", text: "slow|site:openai.com September 2025|3650|openai.com\nlength|long\n"},
  status: "finished_successfully",
  recipient: "web.run",
  channel: null,
  metadata: {},
}}});
stream({v: {message: {
  id: "result-native-1",
  author: {role: "tool", name: "web.run", metadata: {real_author: "tool:web.run"}},
  content: {content_type: "text", parts: [""]},
  status: "finished_successfully",
  recipient: "all",
  channel: null,
  metadata: {
    parent_id: "call-native-1",
    search_result_groups: [{type: "search_result_group", domain: "openai.com", entries: [{type: "search_result", url: "https://openai.com/example", title: "Example"}]}],
  },
}}});
stream({v: {message: {
  id: "final-native-1",
  author: {role: "assistant", metadata: {}},
  content: {content_type: "text", parts: ["Hel"]},
  status: "in_progress",
  recipient: "all",
  channel: "final",
  metadata: {},
}}});
stream({p: "/message/content/parts/0", o: "append", v: "lo"});
stream({p: "/message/status", o: "replace", v: "finished_successfully"});
inbound({type: "done"});

assert.equal(attributes.get("data-chat2api-native-tool-stream"), "63");
const call = posted.find(item => item.phase === "tool-call");
const result = posted.find(item => item.phase === "tool-result");
const snapshots = posted.filter(item => item.phase === "final-snapshot");
const completed = posted.find(item => item.phase === "final-complete");
const turnDone = posted.find(item => item.phase === "turn-complete");
assert.equal(call.tool_name, "web.run");
assert.equal(call.call_id, "call-native-1");
assert.match(call.arguments, /site:openai\.com/);
assert.equal(result.tool_name, "web.run");
assert.equal(result.call_id, "call-native-1");
assert.equal(result.metadata.search_result_groups[0].entries[0].url, "https://openai.com/example");
assert.equal(snapshots.at(-1).text, "Hello");
assert.equal(completed.text, "Hello");
assert.equal(turnDone.completion_source, "conversation-turn-stream-done");
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_content_relay_only_emits_bounded_native_protocol_events() -> None:
    source = read("chrome_extension/content_native_tool_stream_v63.js")
    for token in (
        'type: "chat.tool.call"',
        'type: "chat.tool.result"',
        'type: "chat.response.snapshot"',
        'type: "chat.response.completed"',
        'type: "chat.turn.completed"',
        'request_id: requestId',
        'native: true',
    ):
        assert token in source
    for forbidden in ("Authorization", "Cookie", "localStorage", "sessionStorage"):
        assert forbidden not in source


def test_manifest_and_runtime_preflight_require_v63_native_tool_bridge() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    main = manifest["content_scripts"][0]["js"]
    isolated = manifest["content_scripts"][1]["js"]
    assert "native_tool_stream_main_v63.js" in main
    assert "content_native_tool_stream_v63.js" in isolated
    assert main.index("network_stream_main_v55.js") < main.index("native_tool_stream_main_v63.js") < main.index("multimodal_main_v78.js")
    assert isolated.index("content_network_stream_recovery_v55.js") < isolated.index("content_native_tool_stream_v63.js")

    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v71.js")
    assert 'native_tool_stream_revision: 63' in preflight
    assert 'result?.modules?.native_tool_stream_v63' in preflight
    assert 'result?.modules?.native_tool_stream_main_v63' in preflight
    assert 'native_tool_stream_v63:' in contract
    assert 'native_tool_stream_main_v63:' in contract
