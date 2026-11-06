import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/background_runtime_preflight_v48.js", import.meta.url), "utf8");

function currentContract(nativeGate = true) {
  return {
    ok: true,
    contract_revision: 71,
    native_tool_stream_revision: 63,
    multimodal_revision: 85,
    terminal_prompt_revision: 88,
    conversation_quota_failover_revision: 95,
    ui_hygiene_revision: 101,
    marker: { bundle: "0.22.288", revision: 71 },
    modules: {
      request_v6: true,
      native_scope_gate_v287: nativeGate,
      rich_response_v69: true,
      model_evidence_v144: true,
      model_evidence_main_v144: true,
      network_stream_recovery_v55: true,
      native_tool_stream_v63: true,
      native_tool_stream_main_v63: true,
      multimodal_v78: true,
      multimodal_v84: true,
      multimodal_v85: true,
      multimodal_main_v78: true,
      terminal_prompt_v88: true,
      conversation_quota_failover_v95: true,
      ui_hygiene_v101: true,
    },
  };
}

async function runScenario({ hotHealWorks, staleScopeGate = false }) {
  let injected = 0;
  let clockAdvance = 0;
  let reloads = 0;
  let baseEnsures = 0;
  const saved = [];
  const worlds = [];
  const injectedFiles = [];

  const chrome = {
    scripting: {
      async executeScript(options) {
        injected += 1;
        worlds.push(options?.world || "ISOLATED");
        injectedFiles.push(...(options?.files || []));
        return [];
      },
    },
    tabs: {
      async sendMessage(_tabId, message) {
        if (message?.type === "chat2api.tool-isolation.preflight") return { ok: true };
        if (message?.type !== "chat2api.runtime.contract.v71") return null;
        // An old request-v6 one-shot controller can coexist with a fresh
        // v287 bundle marker and a freshly injected contract script.
        // It must force a full document reload, not pass the fast path.
        if (staleScopeGate && reloads === 0) return currentContract(false);
        if (hotHealWorks && injected >= 2) return currentContract();
        if (reloads > 0) return currentContract();
        return null;
      },
      async reload() { reloads += 1; },
      async get() { return { status: "loading", url: "https://chatgpt.com/" }; },
    },
    storage: { local: { async set(value) { saved.push(value); } } },
  };

  const context = {
    chrome,
    console,
    Promise,
    Date: staleScopeGate ? {now: () => Date.now() + (clockAdvance += 150)} : Date,
    setTimeout(callback) { return setImmediate(callback); },
    clearTimeout(handle) { clearImmediate(handle); },
    ensureContent: async () => { baseEnsures += 1; return true; },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "background_runtime_preflight_v48.js" });

  await context.ensureContent(123);
  return { injected, reloads, baseEnsures, saved, worlds, injectedFiles, state: context.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__ };
}

const hot = await runScenario({ hotHealWorks: true });
assert.equal(hot.reloads, 0, "a stale tab that can be hot-healed must not be reloaded");
assert.ok(hot.injected >= 2);
assert.equal(hot.worlds[0], "MAIN");
assert.ok(hot.injectedFiles.includes("model_evidence_main_v144.js"));
assert.ok(hot.injectedFiles.includes("content_model_evidence_v144.js"));
assert.equal(hot.state.last?.ok, true);

const stale = await runScenario({ hotHealWorks: true, staleScopeGate: true });
assert.equal(stale.reloads, 1,
  "an old request-v6 listener must be reloaded despite a current bundle marker");
assert.equal(stale.state.last?.ok, true);
assert.equal(stale.state.last?.reloaded, true);

const loading = await runScenario({ hotHealWorks: false });
assert.equal(loading.reloads, 1, "reload remains a bounded fallback when hot-heal cannot establish the epoch");
assert.equal(loading.state.last?.ok, true);

// Exercise the real content contract: a fresh marker cannot prove that an
// old one-shot request-v6 listener was replaced inside a persistent tab.
const contentContractSource = fs.readFileSync(
  new URL("../chrome_extension/content_runtime_contract_v71.js", import.meta.url), "utf8");
const contractContext = {
  __CHAT2API_CONTENT_BUNDLE_MARKER_V71__: {bundle:"0.22.288",revision:71},
  __CHAT2API_REQUEST_CONTENT_V6__: {revision:69},
  document:{documentElement:{getAttribute(){return null;}},readyState:"complete"},
  location:{href:"https://chatgpt.com/"},
  Date,
  chrome:{runtime:{onMessage:{addListener(){},removeListener(){}}}},
};
contractContext.globalThis=contractContext;
vm.createContext(contractContext);
vm.runInContext(contentContractSource, contractContext);
const snapshot=contractContext.__CHAT2API_CONTENT_RUNTIME_CONTRACT_V71__.snapshot;
assert.equal(snapshot().modules.native_scope_gate_v287, false);
contractContext.__CHAT2API_REQUEST_CONTENT_V6__.nativeScopeGateRevision=287;
assert.equal(snapshot().modules.native_scope_gate_v287, true);

console.log("runtime preflight v71 single-owner regression scenarios passed");
