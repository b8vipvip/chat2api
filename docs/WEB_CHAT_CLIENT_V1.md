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
5. The raw Key is held only in page memory and used as a Bearer token for standard `POST /v1/chat/completions` streaming requests. The page revalidates the same user session before each chat request. The Key ID preference may be stored locally; the raw Key is never written to localStorage.
6. The request does not set a Mode 1 header. It uses the default Mode 2 OpenAI-compatible API ingress.

The page sends the full ordered conversation using only standard Chat Completions fields (`model`, `messages`, `stream`). The server's default full-history behavior preserves multi-turn context; no private `prompt_mode` field is required.

## Implemented in v1

- Responsive dark/light chat layout with desktop sidebar and mobile sidebar.
- User-session check and owner-scoped API Key selection.
- Online model list with user model-catalog fallback.
- New conversation, local conversation list, local search, rename and delete.
- Image attachments: PNG, JPEG, WebP and GIF; up to 4 images per message, 10 MiB per image and 20 MiB total per message. Files can be selected, pasted or dragged onto the composer. Drag/drop uses an isolated event adapter and funnels accepted File objects through the same MIME/size/key-owner validation as the picker. Images are uploaded through Chat2API's JSON `/v1/files` endpoint under the selected Key's `files` scope. The message uses the standard Chat Completions `image_url` content-part envelope, with a Chat2API-specific `file_...` reference in `image_url.url` to avoid storing/re-sending base64 bytes in browser history.
- Streaming Chat Completions, incremental text, stop generation and incomplete-stream warning when `[DONE]` is missing.
- W-04 request recovery: each turn carries a request ID, pending request/message identity is stored in account-scoped local history, and refresh/network interruption polls the owner-checked final Telemetry snapshot without re-posting generation. This restores a final response only when the server has one; it does not replay missing token deltas.
- Safe basic Markdown rendering for headings, paragraphs, lists, blockquotes, tables, inline code, fenced code and HTTPS/HTTP links. Raw HTML is rendered as text, not executed.
- Copy message/code and export current conversation to Markdown.
- Conversation history is namespaced by user ID in browser localStorage; API Key material is memory-only.

## Explicit limitations / not yet implemented

- Conversation history is browser-local only; it does not sync across devices or persist on the server.
- Local history is capped at 15 conversations, 30 messages per conversation and 8,000 text characters per message. Image bytes remain in the existing server FileStore; browser history stores only validated file IDs and metadata.
- The existing FileStore persists file bytes on disk without application-level encryption at rest. Deleting an individual conversation makes a best-effort session-authenticated deletion of image files no longer referenced by other local conversations. Clearing browser storage, losing browser state or local-history eviction cannot guarantee server cleanup; no server-side retention policy is added. Do not treat this feature as encrypted archival storage for highly sensitive images.
- Image attachments are tied to the API Key that uploaded them. A conversation containing images must continue using that same Key for subsequent multimodal requests; another Key may view the conversation but cannot submit it.
- No generic document/file attachments (PDF, Office documents, etc.), generated-image cards, audio/voice, citations/reference cards, tool/function calling UI, native sidecar blocks, KaTeX/MathML rendering or complete ChatGPT component parity.
- No durable token-level SSE replay. W-04 can reconcile the original request and restore final Telemetry text when available, but cannot replay missing stream deltas or recover an active request after its Broker state has been lost.
- No server-side conversation search, message retention policy, export/import API or administrator retention controls.
- The UI does not create API Keys; users create and manage them in `/console#keys`.
- The Key is not persisted locally, but it is available to same-origin page JavaScript while the page is open; XSS prevention remains important.

## Security boundaries

- No third-party script, font, analytics or remote runtime is loaded.
- The page uses a same-origin CSP and does not render untrusted HTML.
- API Key plaintext is not stored in localStorage, sessionStorage, conversation history or exported Markdown.
- User session and Key ownership are enforced by existing server endpoints; the page is not an authorization boundary.
- FileStore persists a SHA-256 digest with each new file. Every read verifies both stored byte length and SHA-256 before returning bytes; legacy index rows without a digest are backfilled on first successful read. A mismatch fails closed as an I/O integrity error.
- Image preview uses `GET /api/user/files/{file_id}/content`, which requires the HttpOnly user session, checks that the file's owner API Key belongs to the current account, and serves only `vision` PNG/JPEG/WebP/GIF bytes with `nosniff`, sandbox CSP, same-origin resource policy and `private, no-store` caching. Successful previews expose the verified digest as an ETag and `X-Chat2API-Content-SHA256` so byte identity can be checked without changing the standard FileObject JSON shape.
- `DELETE /api/user/files/{file_id}` applies the same account/owner/purpose/MIME checks, requires same-origin Origin/Fetch Metadata, and permits cleanup with the user session even if the uploading API Key has since been disabled.
- Image file IDs are validated before being restored from localStorage. The client never persists raw image bytes or API Key plaintext.
