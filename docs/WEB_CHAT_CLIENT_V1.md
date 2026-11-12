# Chat2API Web Chat Client v1

Status: first interactive browser client; not a complete ChatGPT 1:1 replacement.

## Entry points

- `GET /chat` serves the standalone chat UI.
- `/console` links to the chat client.
- `/assets/chat2api-chat-client-v1.js` and `/assets/chat2api-chat-client-v1.css` are served with no-store headers.
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
- Image attachments: PNG, JPEG, WebP and GIF; up to 4 images per message, 10 MiB per image and 20 MiB total per message. Images are uploaded through the selected Key's `files` scope and sent as standard Chat Completions `image_url` content parts using Chat2API-owned file IDs.
- Streaming Chat Completions, incremental text, stop generation and incomplete-stream warning when `[DONE]` is missing.
- Safe basic Markdown rendering for headings, paragraphs, lists, blockquotes, tables, inline code, fenced code and HTTPS/HTTP links. Raw HTML is rendered as text, not executed.
- Copy message/code and export current conversation to Markdown.
- Conversation history is namespaced by user ID in browser localStorage; API Key material is memory-only.

## Explicit limitations / not yet implemented

- Conversation history is browser-local only; it does not sync across devices or persist on the server.
- Local history is capped at 15 conversations, 30 messages per conversation and 8,000 characters per message.
- No image/file attachments, generated-image cards, audio/voice, citations/reference cards, tool/function calling UI, native sidecar blocks, KaTeX/MathML rendering or complete ChatGPT component parity.
- No stream resume/reconnect after a lost connection. The UI warns when it does not receive the terminal `[DONE]` marker.
- No server-side conversation search, message retention policy, export/import API or administrator retention controls.
- The UI does not create API Keys; users create and manage them in `/console#keys`.
- The Key is not persisted locally, but it is available to same-origin page JavaScript while the page is open; XSS prevention remains important.

## Security boundaries

- No third-party script, font, analytics or remote runtime is loaded.
- The page uses a same-origin CSP and does not render untrusted HTML.
- API Key plaintext is not stored in localStorage, sessionStorage, conversation history or exported Markdown.
- User session and Key ownership are enforced by existing server endpoints; the page is not an authorization boundary.
- Image preview uses `GET /api/user/files/{file_id}/content`, which requires the HttpOnly user session, checks that the file's owner API Key belongs to the current account, and serves only `vision` PNG/JPEG/WebP/GIF bytes with `nosniff`, sandbox CSP, same-origin resource policy and `private, no-store` caching.
- Image file IDs are validated before being restored from localStorage. The client never persists raw image bytes or API Key plaintext.
