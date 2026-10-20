import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync("app/admin_playground_chat_v69.js", "utf8");
const start = source.indexOf("  const escHtml = ");
const end = source.indexOf("  const style = document.createElement", start);
assert.ok(start > 0 && end > start);
const context = { URL, location: { href: "https://chat2api.mv3.cn/admin" } };
runInNewContext(source.slice(start, end) +
  "\nglobalThis.testHelpers={imageResultMarkdown,normalizeAssistantContent,safeImageUrl,renderMarkdown,isActualImageReply};", context);
const media = context.testHelpers;

const result = media.imageResultMarkdown({ data: [{ b64_json: "YWJj" }] });
assert.equal(result, "![生成图片 1](data:image/png;base64,YWJj)");
assert.match(media.renderMarkdown(result), /<img[^>]+src="data:image\/png;base64,YWJj"/);
assert.equal(media.isActualImageReply(result), true);
assert.equal(media.normalizeAssistantContent([{ type: "text", text: "说明" },
  { type: "image_url", image_url: { url: "https://media.oaiusercontent.com/test.png" } }]),
  "说明\n\n![生成图片](https://media.oaiusercontent.com/test.png)");
assert.equal(media.imageResultMarkdown({ data: [{ b64_json: "not$$base64" }] }), "");
assert.equal(media.imageResultMarkdown({ data: [{ url: "/mnt/data/file.png" }] }), "");
assert.equal(media.imageResultMarkdown({ data: [{ url: "https://evil.example/1.png" }] }), "");
assert.equal(media.safeImageUrl("data:image/svg+xml;base64,YWJj"), "");
assert.equal(media.safeImageUrl("javascript:alert(1)"), "");
assert.equal(media.safeImageUrl("https://media.oaiusercontent.com.evil.example/x"), "");
assert.equal(media.safeImageUrl("https://media.oaiusercontent.com/x.png"), "https://media.oaiusercontent.com/x.png");
assert.equal(media.isActualImageReply("bash -lc python - <<'PY'\nsrc='/mnt/data/photo.png'"), false);
assert.doesNotMatch(media.renderMarkdown("![x](javascript:alert(1))"), /<img/);
assert.match(source, /\/v1\/images\/generations/);
assert.match(source, /"X-Chat2API-Request-ID": imageMode/);
assert.match(source, /response_format:"b64_json"/);
assert.match(source, /if \(imageMode && !isActualImageReply\(assistantText\)\)/);
console.log("Playground real image result rendering and image intent differentiation: passed");
