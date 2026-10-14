import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/background_model_library_v145.js", "utf8");
const data = {};
const chrome = {
  storage: {
    local: {
      get: async defaults => ({ ...defaults, ...data }),
      set: async patch => Object.assign(data, patch),
    },
    onChanged: { addListener() {} },
  },
  runtime: {
    onMessage: { addListener() {} },
    onStartup: { addListener() {} },
    onInstalled: { addListener() {} },
    getManifest: () => ({ version: "0.22.130" }),
  },
  tabs: { query: async () => [] },
};
const context = {
  chrome,
  console,
  Date,
  URL,
  setTimeout: () => 1,
  clearTimeout: () => {},
  chatTabs: async () => [],
  trySendSocket: async () => true,
  globalThis: null,
};
context.globalThis = context;
vm.runInNewContext(source, context);
const state = context.__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__;
assert.ok(state && typeof state.validationFresh === "function");
const nonce = state.runtimeNonce;
const model = { id: "gpt-5.6-sol", validated: true, validation_revision: 145 };
const common = {
  modelValidationState: "validated",
  modelValidationRevision: 145,
  modelValidationRuntimeNonce: nonce,
  modelValidationBoundTabId: 413,
  modelValidationSocketUpdatedAt: "1",
  socketUpdatedAt: "1",
  modelValidationClientId: "plus-client",
  clientId: "plus-client",
  models: [model],
};
assert.equal(state.validationFresh({ ...common, boundTabId: null }), true,
  "auto-routed Workers with a real validated tab must publish their models");
assert.equal(state.validationFresh({ ...common, boundTabId: 413 }), true);
assert.equal(state.validationFresh({ ...common, boundTabId: 414 }), false);
assert.equal(state.validationFresh({ ...common, models: [{ id: "gpt-5.6-sol" }] }), false,
  "unverified guesses must never populate the live model picker");
assert.equal(state.validationFresh({ ...common, modelValidationSocketUpdatedAt: "0" }), false);

const entry = readFileSync("app/entry.py", "utf8");
assert.match(entry, /install_worker_model_library_v145_patch\(app\)/,
  "production must install the validated Worker model library");
const admin = readFileSync("app/admin.py", "utf8");
assert.match(admin, /api\('\/v1\/models'\)/,
  "the playground must refresh live models rather than hard-code options");
assert.match(admin, /row\.validated===true/,
  "playground options must represent genuinely verified models");
console.log("Worker live model catalog regression tests passed");
