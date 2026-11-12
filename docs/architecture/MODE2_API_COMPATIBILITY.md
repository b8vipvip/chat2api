# Mode 2 API compatibility contract (M-01)

**Baseline:** `main` commit `b682e1d445008df57e4de9675e2952ae040b8027` (2026-11-12).

The authoritative machine-readable inventory is
[`mode2-api-compatibility-matrix.json`](mode2-api-compatibility-matrix.json).
The matrix is **not** a promise of universal OpenAI API parity; each route
is explicitly marked `compatible_subset` or `extension_only`.

## Supported entrypoints

| Route | Mode 2 mapping | Known limitations |
| --- | --- | --- |
| `GET /v1/models` | SDK model discovery | Only online/currently offered models; not full OpenAI model inventory |
| `POST /v1/chat/completions` | Python/JS Chat Completions | Text and supported image/file references; multi-turn roles are serialized as an ordered prompt; no guaranteed native Chat tool calls or schema-constrained output |
| `POST /v1/responses` | Python/JS Responses | Supported text/native web search/hosted-image and selected emulated tools; no general Responses parity |
| `GET /v1/responses/{response_id}` | Responses retrieve | In-process bounded response store, not durable long-term remote Responses storage |
| `POST /v1/images/generations` | Image generation | `gpt-image` only; requires image-capable Worker |
| `POST /v1/files` | **Chat2API extension** | JSON `{filename, data_base64, mime_type, purpose}`; standard SDK `files.create` multipart upload is **not** supported |
| `GET /v1/files/{file_id}` | Metadata lookup | API-key-scoped; no standard content/download route |
| `DELETE /v1/files/{file_id}` | File delete | API-key-scoped; response shape is a subset |

## Behavioral contract

No client header is needed to select Mode 2. `X-Chat2API-Mode: api`
and `X-Chat2API-Mode: mode2` select Mode 2 explicitly. `chat`
and `mode1` are invalid on `/v1`, and the API never falls through to
a Mode 1 proxy. Request credentials are validated by the existing API-key
authentication layer. Unknown optional SDK fields are **not** evidence that
their semantics are supported: for example, `tools`, `tool_choice`,
`temperature` and `response_format` are not guaranteed by the Chat
Completions Worker bridge. Client consumers should check the capability matrix.

Chat Completions streaming sends `chat.completion.chunk` data frames, a final
`finish_reason="stop"` chunk with estimated usage, and `[DONE]`. It can
emit optional `chat2api_status` or `reasoning` deltas while waiting for a
Worker; these are not assistant `content`. Responses streaming instead emits
named `response.*` events and a `response.completed` or `response.failed`
terminal event; no Chat Completions `[DONE]` guarantee applies.

**Usage is estimated.** `model` in the OpenAI-compatible envelope is the
requested ID, not independent proof of the actually served upstream model.
The R track retains final model provenance, SSE delivery and native-fidelity
validation.

### Standard SDK smoke tests

A dedicated GitHub Actions job installs current bounded **Python OpenAI 2.x**
and **Node OpenAI 6.x** SDKs for serialization/parser regression. These are
test-only dependencies, not production runtime requirements.
Python's HTTP transport forwards requests into a real Chat2API ASGI
`TestClient` with a deterministic fake browser transport; Node's SDK runs
against a bounded synthetic HTTP fixture for independent client encoding and
SSE parsing checks. Neither fake fixture proves a real ChatGPT Worker outcome.
Production-specific route ownership is separately checked against the composed
`app.entry:app` under isolated subprocess state.

The suite asserts both positive interoperability and explicit non-parity
(standard multipart `files.create` does not work). When upstream SDK versions
change, update the SDK lane with a pinned compatible range and review any
serialization differences before broadening claims.

## Ownership and release

M-01 owns this matrix, the SDK fixture tests and their CI job. M-02 owns the
shared `ChatRequestService` boundary and route-adapter integration, in a
separate branch/PR so the tracks can be reviewed independently. G0 controls
actual data migrations; R controls terminal/finality; W owns user chat storage
and UI. M-01 does not modify production routes or enable Mode 1.
