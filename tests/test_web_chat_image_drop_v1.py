"""Node VM regression for the Web Chat image drag/drop adapter."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "chat_image_drop_v1.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for drag/drop event harness")
def test_file_drag_drop_depth_prevention_and_cleanup():
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
(async () => {
const source = fs.readFileSync(process.argv[1], "utf8");
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn) {
    const rows = this.listeners.get(type) || [];
    rows.push(fn);
    this.listeners.set(type, rows);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x !== fn));
  }
  dispatch(type, event) {
    for (const fn of this.listeners.get(type) || []) fn(event);
  }
}
const window = {};
vm.runInNewContext(source, {window, Array, Object, TypeError});
const api = window.Chat2APIImageDropV1;
assert(api && api.isFileDrag({dataTransfer:{types:["Files"]}}));
assert(!api.isFileDrag({dataTransfer:{types:["text/plain"]}}));
const target = new Target();
const document = new Target();
const active = [];
const received = [];
const destroy = api.install(target, document, {
  onFiles: files => received.push(files),
  onActive: value => active.push(value),
});
const file = {name:"reference.png", type:"image/png"};
const fileDrag = () => ({
  dataTransfer:{types:["Files"], files:[file], dropEffect:"none"},
  prevented:false,
  preventDefault(){this.prevented=true;}
});
const first = fileDrag();
target.dispatch("dragenter", first);
assert(first.prevented);
const nested = fileDrag();
target.dispatch("dragenter", nested);
target.dispatch("dragleave", fileDrag());
assert.strictEqual(active[active.length-1], true);
target.dispatch("dragleave", fileDrag());
assert.strictEqual(active[active.length-1], false);

const over = fileDrag();
target.dispatch("dragover", over);
assert(over.prevented);
assert.strictEqual(over.dataTransfer.dropEffect, "copy");

target.dispatch("dragenter", fileDrag());
const dropped = fileDrag();
target.dispatch("drop", dropped);
assert(dropped.prevented);
assert.strictEqual(received.length, 1);
assert.strictEqual(received[0][0], file);
assert.strictEqual(active[active.length-1], false);

const textDrag = {
  dataTransfer:{types:["text/plain"], files:[]},
  prevented:false,
  preventDefault(){this.prevented=true;}
};
target.dispatch("drop", textDrag);
assert.strictEqual(textDrag.prevented, false);
assert.strictEqual(received.length, 1);

const outsideDrop = fileDrag();
document.dispatch("drop", outsideDrop);
assert(outsideDrop.prevented);

destroy();
destroy();
const afterDestroy = fileDrag();
target.dispatch("drop", afterDestroy);
assert.strictEqual(received.length, 1);
assert.strictEqual((target.listeners.get("drop") || []).length, 0);
assert.strictEqual((document.listeners.get("drop") || []).length, 0);

const asyncTarget = new Target();
const asyncDocument = new Target();
let unhandled = false;
const onUnhandled = () => { unhandled = true; };
process.on("unhandledRejection", onUnhandled);
const destroyAsync = api.install(asyncTarget, asyncDocument, {
  onFiles: () => Promise.reject(new Error("simulated handler failure")),
});
asyncTarget.dispatch("drop", fileDrag());
await new Promise(resolve => setTimeout(resolve, 0));
process.removeListener("unhandledRejection", onUnhandled);
assert.strictEqual(unhandled, false);
destroyAsync();
console.log("image-drop-harness-passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", harness, str(MODULE)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "image-drop-harness-passed" in result.stdout
