import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync("chrome_extension/background_media_fetch_v175.js", "utf8");
let listener;
let calls = 0;
let nextType = "image/png";
let sizeOverride = null;

runInNewContext(source, {
  URL,
  AbortController,
  Uint8Array,
  setTimeout,
  clearTimeout,
  btoa,
  chrome: {
    runtime: {
      onMessage: { addListener: (callback) => { listener = callback; } },
    },
  },
  fetch: async (url, options) => {
    calls += 1;
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    assert.match(url, /^https:\/\//);
    const blob = new Blob([Uint8Array.from([137, 80, 78, 71])], { type: nextType });
    return {
      ok: true,
      headers: { get: (key) =>
        key === "content-type" ? nextType
          : key === "content-length" ? String(sizeOverride ?? blob.size) : null },
      blob: async () => blob,
    };
  },
});
assert.equal(typeof listener, "function");

function send(message, sender = { url: "https://chatgpt.com/c/abc", tab: { id: 2 } }) {
  return new Promise((resolve) => {
    const keepAlive = listener(message, sender, resolve);
    if (!keepAlive) return;
  });
}
assert.deepEqual(
  await send({ type: "chat2api.media.fetch-v175", url: "http://127.0.0.1/secrets" }),
  { ok: false, error: "untrusted-image-source" },
);
assert.deepEqual(
  await send({ type: "chat2api.media.fetch-v175", url: "https://example.com/img.png" }),
  { ok: false, error: "untrusted-image-source" },
);
assert.deepEqual(
  await send({ type: "chat2api.media.fetch-v175", url: "https://cdn.openai.com/img.png" },
    { url: "https://other.example/", tab: { id: 2 } }),
  { ok: false, error: "untrusted-image-source" },
);
assert.equal(calls, 0, "untrusted requests must never invoke fetch");

const result = await send({
  type: "chat2api.media.fetch-v175",
  url: "https://media.oaiusercontent.com/example.png",
});
assert.equal(result.ok, true);
assert.equal(result.bytes, 4);
assert.match(result.data_url, /^data:image\/png;base64,/);
assert.equal(calls, 1);

nextType = "image/svg+xml";
assert.equal((await send({
  type: "chat2api.media.fetch-v175", url: "https://cdn.oaistatic.com/icon.svg",
})).ok, false);

nextType = "image/png";
sizeOverride = 5 * 1024 * 1024;
assert.equal((await send({
  type: "chat2api.media.fetch-v175", url: "https://cdn.openai.com/huge.png",
})).ok, false);
console.log("Bounded, origin-restricted image relay checks passed");
