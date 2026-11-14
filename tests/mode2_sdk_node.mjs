/** Official Node OpenAI SDK against the real Chat2API ASGI app.
 *
 * tests/test_mode2_openai_node_sdk.py starts the composed API app on loopback,
 * injects a deterministic Worker transport, and runs this SDK process against
 * it. This is not a synthetic protocol fixture or a real browser-worker test.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";

const modules = process.env.MODE2_SDK_NODE_MODULES;
const baseURL = process.env.MODE2_BASE_URL;
const apiKey = process.env.MODE2_API_KEY;
assert.ok(modules, "MODE2_SDK_NODE_MODULES must point to the isolated npm dependencies");
assert.ok(baseURL, "MODE2_BASE_URL must point to the real Chat2API ASGI server");
assert.ok(apiKey, "MODE2_API_KEY is required");
const require = createRequire(join(modules, "openai"));
const sdkPackage = require(join(modules, "openai"));
const OpenAI = sdkPackage.default || sdkPackage.OpenAI || sdkPackage;
const client = new OpenAI({ apiKey, baseURL, maxRetries: 0 });

const models = await client.models.list();
assert.ok(Array.isArray(models.data));
const model = await client.models.retrieve("gpt-5.6-sol");
assert.equal(model.id, "gpt-5.6-sol");

const messages = [
  { role: "system", content: "Use Chinese." },
  { role: "user", content: "First turn" },
  { role: "assistant", content: "Previous reply" },
  { role: "user", content: "Second turn" },
];

const legacy = await client.completions.create({ model: "gpt-5.6-sol", prompt: "Legacy completion" });
assert.equal(legacy.object, "text_completion");
assert.equal(legacy.choices[0].text, "mode2-node-sdk-answer");

const completion = await client.chat.completions.create({ model: "gpt-5.6-sol", messages });
assert.equal(completion.object, "chat.completion");
assert.equal(completion.choices[0].message.content, "mode2-node-sdk-answer");
assert.equal(completion.usage.total_tokens > 0, true);

const streaming = await client.chat.completions.create({ model: "gpt-5.6-sol", messages, stream: true });
let streamedText = "";
let finished = false;
for await (const chunk of streaming) {
  for (const choice of chunk.choices || []) {
    streamedText += choice.delta?.content || "";
    if (choice.finish_reason === "stop") finished = true;
  }
}
assert.equal(streamedText, "mode2-node-sdk-answer");
assert.equal(finished, true);

const response = await client.responses.create({ model: "gpt-5.6-sol", input: "Hello Responses" });
assert.equal(response.status, "completed");
assert.equal(response.output_text, "mode2-node-sdk-answer");
const retrievedResponse = await client.responses.retrieve(response.id);
assert.equal(retrievedResponse.id, response.id);
assert.equal(retrievedResponse.output_text, response.output_text);

const image = await client.images.generate({
  model: "gpt-image",
  prompt: "Generate a small test image",
  response_format: "b64_json",
});
assert.equal(image.model, "gpt-image");
assert.equal(image.data[0].b64_json, process.env.MODE2_EXPECTED_IMAGE_B64);

const responseStream = await client.responses.create({
  model: "gpt-5.6-sol",
  input: "Stream Responses events",
  stream: true,
});
let responseStreamText = "";
let responseCompleted = false;
for await (const event of responseStream) {
  if (event.type === "response.output_text.delta") responseStreamText += event.delta;
  if (event.type === "response.completed") {
    responseCompleted = true;
    assert.equal(event.response.output_text, "mode2-node-sdk-answer");
  }
}
assert.equal(responseStreamText, "mode2-node-sdk-answer");
assert.equal(responseCompleted, true);

const aliased = await client.chat.completions.create({
  model: "gpt-5-6-thinking",
  messages: [{ role: "user", content: "Check model alias" }],
});
assert.equal(aliased.model, "gpt-5-6-thinking");

const uploaded = await client.files.create({
  file: new File(["one two three"], "测试 sample.txt", { type: "text/plain" }),
  purpose: "assistants",
});
assert.equal(uploaded.object, "file");
assert.equal(uploaded.filename, "测试_sample.txt");
assert.equal(uploaded.purpose, "assistants");
assert.equal(uploaded.status, "processed");
assert.equal(uploaded.bytes, 13);
const retrieved = await client.files.retrieve(uploaded.id);
assert.equal(retrieved.id, uploaded.id);
const binary = await client.files.content(uploaded.id);
const content = Buffer.from(await binary.arrayBuffer());
assert.equal(content.toString("utf8"), "one two three");
const deleted = await client.files.delete(uploaded.id);
assert.equal(deleted.id, uploaded.id);
assert.equal(deleted.deleted, true);

let authRejected = false;
try {
  const invalidClient = new OpenAI({
    apiKey: "invalid-mode2-node-key",
    baseURL,
    maxRetries: 0,
  });
  await invalidClient.models.list();
} catch (error) {
  authRejected = error.status === 401 && error.error?.type === "authentication_error";
}
assert.equal(authRejected, true, "real API must return a standard authentication error envelope");

let validationRejected = false;
try {
  await client.chat.completions.create({ model: "gpt-5.6-sol", messages: [] });
} catch (error) {
  validationRejected = error.status === 422 && error.error?.type === "invalid_request_error";
}
assert.equal(validationRejected, true, "real API must return a standard validation error envelope");

process.stdout.write("Official Node OpenAI SDK passed against real Chat2API ASGI routes (fake Worker transport)\n");
