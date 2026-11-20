# Chat2API Web Chat Client v1

Status: first interactive browser client; not a complete ChatGPT 1:1 replacement.

## Entry points

- `GET /chat` serves the standalone chat UI.
- `/console` links to the chat client.
- `/assets/chat2api-image-drop-v1.js`, `/assets/chat2api-chat-client-v1.js` and `/assets/chat2api-chat-client-v1.css` are served with no-store headers.
- The HTML response applies a restrictive same-origin Content Security Policy and `X-Content-Type-Options: nosniff`.

## Authentication and API flow

1. The page checks the existing user session through `GET /api/user/me`.
2. It lists the signed-in user's own API Keys through `GET /api/user/keys`.
3. The user selects an enabled, non-expired Key with recoverable secret and `chat` scope.
4. The page fetches that Key's secret through the existing owner-checked `GET /api/user/keys/{key_id}/secret` endpoint.
5. The raw Key is held only in page memory and used as a Bearer token for the dedicated `POST /api/user/chat/completions` Mode 1 Web Chat ingress. The page revalidates the same user session before each chat request. The Key ID preference may be stored locally; the raw Key is never written to localStorage.
6. The Mode 1 ingress requires both the signed-in user session and the selected managed API Key, rejects the administrator master key, and verifies that the user owns the Key.
7. The Mode 1 adapter invokes the same internal Chat Completions request handler used by Mode 2. That shared handler retains the existing model policy, quota/wallet admission, Worker selection, RequestBroker, ChatRequestService and terminal/billing owners; Mode 1 does not call the OpenAI-compatible `/v1/chat/completions` route.
8. On bootstrap, the client reads owner-scoped server conversations through GET /api/user/chat/conversations, merges them with the account's legacy local cache, and uploads local-only conversations with revision 0. Conversation GET/PUT/DELETE requests are authorized only by the HttpOnly user session; a caller-supplied user ID is never accepted.
9. Conversation PUT uses an expected server revision. Concurrent tabs/devices receive 409 with the latest row, merge message IDs and retry; DELETE writes an owner-scoped tombstone so a stale device cannot silently resurrect a deleted conversation.
10. Stop requests use the same user-session cancel endpoint. If the request is still queued, the server signals the pending admission task and waits for its terminal projection; if already running, it serializes the cancel message behind the initial Worker dispatch. The browser aborts the generation POST only after this cancellation handshake returns.
11. In parallel, the browser opens a user-session native-content sidecar for the same request ID. The sidecar checks that the signed-in user owns the API Key recorded in Telemetry, then reads the same validated NativeContentStore snapshot and archived media records. It never dispatches a second generation.

The page sends the full ordered conversation using the Chat2API Web Chat request schema (`model`, `messages`, `stream`). The server's default full-history behavior preserves multi-turn context; no private `prompt_mode` field is required.

## Implemented in v1

- Responsive dark/light chat layout with desktop sidebar and mobile sidebar.
- User-session check and owner-scoped API Key selection.
- Online model list with user model-catalog fallback.
- New conversation, conversation list, local search, rename and delete. Server persistence uses a private SQLite database with per-user ownership, optimistic revisions, conflict merging and deletion tombstones; browser localStorage remains a local cache and the one-time source for migration of older local conversations.
- Image attachments: PNG, JPEG, WebP and GIF; up to 4 combined attachments per message, 10 MiB per image and 20 MiB total per message. Document attachments support PDF, TXT, Markdown, CSV, JSON, RTF, DOCX, XLSX and PPTX; each document is limited to 20 MiB and uses the existing FileStore with purpose `assistants`. Images can be selected, pasted or dragged onto the composer; documents are selected through the dedicated file picker. Image drag/drop uses an isolated event adapter and funnels accepted File objects through the same MIME/size/key-owner validation as the picker. Images are uploaded through Chat2API's JSON `/v1/files` endpoint under the selected Key's `files` scope. The message uses the standard Chat Completions `image_url` content-part envelope, with a Chat2API-specific `file_...` reference in `image_url.url` to avoid storing/re-sending base64 bytes in browser history.
- Streaming Chat Completions, incremental text, stop generation and incomplete-stream warning when `[DONE]` is missing.
- W-04 request recovery: each turn carries a request ID, pending request/message identity is stored in account-scoped local history, and refresh/network interruption polls the owner-checked final Telemetry snapshot without re-posting generation. This restores a final response only when the server has one; it does not replay missing token deltas.
- Safe Markdown fallback plus a Mode 1 native-content renderer for validated HTML layout, SVG charts, MathML, archived images, generated files and clickable citation links restricted to public HTTPS destinations. Citation links open in a new tab with no referrer and no opener. The renderer builds DOM nodes from allowlisted tags, styles, SVG attributes and link targets; it never inserts captured HTML or executes scripts. The user-session sidecar streams revisions and finality for the same request ID.
- Copy message/code and export current conversation to Markdown.
- Conversation history is persisted server-side per authenticated user and synchronized across devices. The localStorage copy is an offline/cache fallback and is namespaced by user ID; API Key material is memory-only.

