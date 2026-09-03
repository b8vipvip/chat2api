import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/background_runtime_preflight_v48.js", import.meta.url), "utf8");

function currentContract() {
  return {
    ok: true,
    contract_revision: 71,
    multimodal_revision: 78,
    marker: { bundle: "0.8.20", revision: 71 },
    modules: {
      request_v6: true,
      rich_response_v69: true,
      response_stream_v69: true,
      multimodal_v78: true,
      multimodal_main_v78: true,
    },
  };
}

async function runScenario({ hotHealWorks }) {
  let injected = 0;
  let reloads = 0;
  let baseEnsures = 0;
  const saved = [];
  const worlds = [];

  const chrome = {
    scripting: {
      async executeScript(options) {
        injected += 1;
        worlds.push(options?.world || "ISOLATED");
        return [];
      },
    },
    tabs: {
      async sendMessage(_tabId, message) {
        if (message?.type === "chat2api.tool-isolation.preflight") return { ok: true };
        if (message?.type !== "chat2api.runtime.contract.v71") return null;
        if (hotHealWorks && injected >= 2) return currentContract();
        if (reloads > 0) return currentContract();
        return null;
      },
      async reload() {
        reloads += 1;
      },
      async get() {
        return { status: "loading", url: "https://chatgpt.com/" };
      },
    },
    storage: {
      local: {
        async set(value) {
          saved.push(value);
        },
      },
    },
  };

  const context = {
    chrome,
    console,
    Promise,
    Date,
    setTimeout(callback) {
      callback();
      return 1;
    },
    clearTimeout() {},
    ensureContent: async () => {
      baseEnsures += 1;
      return true;
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "background_runtime_preflight_v48.js" });

  await context.ensureContent(123);
  return { injected, reloads, baseEnsures, saved, worlds, state: context.__CHAT2API_BACKGROUND_RUNTIME_PREFLIGHT_V71__ };
}

const hot = await runScenario({ hotHealWorks: true });
assert.equal(hot.reloads, 0, "a stale tab that can be hot-healed must not be reloaded");
assert.ok(hot.injected >= 2);
assert.equal(hot.worlds[0], "MAIN", "the multimodal/page bridge must be injected into the page MAIN world first");
assert.equal(hot.state.last?.ok, true);
assert.equal(hot.state.last?.hot_healed, true);
assert.equal(hot.state.last?.contract_revision, 71);
assert.equal(hot.state.last?.multimodal_revision, 78);

const loading = await runScenario({ hotHealWorks: false });
assert.equal(loading.reloads, 1, "reload remains a bounded fallback when hot-heal cannot establish the epoch");
assert.equal(loading.state.last?.ok, true, "a current content contract must win even while tabs.get reports loading");
assert.equal(loading.state.last?.reloaded, true);
assert.equal(loading.state.last?.marker?.bundle, "0.8.20");
assert.equal(loading.state.last?.multimodal_revision, 78);

console.log("runtime preflight v71 regression scenarios passed");
