# Mode 1 Native Web Delivery — Architecture and Acceptance Plan

Status: design baseline for Issue #854; data-plane implementation is gated.

Issue: https://github.com/b8vipvip/chat2api/issues/854

## 1. Product contract

Chat2API Web Chat remains the user-facing application. A separate authenticated Web BFF owns request identity, user/session association, conversation mapping, cancellation, recovery and content delivery. Actual generation may use the official ChatGPT web application only through an interaction mechanism that is explicitly authorized for this deployment.

The target flow is:

`Chat2API Web Chat → authenticated Web BFF → user-bound browser runtime → official ChatGPT web UI → validated native-content delivery → Web Chat Renderer`

The OpenAI-compatible `/v1` API remains a separate Mode 2 ingress and is not used as the Mode 1 generation backend.

## 2. Mandatory authorization and credential boundary

The Phase 0 feasibility audit remains binding until superseded by written authorization and a reviewed architecture. This implementation must not:

- request, collect, persist, export, or forward ChatGPT passwords, session cookies, browser storage, or private bearer tokens;
- proxy `chatgpt.com` pages, private `/backend-api/*` endpoints, WebSocket internals, or undocumented protocols;
- spoof browser fingerprints, bypass login, CAPTCHA, Turnstile, rate limits, or other access controls;
- pool, share, rotate, or transfer one ChatGPT account/session across users;
- expose a ChatGPT plan session as a general-purpose OpenAI-compatible API.

The default candidate runtime is a browser owned and controlled by the individual end user. Authentication stays in that browser. The server may store an opaque runtime binding and request metadata, but never upstream credentials. Requests must be initiated by the authenticated user or by automation that the same user explicitly authorizes.

DOM/media capture and automated page interaction must use a mechanism that is confirmed to be permitted for the target deployment. Until that basis is documented, the data plane remains disabled; no implementation may silently fall back to private endpoints or credential extraction.

## 3. Component ownership

| Component | Responsibility | Required invariant |
|---|---|---|
| Web Chat client | Prompt, attachments, model selection, live rendering | User never leaves Chat2API |
| Web BFF | User authentication, request ID, conversation ID, runtime binding, cancellation and recovery | User/session owner is authoritative |
| Runtime binding store | Bind one user to their own approved browser runtime | No shared-account or cross-user dispatch |
| Browser interaction adapter | Interact with the visible official web UI using the approved mechanism | No cookie/token export or private API calls |
| Native capture adapter | Convert approved native output into a bounded structured snapshot and media references | No raw HTML, script, CSS, arbitrary remote URL or unowned file |
| Native delivery service | Validate, persist and stream structured content | Reuse existing owner-scoped NativeContentStore/FileStore and request terminal authority |
| Web Chat Renderer | Render text, media, code, files, math, charts and tool states | Safe allowlist renderer; same-origin owned downloads |

The existing `ChatRequestService` is a dispatch seam, not a complete Web BFF. It does not own authentication, user/runtime binding, terminal state, SSE, cancellation, persistence or billing. Those responsibilities must remain explicit and must not be duplicated inside the Worker.

## 4. Implementation phases

1. **Legacy cleanup** — remove the old Cookie-proxy test switch, its persistence/API/UI and the old mode-selection branch. Keep Mode 2 ingress behavior unchanged.
2. **Authorization/runtime gate** — document the permitted official-web interaction mechanism and prove that the runtime is user-controlled. Add fail-closed capability checks; no runtime is eligible by default.
3. **Identity and session binding** — add a durable, owner-scoped binding from a Chat2API user to exactly one approved runtime; reject stale, unpaired, shared or cross-user bindings.
4. **Web BFF** — add a session-authenticated native-chat endpoint with stable request/conversation IDs, idempotent dispatch, cancellation and owner-checked recovery. Reuse the existing Broker/ChatRequestService terminal owner.
5. **Native content delivery** — add validated revisioned text/media/tool snapshots, chunk integrity, owner-scoped file references and reconnect delivery. Do not make a partial snapshot final.
6. **Renderer and persistence** — render all supported rich blocks in the existing Web Chat and persist conversation/session mappings with the existing security and retention guarantees.
7. **Acceptance** — production `app.entry:app` integration, real browser tests, cross-user isolation, interrupted-stream recovery, media/file integrity and negative authorization tests.

## 5. Issue #854 acceptance matrix

- Text streams incrementally and final content is preserved.
- Images, video and audio are delivered as owned media assets with verified content identity.
- Code and files preserve content and authenticated download behavior.
- Math and charts use structured, validated representations.
- Tool activity and results preserve their state transitions.
- Conversations have stable per-user IDs and survive refresh/reconnect.
- Cancellation and recovery target the original request; no duplicate generation is submitted.
- A user cannot observe, dispatch through, or recover another user's runtime, session, request, conversation or media.
- No upstream credential, cookie, browser storage value or private protocol payload enters server logs, API responses or persistent server storage.
- Mode 2 SDK/API behavior and its request/terminal/billing owners remain unchanged.

## 6. Release gates

The Mode 1 data plane must remain unavailable until all of the following are evidenced:

- explicit authorization or a documented supported mechanism for the exact official-web interaction and native capture path;
- proof that each runtime is controlled by the corresponding end user, or written approval for a different deployment model;
- threat model and review for account/session isolation, browser messaging, media/file delivery and data retention;
- integrated automated tests and real desktop/mobile browser E2E;
- no private-web API proxy, cookie/session extraction, access-control bypass or shared-account behavior.

A UI switch or administrator preference is not an authorization grant and must not be able to bypass these gates.
