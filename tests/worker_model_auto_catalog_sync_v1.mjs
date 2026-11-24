import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "chrome_extension", "background_model_library_v145.js"), "utf8");
const stored = {
  clientId: "client-auto-sync",
  boundTabId: 101,
  socketState: "connected",
  socketUpdatedAt: "2026-11-24T10:00:00.000Z",
  modelValidationState: "pending",
  modelValidationRevision: 0,
  modelObservedChoices: [],
  models: [],
};
const published = [];
let automaticMessages = 0;
let selectMessages = 0;
let busy = false;
const listeners = {};
const context = {
  URL,
  Date,
  Math,
  Promise,
  Set,
  Array,
  String,
  Number,
  Boolean,
  Object,
  JSON,
  Error,
  RegExp,
  console,
  setTimeout,
  clearTimeout,
  crypto: { randomUUID: () => "test-runtime-nonce" },
  chatTabs: async () => [{ id: 101, url: "https://chatgpt.com/" }],
  trySendSocket: async payload => { published.push(payload); return true; },
  chrome: {
    storage: {
      local: {
        get: async defaults => ({ ...defaults, ...stored }),
        set: async patch => Object.assign(stored, patch),
      },
    },
    tabs: {
      get: async id => ({ id, url: "https://chatgpt.com/" }),
      query: async () => [{ id: 101, url: "https://chatgpt.com/" }],
      sendMessage: async (_id, message) => {
        if (message.type === "chat2api.models.validate.v145") {
          assert.equal(message.automatic, true);
          automaticMessages += 1;
          if (busy) return { ok: false, code: "model_validation_busy", error: "composer active" };
          return {
            ok: true,
            data: {
              current_model: "gpt-6",
              validated_at: "2026-11-24T10:01:00.000Z",
              observed_models: [
                { id: "gpt-6", label: "GPT-6", enabled: true, selected: true },
                { id: "gpt-6-reasoning", label: "GPT-6 Reasoning", enabled: true, selected: false },
                { id: "retired-model", label: "Retired model", enabled: false, selected: false },
              ],
              models: [],
            },
          };
        }
        if (message.type === "chat2api.model.select.v145") selectMessages += 1;
        return { ok: false, error: "unexpected message" };
      },
      onUpdated: { addListener: fn => { listeners.updated = fn; } },
      onActivated: { addListener: fn => { listeners.activated = fn; } },
    },
    scripting: { executeScript: async () => [] },
    runtime: {
      getManifest: () => ({ version: "0.22.300" }),
      onStartup: { addListener: fn => { listeners.startup = fn; } },
      onInstalled: { addListener: fn => { listeners.installed = fn; } },
      onMessage: { addListener: fn => { listeners.message = fn; } },
    },
  },
};
vm.runInNewContext(source, context, { filename: "background_model_library_v145.js" });
const authority = context.__CHAT2API_BACKGROUND_MODEL_LIBRARY_V145__;
assert.ok(authority && typeof authority.syncObservedChoices === "function");
const result = await authority.syncObservedChoices("test-browser-sync");
assert.equal(result.ok, true);
assert.equal(stored.modelValidationState, "discovered");
assert.deepEqual(stored.modelObservedChoices.map(row => row.id), ["gpt-6", "gpt-6-reasoning"]);
assert.ok(stored.modelObservedChoices.every(row => row.selectable === true && row.validated === false));
assert.equal(stored.models.length, 0, "observations must not be promoted to verified models");
assert.equal(selectMessages, 0, "automatic catalog sync must never select a model");
assert.equal(automaticMessages, 1);
assert.ok(published.some(item =>
  item.type === "extension.status" &&
  item.metadata.model_validation_state === "discovered" &&
  item.metadata.model_observed_choices.length === 2
));

busy = true;
stored.modelValidationState = "pending";
stored.modelObservedChoices = [];
stored.modelValidationUpdatedAt = "";
authority.lastDiscoveryAt = 0;
const deferred = await authority.syncObservedChoices("busy-browser-sync");
assert.equal(deferred.deferred, true);
assert.equal(stored.modelValidationState, "pending", "busy inspection must not poison the catalog state");
assert.equal(selectMessages, 0);
console.log("worker-model-auto-catalog-sync-v1-passed");
