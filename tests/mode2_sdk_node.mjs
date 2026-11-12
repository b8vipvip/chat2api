/** Real official Node OpenAI SDK transport/stream smoke (synthetic loopback fixture).
 *
 * This is intentionally NOT an upstream-worker integration test: Python
 * test_mode2_openai_python_sdk.py checks the real Chat2API ASGI handlers.
 */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const modules = process.env.MODE2_SDK_NODE_MODULES;
assert.ok(modules, "MODE2_SDK_NODE_MODULES must point to the isolated npm dependencies");
const sdkPackage = require(join(modules, "openai"));
const OpenAI = sdkPackage.default || sdkPackage.OpenAI || sdkPackage;

const seen = [];
const server = createServer(async (req, res) => {
  const parts = [];
  for await (const part of req) parts.push(part);
  const body = Buffer.concat(parts);
  seen.push({ method: req.method, url: req.url, authorization: req.headers.authorization, contentType: req.headers["content-type"], body });
  const json = (status, data) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(data));
  };
  if (req.headers.authorization !== "Bearer mode2-node-sdk-fixture") {
    return json(401, { error: { message: "Missing API key", type: "authentication_error" } });
  }
  if (req.url === "/v1/models" && req.method === "GET") {
    return json(200, { object: "list", data: [{ id: "gpt-5.6-sol", object: "model", created: 1, owned_by: "chat2api" }] });
  }
  if (req.url === "/v1/chat/completions" && req.method === "POST") {
    const request = JSON.parse(body.toString("utf8"));
    if (request.stream === true) {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      const base = { id: "chatcmpl_mode2_sdk", object: "chat.completion.chunk", created: 1, model: request.model };
      res.write("data: " + JSON.stringify({ ...base, choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] }) + "\n\n");
      res.write("data: " + JSON.stringify({ ...base, choices: [{ index: 0, delta: { content: "hello SDK" }, finish_reason: null }] }) + "\n\n");
      res.write("data: " + JSON.stringify({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 } }) + "\n\n");
      return res.end("data: [DONE]\n\n");
    }
    return json(200, {
      id: "chatcmpl_mode2_sdk", object: "chat.completion", created: 1, model: request.model,
      choices: [{ index: 0, message: { role: "assistant", content: "hello SDK" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
    });
  }
  if (req.url === "/v1/responses" && req.method === "POST") {
    const request = JSON.parse(body.toString("utf8"));
    return json(200, {
      id: "resp_mode2_sdk", object: "response", created_at: 1, status: "completed", model: request.model,
      output: [{ id: "msg_sdk", type: "message", status: "completed", role: "assistant", content: [{ type: "output_text", text: "hello SDK", annotations: [] }] }],
      output_text: "hello SDK",
    });
  }
  if (req.url === "/v1/files" && req.method === "POST") {
    return json(415, { error: { message: "multipart upload is not supported", type: "invalid_request_error" } });
  }
  return json(404, { error: { message: "Unknown route", type: "invalid_request_error" } });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
try {
  const address = server.address();
  const client = new OpenAI({
    apiKey: "mode2-node-sdk-fixture",
    baseURL: `http://127.0.0.1:${address.port}/v1`,
    maxRetries: 0,
  });

  const models = await client.models.list();
  assert.equal(models.data[0].id, "gpt-5.6-sol");

  const messages = [
    { role: "system", content: "Use Chinese." },
    { role: "user", content: "First turn" },
    { role: "assistant", content: "Previous reply" },
    { role: "user", content: "Second turn" },
  ];
  const completion = await client.chat.completions.create({ model: "gpt-5.6-sol", messages });
  assert.equal(completion.choices[0].message.content, "hello SDK");
  assert.equal(completion.usage.total_tokens, 6);

  const streaming = await client.chat.completions.create({ model: "gpt-5.6-sol", messages, stream: true });
  let text = "";
  let finished = false;
  for await (const chunk of streaming) {
    for (const choice of chunk.choices || []) {
      text += choice.delta?.content || "";
      if (choice.finish_reason === "stop") finished = true;
    }
  }
  assert.equal(text, "hello SDK");
  assert.equal(finished, true);

  const response = await client.responses.create({ model: "gpt-5.6-sol", input: "Hello response endpoint" });
  assert.equal(response.status, "completed");
  assert.equal(response.output_text, "hello SDK");

  let rejectedFileUpload = false;
  try {
    await client.files.create({
      file: new File(["sample"], "sample.txt", { type: "text/plain" }),
      purpose: "assistants",
    });
  } catch (error) {
    rejectedFileUpload = error.status === 415;
  }
  assert.equal(rejectedFileUpload, true, "SDK multipart upload must be documented as unsupported");

  const chatRequests = seen.filter((item) => item.url === "/v1/chat/completions");
  assert.equal(chatRequests.length, 2);
  for (const request of chatRequests) {
    assert.equal(request.authorization, "Bearer mode2-node-sdk-fixture");
    const payload = JSON.parse(request.body.toString("utf8"));
    assert.equal(payload.model, "gpt-5.6-sol");
    assert.deepEqual(payload.messages, messages);
    assert.equal(payload.prompt_mode, undefined, "ordinary SDK should not require private prompt_mode");
  }
  const upload = seen.find((item) => item.url === "/v1/files");
  assert.ok(upload.contentType.startsWith("multipart/form-data"));
  process.stdout.write("Mode 2 Node SDK serialization, JSON, SSE and unsupported multipart checks passed\n");
} finally {
  await new Promise((resolve) => server.close(resolve));
}
