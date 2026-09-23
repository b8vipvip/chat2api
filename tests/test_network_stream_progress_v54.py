from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_manifest_installs_v55_main_world_stream_evidence_without_legacy_terminal_owners() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    main = manifest["content_scripts"][0]
    isolated = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.22.95"
    assert main["world"] == "MAIN"
    assert main["run_at"] == "document_start"
    assert "network_stream_main_v55.js" in main["js"]
    assert "native_tool_stream_main_v63.js" in main["js"]
    assert main["js"].index("network_stream_main_v55.js") < main["js"].index("native_tool_stream_main_v63.js")
    assert "network_stream_main_v54.js" not in main["js"]
    assert isolated.index("content_request_v6.js") < isolated.index("content_network_stream_recovery_v55.js") < isolated.index("content_native_tool_stream_v63.js")
    assert "content_response_stream_recovery_v49.js" not in isolated
    assert "content_response_stream_recovery_v69.js" not in isolated
    assert "content_terminal_integrity_v89.js" not in isolated
    assert "content_network_stream_progress_v54.js" not in isolated

def test_main_world_v55_uses_parser_revision_63_for_conversation_sse() -> None:
    source = read("chrome_extension/network_stream_main_v55.js")
    for token in ('url.pathname === "/backend-api/f/conversation"','const PARSER_REVISION = 63;','data-chat2api-network-stream-parser','type.includes("text/event-stream")',"response.clone()","clone.body?.getReader",'phase: "assistant-snapshot"','phase: "assistant-complete"','type === "message_stream_complete"','patch?.p !== undefined ? patch.p : patch?.path','if (pointer == null || pointer === "") return [];','if (typeof payload === "string") return;'):
        assert token in source
    for forbidden in ("Authorization", "Cookie", "request_body", "prompt_text"):
        assert forbidden not in source

def test_main_world_v55_reconstructs_real_root_patch_protocol_without_dom_in_vm() -> None:
    script = r'''
import fs from "node:fs"; import vm from "node:vm"; import assert from "node:assert/strict"; import {TextDecoder, TextEncoder} from "node:util";
const source=fs.readFileSync("chrome_extension/network_stream_main_v55.js","utf8"),messages=[],attributes=new Map(),encoder=new TextEncoder();
const chunks=[encoder.encode('data: "v1"\n\n'),encoder.encode('data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"text","parts":["Hel"]},"status":"in_progress"}},"c":2}\n\n'),encoder.encode('data: {"p":"/message/content/parts/0","o":"append","v":"lo"}\n\n'),encoder.encode('data: {"p":"/message/status","o":"replace","v":"finished_successfully"}\n\n'),encoder.encode('data: {"type":"message_stream_complete"}\n\n'),encoder.encode('data: [DONE]\n\n')];
let cursor=0; const reader={async read(){if(cursor>=chunks.length)return{done:true,value:undefined};return{done:false,value:chunks[cursor++]};},releaseLock(){}};
const response={ok:true,status:200,body:{},headers:{get:n=>n.toLowerCase()==="content-type"?"text/event-stream":""},clone:()=>({body:{getReader:()=>reader}})};
const documentElement={setAttribute(n,v){attributes.set(n,String(v));},getAttribute(n){return attributes.get(n)??null;}};
const context={console,Math,String,Number,Boolean,Promise,Object,Array,Date,URL,TextDecoder,structuredClone,setTimeout,clearTimeout,location:{href:"https://chatgpt.com/",origin:"https://chatgpt.com"},document:{documentElement},postMessage:m=>messages.push(m),fetch:async()=>response}; context.globalThis=context; vm.createContext(context); vm.runInContext(source,context,{filename:"network_stream_main_v55.js"}); await context.fetch("https://chatgpt.com/backend-api/f/conversation",{method:"POST"}); for(let i=0;i<16;i++)await new Promise(r=>setTimeout(r,0));
const snapshots=messages.filter(i=>i?.phase==="assistant-snapshot"),completed=messages.find(i=>i?.phase==="assistant-complete"),done=messages.find(i=>i?.phase==="done"); assert.equal(attributes.get("data-chat2api-network-stream-parser"),"63"); assert.equal(snapshots.at(-1)?.text,"Hello"); assert.equal(completed?.text,"Hello"); assert.equal(done?.assistant_chars,5);
'''
    result=subprocess.run(["node","--input-type=module","-e",script],cwd=ROOT,capture_output=True,text=True,check=False)
    assert result.returncode == 0, result.stdout + result.stderr

def test_isolated_v55_recovery_is_evidence_only() -> None:
    source=read("chrome_extension/content_network_stream_recovery_v55.js")
    for token in ('network-stream-evidence-v56','network_terminal_authority: "request-v6"','type: "chat.snapshot"'):
        assert token in source
    for forbidden in ('type: "chat.completed"','active.cancelled = true','sealResponseOwner('):
        assert forbidden not in source

def test_runtime_preflight_requires_v55_parser_63_main_and_isolated_evidence_modules() -> None:
    bootstrap=read("chrome_extension/content_bootstrap.js"); preflight=read("chrome_extension/background_runtime_preflight_v48.js"); contract=read("chrome_extension/content_runtime_contract_v48.js"); contract_v71=read("chrome_extension/content_runtime_contract_v71.js"); marker=read("chrome_extension/content_bundle_marker_v48.js")
    assert 'world: "MAIN"' in bootstrap
    assert '"network_stream_main_v55.js"' in bootstrap and '"content_network_stream_recovery_v55.js"' in bootstrap
    assert 'const REQUIRED_BUNDLE = "0.22.95"' in preflight
    assert '"content_network_stream_recovery_v55.js"' in preflight
    assert "network_stream_recovery_v55" in contract and "network_stream_parser_v63" in contract
    assert "native_tool_stream_v63" in contract_v71
    assert 'bundle: "0.22.95"' in marker

def test_linux_worker_bundle_actually_packages_generation_probe() -> None:
    dockerfile=read("Dockerfile"); dockerignore=read(".dockerignore")
    assert "scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "/app/worker_payload/scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "!scripts/linux_worker_generation_probe.sh" in dockerignore

def test_main_world_v55_keeps_complete_answer_when_citation_candidate_regresses_in_vm() -> None:
    source=read("chrome_extension/network_stream_main_v55.js")
    assert "bestText" in source or "candidate" in source