## Explicit limitations / not yet implemented

- The Web Chat client currently exposes the newest 15 conversations, with at most 30 messages per conversation and 8,000 text characters per message. The server keeps a bounded response window of the newest 100 conversation rows (including deletion tombstones); there is not yet server-side conversation search, retention policy, export/import API or administrator retention controls. Image bytes remain in the existing server FileStore; conversation history stores only validated file IDs and metadata.
- SQLite conversation rows are stored in a private 0600 database and scoped by authenticated user ID. Writes use optimistic revisions; simultaneous device updates are merged by stable message ID and retried, while deletions are represented by payload-free tombstones. This is persistence/synchronization, not end-to-end encryption or encrypted archival storage.
- The existing FileStore persists file bytes on disk without application-level encryption at rest. Deleting a conversation does not automatically delete its image/document files: another device or conversation may still reference them, and a browser-local reference scan is not sufficient to prove an attachment is orphaned. Server-side attachment reference accounting and retention cleanup remain separate work. Do not treat this feature as encrypted archival storage for highly sensitive images.
- Image attachments are tied to the API Key that uploaded them. A conversation containing images must continue using that same Key for subsequent multimodal requests; another Key may view the conversation but cannot submit it.
- Audio/voice, rich citation/reference cards (safe public HTTPS source links are supported, but the full card UI is not), tool/function calling UI, KaTeX typesetting and complete ChatGPT component parity remain unimplemented. Native MathML is preserved as MathML markup, but a dedicated visual math typesetting engine is not bundled.
- No durable token-level SSE replay. W-04 can reconcile the original request and restore final Telemetry text when available; the native sidecar can independently rehydrate the last available snapshot, but neither mechanism replays missing text deltas or recovers an active request after its Broker state has been lost.
- Server-side conversation search, configurable message retention, export/import API and administrator retention controls are not yet implemented.
- Queued Mode 1 requests can be cancelled before Worker selection. Once a Worker dispatch has completed, the existing Worker/Broker terminal path remains authoritative; the user-session API only sends the cancel signal and does not fabricate a Worker terminal.
- The UI does not create API Keys; users create and manage them in `/console#keys`.
- The Key is not persisted locally, but it is available to same-origin page JavaScript while the page is open; XSS prevention remains important.

## Security boundaries

- No third-party script, font, analytics or remote runtime is loaded.
- The page uses a same-origin CSP and does not render untrusted HTML.
- API Key plaintext is not stored in localStorage, sessionStorage, conversation history or exported Markdown.
- User session and Key ownership are enforced by existing server endpoints; the page is not an authorization boundary.
- The Mode 1 generation ingress requires both the HttpOnly user session and the selected managed API Key; it rejects master-key use and cross-account Key ownership.
- Native sidecar reads use the HttpOnly user session, resolve the request's API Key owner from Telemetry, conceal cross-user request IDs as 404, and are marked private/no-store with Cookie variation. The API Key itself is never returned by the native sidecar.
- FileStore persists a SHA-256 digest with each new file. Every read verifies both stored byte length and SHA-256 before returning bytes; legacy index rows without a digest are backfilled on first successful read. A mismatch fails closed as an I/O integrity error.
- Image preview uses `GET /api/user/files/{file_id}/content`, which requires the HttpOnly user session, checks that the file's owner API Key belongs to the current account, and serves only `vision` PNG/JPEG/WebP/GIF bytes with `nosniff`, sandbox CSP, same-origin resource policy and `private, no-store` caching. Successful previews expose the verified digest as an ETag and `X-Chat2API-Content-SHA256` so byte identity can be checked without changing the standard FileObject JSON shape.
- `DELETE /api/user/files/{file_id}` applies the same account/owner/purpose/MIME checks, requires same-origin Origin/Fetch Metadata, and permits cleanup with the user session even if the uploading API Key has since been disabled.
- Image file IDs are validated before being restored from localStorage. The client never persists raw image bytes or API Key plaintext.
