import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const assertFromVm = (actual, expected) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);

const source = readFileSync("chrome_extension/background_media_fetch_v175.js", "utf8");
let listener;
let fileListener;
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
      onMessage: { addListener: (callback) => { if (!fileListener) fileListener = callback; else listener = callback; } },
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
      body: { getReader: () => {
        let used = false;
        return {
          read: async () => {
            if (used) return {done: true};
            used = true;
            return {done: false, value: Uint8Array.from([37, 80, 68, 70])};
          },
          cancel: async () => {},
        };
      } },
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
assertFromVm(
  await send({ type: "chat2api.media.fetch-v175", url: "http://127.0.0.1/secrets" }),
  { ok: false, error: "untrusted-image-source" },
);
assertFromVm(
  await send({ type: "chat2api.media.fetch-v175", url: "https://example.com/img.png" }),
  { ok: false, error: "untrusted-image-source" },
);
assertFromVm(
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


assert.equal(typeof fileListener, "function");
function sendFile(message, sender = {url: "https://chatgpt.com/c/abc", tab: {id: 2}}) {
  return new Promise(resolve => {
    fileListener(message, sender, resolve);
  });
}
const callsBeforeFile = calls;
assertFromVm(await sendFile({
  type: "chat2api.file.fetch-v188", url: "http://localhost/private", filename: "report.pdf",
}), {ok: false, error: "untrusted-file-source"});
assertFromVm(await sendFile({
  type: "chat2api.file.fetch-v188", url: "https://cdn.openai.com/sample.exe", filename: "sample.exe",
}), {ok: false, error: "untrusted-file-source"});
assertFromVm(await sendFile({
  type: "chat2api.file.fetch-v188", url: "https://attacker.example/sample.pdf", filename: "sample.pdf",
}), {ok: false, error: "untrusted-file-source"});
assert.equal(calls, callsBeforeFile, "untrusted file requests must never make network requests");

nextType = "application/octet-stream";
const fileResult = await sendFile({
  type: "chat2api.file.fetch-v188",
  url: "https://media.oaiusercontent.com/generated/report.pdf",
  filename: "报告.pdf",
});
assert.equal(fileResult.ok, true);
assert.equal(fileResult.bytes, 4);
assert.match(fileResult.data_url, /^data:application\/pdf;base64,/);

nextType = "text/html";
assert.equal((await sendFile({
  type: "chat2api.file.fetch-v188",
  url: "https://media.oaiusercontent.com/generated/error.pdf",
  filename: "error.pdf",
})).ok, false, "HTML login/error pages must never be archived as PDF files");

nextType = "application/octet-stream";
sizeOverride = 5 * 1024 * 1024;
const tooLarge = await sendFile({
  type: "chat2api.file.fetch-v188",
  url: "https://media.oaiusercontent.com/generated/large.zip",
  filename: "large.zip",
});
assert.equal(tooLarge.ok, false);
console.log("Bounded, origin-restricted generated file relay checks passed");
