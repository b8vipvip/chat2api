# Web Chat production entry integration

Status: **Draft / partial product integration**.

This integration connects the existing /chat client to the W-01 account-scoped
image attachment flow, W-03 safe Markdown renderer, W-04 owner-scoped request
recovery/cancellation APIs and W-05A owner-scoped document download API. It
does not install the W-02 plaintext SQLite conversation prototype.

## Installed behavior

- The existing chat HTML loads the renderer, recovery controller and image-drop
  adapter before the chat client. Assets are same-origin and no-store.
- Assistant Markdown uses the DOM-only allowlist renderer; fallback is
  textContent, never untrusted HTML.
- W-01 accepts PNG/JPEG/WebP/GIF image attachments, validates actual raster bytes on Chat Completions/Responses image references and browser previews, uses the standard Files
  endpoint with purpose=vision, enforces per-image/message size limits, binds
  file IDs to the current account/API key, and provides owner-checked preview
  and same-origin deletion endpoints.
- Each Web Chat request supplies a client-generated req_ identifier using
  X-Chat2API-Request-ID. Pending request_id and assistant message identity are
  stored in account-scoped local browser history. On reload the client polls
  the owner-checked recovery endpoint and never re-POSTs generation.
- Stop sends an owner-checked cancel request to the assigned Worker before
  stopping local stream reading and reconciling the original request.
- W-05A document downloads use a same-origin owner-checked endpoint.

## Deliberately limited

- FileStore image bytes are **not encrypted at rest** by this change. No
  production retention/deletion lifecycle or orphan sweeper is established.
  Do not describe image storage as encrypted or production-hardened.
- Real Chrome + live Worker multimodal understanding and image-byte end-to-end
  verification remain outstanding; API-level tests do not prove provider
  vision behavior or native 1:1 content integrity.
- W-02 server conversation storage is not installed because its current
  prototype is plaintext SQLite and DataKeyring/production storage remains
  deferred by explicit user decision. Existing browser history remains.
- W-04 is final-snapshot polling, not durable token-level SSE event replay.
  It can restore final Telemetry text when available, but cannot replay missing
  deltas or replace Broker/request-v6 terminal ownership.
- Structured renderer image blocks are not enabled; W-01 image previews use
  their separate owner-checked preview endpoint.

## Automated integration coverage

The app.entry subprocess test verifies route uniqueness, private/no-store auth
failures, static asset serving, script order, image preview/delete routes and
real document upload/download/delete through the production composition. A
Playwright Chromium E2E exercises the document picker, actual FileStore upload,
message payload, file card and authenticated download. Worker handoff is also
covered by a separate API/WebSocket test. CI checks JavaScript syntax for the
renderer, recovery controller, image-drop adapter and chat client.

## Remaining release gates

Real desktop/mobile browser E2E, live Worker cancellation, refresh recovery
under network interruption, real multimodal request evidence, image SHA/bytes
verification, retention/orphan cleanup, cross-account isolation in production
FileStore, W-02 encrypted server-side history, durable event replay, production
image smoke and deployment/rollback evidence remain required. This PR stays
Draft until these gates pass.

## W-05B generic document attachments

- Web Chat accepts PDF, TXT, Markdown, CSV, JSON, RTF, DOCX, XLSX and PPTX through a separate file picker.
- Files use the existing `/v1/files` JSON/base64 upload contract with purpose `assistants` and the selected API Key's `files` scope.
- User messages persist a structured `type: file` / `file_id` content part; the existing Chat Completions attachment resolver verifies API-Key ownership and passes canonical file metadata to the Worker.
- The W-05A session-authenticated download route renders user file cards; the same-origin delete route accepts only owned `vision` image files or `assistants` document MIME types.
- Up to four combined image/document attachments and 20 MiB total per message. Browser history stores file IDs and metadata only; it does not store file bytes.
- No server-side conversation store, encrypted FileStore, server retention policy, document OCR, or native document preview is claimed by this slice.
- A Playwright Chromium E2E covers browser file selection, FileStore upload, structured send payload, user-message file card and authenticated exact-byte download. It mocks only the Chat Completions SSE response; Worker handoff is covered separately by the API/WebSocket test.

- `app.entry:app` installs the owner-scoped download route through the single Web Chat composition owner `install_web_chat_features_v1`, after the user-account routes exist. Production-entry tests register an account, create a `files`-scoped key, upload a document, download exact bytes through the HttpOnly session, and delete the owner-scoped file.

## Inherited C-03B resource-ownership and Worker download boundary

The production integration also includes the C-03B resource-authorization guards.
Tenant membership is not an implicit grant to another user's personal API keys,
request history, billing, files, archived media or native-content sidecars.
Shared-workspace access requires a separately reviewed resource-sharing contract.

A paired Worker attachment read is scoped to the exact live request, assigned
Worker, API-key owner and file IDs explicitly attached to that request. A Worker
token or a stale Telemetry row with `status=running` is not a global FileStore
read capability: the server also requires the matching nonterminal Broker lease.
Responses dispatch failure terminalizes that lease and the Telemetry projection.
Administrator request-media retrieval verifies that the archived file owner
matches the API key recorded on the request.

The C-03B tenant/resource tests and stale-grant regressions must remain in the
full production-entry CI run. Do not rebase this integration to the pre-C-03B
base and silently drop those protections.

