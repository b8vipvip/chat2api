# Mode 2 API compatibility contract (M-01)

**Baseline:** `main` commit `b682e1d445008df57e4de9675e2952ae040b8027` (2026-11-12).

The authoritative machine-readable inventory is
[`mode2-api-compatibility-matrix.json`](mode2-api-compatibility-matrix.json).
The matrix is **not** a promise of universal OpenAI API parity; each route
is explicitly marked `compatible_subset` or `extension_only`.

## Supported entrypoints

The machine-readable matrix enumerates **all 20 production `/v1/*` routes**
(19 HTTP operations plus one WebSocket), not just the primary chat routes.
A runtime test compares the matrix against every registered production route
and the generated OpenAPI document, failing on either an omitted route or a
stale/duplicate owner.

| Route | Classification | SDK / contract |
| --- | --- | --- |
| `GET /v1/models` | Compatible subset | `client.models.list()`; currently online model catalog |
| `GET /v1/models/{model_id}` | Compatible subset | `client.models.retrieve()`; online model only |
| `POST /v1/chat/completions` | Compatible subset | `client.chat.completions.create()`; transcript bridge |
| `POST /v1/completions` | Compatible subset | `client.completions.create()`; legacy prompt-to-chat bridge |
| `POST /v1/responses` | Compatible subset | `client.responses.create()`; selected Responses inputs/tools |
| `GET /v1/responses/{response_id}` | Compatible subset | `client.responses.retrieve()`; bounded in-process store |
| `POST /v1/images/generations` | Compatible subset | `client.images.generate()`; gpt-image and image-capable Worker |
| `POST /v1/files` | Compatible subset | Standard multipart `files.create` for `assistants`, `vision`, `user_data`; legacy JSON/base64 extension retained |
| `GET /v1/files/{file_id}` | Compatible subset | `client.files.retrieve()`; standard FileObject metadata |
| `GET /v1/files/{file_id}/content` | Compatible subset | `client.files.content()`; owner-scoped binary download |
| `DELETE /v1/files/{file_id}` | Compatible subset | `client.files.delete()`; standard FileDeleted envelope |
| `POST /v1/audio/speech` | Chat2API extension | JSON/base64 GPT-Live speech result, not standard binary audio |
| `POST /v1/audio/conversations` | Chat2API extension | Owner-scoped uploaded audio reference and JSON result |
| `POST /v1/audio/transcriptions` | Chat2API extension | Uses `audio_file_id`, not standard multipart transcription |
| `POST /v1/audio/realtime/sessions` | Chat2API extension | One-time session token; not OpenAI client-secrets route |
| `WEBSOCKET /v1/audio/realtime` | Chat2API extension | Custom `chat2api-live-v1` PCM wire protocol |
| `GET /v1/media/{media_id}` | Chat2API extension | Owner-scoped metadata and signed media URL |
| `GET /v1/generated-files/{file_id}` | Chat2API extension | Owner-scoped generated-file metadata and signed URL |
| `GET /v1/native-content/{request_id}` | Chat2API extension | Owner-scoped native page snapshot |
| `GET /v1/native-content/{request_id}/events` | Chat2API extension | Presentation-only native snapshot SSE |

Standard multipart upload does not imply support for batch, fine-tune or evals
file purposes. The standard `GET /v1/files` list endpoint is not implemented.
The audio and realtime endpoints are explicitly classified as extensions rather
than being presented as standard OpenAI audio/realtime compatibility.

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
Both official SDKs now call a live Chat2API ASGI server. Python uses the
in-process ASGI transport; Node runs as a separate process against a loopback
Uvicorn server. Both replace only the external Chrome/Worker transport with a
deterministic fake that publishes through the real Broker. Neither fixture
proves a real ChatGPT Worker outcome.
Production-specific route ownership is separately checked against the composed
`app.entry:app` under isolated subprocess state.

The suite asserts positive interoperability for Chat Completions, legacy Completions, Responses, models and standard Files create/retrieve/content/delete. It also checks standard error envelopes for authentication and validation failures. When upstream SDK versions
change, update the SDK lane with a pinned compatible range and review any
serialization differences before broadening claims.

## Ownership and release

M-01 owns this matrix, the SDK fixture tests and their CI job. M-02 owns the
shared `ChatRequestService` boundary and route-adapter integration, in a
separate branch/PR so the tracks can be reviewed independently. G0 controls
actual data migrations; R controls terminal/finality; W owns user chat storage
and UI. M-01 does not modify production routes or enable Mode 1.
