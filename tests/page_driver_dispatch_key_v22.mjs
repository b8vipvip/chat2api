import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const driverUrl = new URL("../chrome_extension/content_page_driver_v22.js", import.meta.url);
const source = readFileSync(driverUrl, "utf8");

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
  sessionStorage: {
    getItem() {
      return null;
    },
  },
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
vm.runInContext(source, sandbox, { filename: "content_page_driver_v22.js" });

assert.equal(constructedEvents.length, 0, "Driver load must not construct keyboard events");
assert.equal(runtimeListeners.length, 1, "Driver should install exactly one verification listener");

const driver = sandbox.__CHAT2API_PAGE_DRIVER_V22__;
assert.ok(driver, "Page Driver global API must be installed");
assert.equal(driver.version, "22.3.0");
assert.equal(typeof driver.dispatchKey, "function");

// Duplicate bootstrap injection must stay idempotent and side-effect free.
vm.runInContext(source, sandbox, { filename: "content_page_driver_v22.js" });
assert.equal(runtimeListeners.length, 1, "Duplicate injection must not install another listener");
assert.equal(constructedEvents.length, 0, "Duplicate injection must not construct keyboard events");

assert.equal(driver.dispatchKey(null, "Escape"), false);
assert.equal(driver.dispatchKey({}, "Escape"), false);
assert.equal(constructedEvents.length, 0, "Invalid targets must not construct keyboard events");

const dispatched = [];
const target = {
  dispatchEvent(event) {
    dispatched.push(event);
    return true;
  },
};

const result = driver.dispatchKey(target, "M", "KeyM", {
  ctrlKey: true,
  shiftKey: true,
  altKey: true,
  metaKey: true,
  repeat: true,
});

assert.equal(result, true);
assert.equal(dispatched.length, 2, "One dispatchKey call must emit exactly two events");
assert.deepEqual(dispatched.map(event => event.type), ["keydown", "keyup"]);
for (const event of dispatched) {
  assert.equal(event.key, "M");
  assert.equal(event.code, "KeyM");
  assert.equal(event.bubbles, true);
  assert.equal(event.cancelable, true);
  assert.equal(event.ctrlKey, true);
  assert.equal(event.shiftKey, true);
  assert.equal(event.altKey, true);
  assert.equal(event.metaKey, true);
  assert.equal(event.repeat, true);
}

const defaultCodeEvents = [];
const defaultCodeTarget = {
  dispatchEvent(event) {
    defaultCodeEvents.push(event);
    return true;
  },
};
assert.equal(driver.dispatchKey(defaultCodeTarget, "Escape"), true);
assert.deepEqual(defaultCodeEvents.map(event => [event.type, event.key, event.code]), [
  ["keydown", "Escape", "Escape"],
  ["keyup", "Escape", "Escape"],
]);

console.log("page_driver_dispatch_key_v22 VM contract passed");
