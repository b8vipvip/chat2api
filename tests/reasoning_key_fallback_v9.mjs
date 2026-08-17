import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const reasoningUrl = new URL("../chrome_extension/content_reasoning_v7.js", import.meta.url);
const source = readFileSync(reasoningUrl, "utf8");

// Keep production source unchanged. The VM-only hook exposes the internal key()
// closure so this contract can execute the exact Driver-first/fallback logic.
const instrumented = source.replace(
  /\n\}\)\(\);\s*$/,
  '\n  globalThis.__CHAT2API_REASONING_VM_V9__ = Object.freeze({ key });\n})();',
);
assert.notEqual(instrumented, source, "Reasoning VM export hook must attach before the final IIFE close");

const runtimeListeners = [];
const constructedEvents = [];

class FakeKeyboardEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.key = init.key;
    this.code = init.code;
    this.bubbles = Boolean(init.bubbles);
    this.cancelable = Boolean(init.cancelable);
    this.ctrlKey = Boolean(init.ctrlKey);
    this.shiftKey = Boolean(init.shiftKey);
    this.altKey = Boolean(init.altKey);
    this.metaKey = Boolean(init.metaKey);
    this.repeat = Boolean(init.repeat);
    constructedEvents.push(this);
  }
}

const sandbox = {
  console,
  KeyboardEvent: FakeKeyboardEvent,
  setTimeout,
  clearTimeout,
  chrome: {
    runtime: {
      onMessage: {
        addListener(listener) {
          runtimeListeners.push(listener);
        },
      },
    },
  },
};

vm.createContext(sandbox);
vm.runInContext(instrumented, sandbox, { filename: "content_reasoning_v7.js" });

assert.equal(constructedEvents.length, 0, "Reasoning controller load must not construct keyboard events");
assert.equal(runtimeListeners.length, 1, "Reasoning controller should install exactly one runtime listener");
assert.ok(sandbox.__CHAT2API_REASONING_VM_V9__, "VM hook must expose the internal key helper");
assert.equal(typeof sandbox.__CHAT2API_REASONING_VM_V9__.key, "function");

const key = sandbox.__CHAT2API_REASONING_VM_V9__.key;

function makeTarget() {
  const dispatched = [];
  return {
    dispatched,
    dispatchEvent(event) {
      dispatched.push(event);
      return true;
    },
  };
}

function clearConstructedEvents() {
  constructedEvents.splice(0, constructedEvents.length);
}

// Driver success is authoritative: the local fallback must not run at all.
{
  clearConstructedEvents();
  const target = makeTarget();
  const calls = [];
  const extra = { ctrlKey: true, shiftKey: true, altKey: true, metaKey: true, repeat: true };
  sandbox.__CHAT2API_PAGE_DRIVER_V22__ = {
    dispatchKey(receivedTarget, name, code, receivedExtra) {
      calls.push({ receivedTarget, name, code, receivedExtra });
      return true;
    },
  };

  assert.equal(key(target, "M", "KeyM", extra), true);
  assert.equal(calls.length, 1, "Driver success path must call dispatchKey exactly once");
  assert.equal(calls[0].receivedTarget, target);
  assert.equal(calls[0].name, "M");
  assert.equal(calls[0].code, "KeyM");
  assert.equal(calls[0].receivedExtra, extra, "Reasoning must forward the modifier object unchanged");
  assert.equal(target.dispatched.length, 0, "Driver success must suppress the local KeyboardEvent fallback");
  assert.equal(constructedEvents.length, 0, "Driver success must construct zero local keyboard events");
}

// Driver false means the Driver declined the write; local fallback must run once.
{
  clearConstructedEvents();
  const target = makeTarget();
  let calls = 0;
  sandbox.__CHAT2API_PAGE_DRIVER_V22__ = {
    dispatchKey() {
      calls += 1;
      return false;
    },
  };

  assert.equal(key(target, "Enter", "Enter", { ctrlKey: true }), true);
  assert.equal(calls, 1, "Driver false path must call dispatchKey exactly once");
  assert.equal(target.dispatched.length, 2, "Driver false path must emit exactly one local keydown+keyup pair");
  assert.deepEqual(target.dispatched.map(event => event.type), ["keydown", "keyup"]);
  assert.equal(constructedEvents.length, 2, "Driver false path must construct exactly two local keyboard events");
  for (const event of target.dispatched) {
    assert.equal(event.key, "Enter");
    assert.equal(event.code, "Enter");
    assert.equal(event.bubbles, true);
    assert.equal(event.cancelable, true);
    assert.equal(event.ctrlKey, true);
  }
}

// Driver exceptions are contained; they must trigger exactly one local fallback.
{
  clearConstructedEvents();
  const target = makeTarget();
  let calls = 0;
  sandbox.__CHAT2API_PAGE_DRIVER_V22__ = {
    dispatchKey() {
      calls += 1;
      throw new Error("synthetic driver failure");
    },
  };

  assert.equal(key(target, "Escape", "Escape"), true);
  assert.equal(calls, 1, "Driver throw path must call dispatchKey exactly once");
  assert.equal(target.dispatched.length, 2, "Driver throw path must emit exactly one local keydown+keyup pair");
  assert.deepEqual(target.dispatched.map(event => event.type), ["keydown", "keyup"]);
  assert.equal(constructedEvents.length, 2, "Driver throw path must construct exactly two local keyboard events");
}

// Missing Driver keeps the historical local implementation available.
{
  clearConstructedEvents();
  const target = makeTarget();
  sandbox.__CHAT2API_PAGE_DRIVER_V22__ = null;

  assert.equal(key(target, "Home", "Home"), true);
  assert.equal(target.dispatched.length, 2, "Missing Driver must emit exactly one local keydown+keyup pair");
  assert.deepEqual(target.dispatched.map(event => event.type), ["keydown", "keyup"]);
  assert.equal(constructedEvents.length, 2, "Missing Driver must construct exactly two local keyboard events");
}

// With neither a usable Driver result nor a dispatchable target, key() must fail cleanly.
{
  clearConstructedEvents();
  sandbox.__CHAT2API_PAGE_DRIVER_V22__ = { dispatchKey: () => false };
  assert.equal(key(null, "Escape", "Escape"), false);
  assert.equal(key({}, "Escape", "Escape"), false);
  assert.equal(constructedEvents.length, 0, "Invalid fallback targets must construct zero keyboard events");
}

console.log("reasoning_key_fallback_v9 VM contract passed");
