import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync("app/admin_worker_presentation_v65.js", "utf8");

function extractFunction(name) {
  const needle = `function ${name}(`;
  const start = source.indexOf(needle);
  assert.notEqual(start, -1, `missing ${name}`);
  const brace = source.indexOf("{", start);
  assert.notEqual(brace, -1, `missing ${name} body`);
  let depth = 0;
  for (let index = brace; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated ${name}`);
}

class FakeNode {
  constructor(key) {
    this.dataset = { chat2apiColumnKey: key };
    this.parentNode = null;
  }

  get nextElementSibling() {
    if (!this.parentNode) return null;
    const index = this.parentNode.children.indexOf(this);
    return index >= 0 ? this.parentNode.children[index + 1] || null : null;
  }
}

class FakeParent {
  constructor(keys) {
    this.children = keys.map(key => new FakeNode(key));
    for (const child of this.children) child.parentNode = this;
    this.mutations = 0;
  }

  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }

  insertBefore(node, next) {
    this.mutations += 1;
    const oldIndex = this.children.indexOf(node);
    if (oldIndex >= 0) this.children.splice(oldIndex, 1);
    const nextIndex = this.children.indexOf(next);
    assert.notEqual(nextIndex, -1, "next node must belong to parent");
    this.children.splice(nextIndex, 0, node);
    node.parentNode = this;
    return node;
  }

  appendChild(node) {
    this.mutations += 1;
    const oldIndex = this.children.indexOf(node);
    if (oldIndex >= 0) this.children.splice(oldIndex, 1);
    this.children.push(node);
    node.parentNode = this;
    return node;
  }
}

const EXTRAS = [
  { key: "device_name" },
  { key: "occupancy" },
];
const cellKey = node => String(node?.dataset?.chat2apiColumnKey || "");
const placeExtraNodes = new Function("EXTRAS", "cellKey", `${extractFunction("placeExtraNodes")}; return placeExtraNodes;`)(EXTRAS, cellKey);
const order = ["client_id", "device_id", "device_name", "worker_settings", "occupancy"];

// A correctly ordered DOM must be a fixed point. This is the exact condition
// that v64 violated: it moved the same nodes again and retriggered its observer.
const stable = new FakeParent(order);
placeExtraNodes(stable, order);
assert.equal(stable.mutations, 0, "stable presentation must not emit childList mutations");
assert.deepEqual(stable.children.map(cellKey), order);

// A legacy renderer may rebuild rows with both extra cells at the tail. One
// pass may repair them, but the next observer pass must perform no more moves.
const rebuilt = new FakeParent(["client_id", "device_id", "worker_settings", "device_name", "occupancy"]);
placeExtraNodes(rebuilt, order);
assert.deepEqual(rebuilt.children.map(cellKey), order);
assert.ok(rebuilt.mutations > 0, "misordered DOM should be repaired");
const repairedMutationCount = rebuilt.mutations;
placeExtraNodes(rebuilt, order);
assert.equal(rebuilt.mutations, repairedMutationCount, "second observer pass must converge without new mutations");

console.log("worker_presentation_liveness_v65 DOM convergence contract passed");
