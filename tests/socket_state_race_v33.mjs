import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome_extension/background_socket_singleflight_v21.js", import.meta.url), "utf8");
let intervalStarts = 0;
let intervalClears = 0;
const sandbox = {
  console,
  Promise,
  Date,
  WebSocket: { CONNECTING: 0, OPEN: 1 },
  setInterval() { intervalStarts += 1; return 1000 + intervalStarts; },
  clearInterval() { intervalClears += 1; },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`
  let socket = { readyState: WebSocket.OPEN };
  let keepAliveTimer = 7;
  let reconnectCalls = 0;
  let baseConnectCalls = 0;
  let lastState = null;
  async function trySendSocket() { return true; }
  function socketReady() { return Boolean(socket && socket.readyState === WebSocket.OPEN); }
  async function updateState(nextState, socketError = "") { lastState = [nextState, socketError]; }
  function scheduleReconnect() { reconnectCalls += 1; }
  async function connectSocket() { baseConnectCalls += 1; }
  globalThis.readHarness = () => ({ reconnectCalls, baseConnectCalls, lastState, keepAliveTimer });
  globalThis.setSocketState = value => { socket.readyState = value; };
`, sandbox);
vm.runInContext(source, sandbox, { filename: "background_socket_singleflight_v21.js" });

await vm.runInContext('updateState("disconnected", "stale old socket close")', sandbox);
let row = sandbox.readHarness();
assert.deepEqual(Array.from(row.lastState), ["connected", ""], "a stale old onclose must not overwrite a live replacement socket");
assert.equal(sandbox.__CHAT2API_SOCKET_SINGLEFLIGHT_V21__.staleStateDrops, 1);
assert.ok(intervalClears >= 1 && intervalStarts >= 1, "stale close must re-arm the live socket keepalive it just cleared");

vm.runInContext("scheduleReconnect()", sandbox);
row = sandbox.readHarness();
assert.equal(row.reconnectCalls, 0, "a stale close must not schedule reconnect while the replacement socket is open");
assert.equal(sandbox.__CHAT2API_SOCKET_SINGLEFLIGHT_V21__.staleReconnectDrops, 1);

sandbox.setSocketState(3);
await vm.runInContext('updateState("disconnected", "real close")', sandbox);
vm.runInContext("scheduleReconnect()", sandbox);
row = sandbox.readHarness();
assert.deepEqual(Array.from(row.lastState), ["disconnected", "real close"]);
assert.equal(row.reconnectCalls, 1, "a real current-socket close must still reconnect");

console.log("socket_state_race_v33 VM contract passed");
