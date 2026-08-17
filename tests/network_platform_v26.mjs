import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const platformSource = readFileSync(new URL("../chrome_extension/background_platform_v26.js", import.meta.url), "utf8");
const networkSource = readFileSync(new URL("../chrome_extension/background_network_v26.js", import.meta.url), "utf8");

const storage = {};
const storageListeners = [];
const fetchCalls = [];
let fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({ success: true, country_code: "US" }) });
const socketPayloads = [];

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  AbortController,
  navigator: { onLine: true },
  trySendSocket: async payload => { socketPayloads.push({ channel: "try", payload }); return true; },
  sendSocket: async payload => { socketPayloads.push({ channel: "send", payload }); return true; },
  fetch: async (input, init = {}) => {
    fetchCalls.push({ input: String(input), init });
    return fetchHandler(input, init);
  },
  chrome: {
    runtime: {
      async getPlatformInfo() { return { os: "linux", arch: "x86-64", nacl_arch: "x86-64" }; },
    },
    storage: {
      local: {
        async get(defaults = {}) {
          if (typeof defaults === "string") return { [defaults]: storage[defaults] };
          return { ...(defaults || {}), ...storage };
        },
        async set(values) {
          const changes = {};
          for (const [key, value] of Object.entries(values || {})) {
            changes[key] = { oldValue: storage[key], newValue: value };
            storage[key] = value;
          }
          for (const listener of storageListeners) listener(changes, "local");
        },
      },
      onChanged: {
        addListener(listener) { storageListeners.push(listener); },
      },
    },
  },
};
sandbox.self = sandbox;
vm.createContext(sandbox);

vm.runInContext(platformSource, sandbox, { filename: "background_platform_v26.js" });
await new Promise(resolve => setTimeout(resolve, 0));

const platform = sandbox.__CHAT2API_PLATFORM_V26__;
assert.ok(platform, "Platform API must be installed");
const detected = await platform.detect();
assert.equal(detected.os, "linux");
assert.equal(detected.arch, "x86-64");
assert.equal(detected.linux_supported, true);
assert.equal(detected.supported_desktop, true);
assert.equal(storage.platformOs, "linux");
assert.equal(storage.platformLinuxSupported, true);

fetchCalls.length = 0;
await sandbox.fetch("https://server.example/api/extensions/register", {
  method: "POST",
  body: JSON.stringify({ name: "Linux worker", metadata: { existing: true } }),
});
assert.equal(fetchCalls.length, 1);
const registration = JSON.parse(fetchCalls[0].init.body);
assert.equal(registration.metadata.existing, true);
assert.equal(registration.metadata.platform_os, "linux");
assert.equal(registration.metadata.platform_arch, "x86-64");
assert.equal(registration.metadata.platform_linux_supported, true);

vm.runInContext(networkSource, sandbox, { filename: "background_network_v26.js" });
const gate = sandbox.__CHAT2API_NETWORK_GATE_V26__;
assert.ok(gate, "Network gate API must be installed");

// Status calls before the authenticated extension socket is connected must not
// perform an egress lookup. This keeps the network probe behind the server's
// connection_enabled/authentication boundary.
fetchCalls.length = 0;
socketPayloads.length = 0;
await sandbox.trySendSocket({ type: "extension.status", metadata: { existing: true } });
assert.equal(fetchCalls.length, 0, "Disconnected extension status must not trigger IP-country lookup");
assert.equal(socketPayloads.length, 1);
assert.equal(socketPayloads[0].payload.metadata.network_probe_status, "unknown");

fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({ success: true, country_code: "US", ip: "198.51.100.1" }) });
await sandbox.chrome.storage.local.set({ socketState: "connected" });
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(fetchCalls.length, 1, "Accepted socket connection must trigger one egress lookup");
const external = await gate.probe(false);
assert.equal(external.status, "external");
assert.equal(external.country_code, "US");
assert.equal(external.external_ready, true);
assert.equal(storage.networkExternalReady, true);
assert.equal(storage.networkCountryCode, "US");
assert.equal(fetchCalls.length, 1);
assert.equal(Object.prototype.hasOwnProperty.call(storage, "networkPublicIp"), false, "Public IP must not be persisted");

const cached = await gate.probe(false);
assert.equal(cached.external_ready, true);
assert.equal(fetchCalls.length, 1, "Fresh country result must be served from cache");

socketPayloads.length = 0;
await sandbox.trySendSocket({ type: "extension.status", metadata: { existing: true } });
assert.equal(socketPayloads.length, 1);
assert.equal(socketPayloads[0].payload.metadata.existing, true);
assert.equal(socketPayloads[0].payload.metadata.platform_os, "linux");
assert.equal(socketPayloads[0].payload.metadata.network_country_code, "US");
assert.equal(socketPayloads[0].payload.metadata.network_external_ready, true);

fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({ success: true, country_code: "CN" }) });
const mainland = await gate.probe(true);
assert.equal(mainland.status, "china-mainland");
assert.equal(mainland.country_code, "CN");
assert.equal(mainland.external_ready, false);
assert.equal(await gate.allowPrewarm(), false);

fetchHandler = async () => { throw new Error("synthetic lookup failure"); };
const failed = await gate.probe(true);
assert.equal(failed.status, "error");
assert.equal(failed.external_ready, false);
assert.match(failed.error, /synthetic lookup failure/);

sandbox.navigator.onLine = false;
const beforeOfflineFetches = fetchCalls.length;
const offline = await gate.probe(true);
assert.equal(offline.status, "offline");
assert.equal(offline.external_ready, false);
assert.equal(fetchCalls.length, beforeOfflineFetches, "Offline detection must not attempt IP lookup");

console.log("network_platform_v26 VM contract passed");
