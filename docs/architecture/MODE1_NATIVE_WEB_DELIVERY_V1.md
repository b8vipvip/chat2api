# Mode 1 — Web Chat over the Existing Worker Generation Path

Status: corrected architecture baseline for Issue #854.

Issue: https://github.com/b8vipvip/chat2api/issues/854

## 1. Correct product definition

Mode 1 and Mode 2 use the **same existing Chat2API Worker devices and the same ChatGPT generation source**. The Worker devices already have their ChatGPT browser sessions and are the generation resources used by the current Mode 2 flow.

Mode 1 does **not** create a browser runtime for every Web Chat user. Web Chat users do not install Chrome extensions, pair a local browser, or supply their own ChatGPT login. Do not add a per-user ChatGPT-account/runtime binding system.

The distinction is the client interaction and delivery contract:

- **Mode 1:** a Chat2API Web Chat conversation UI, patterned after the administrator Playground's “聊天对话” experience, with multi-turn conversation, model selection, attachments, streaming/progress, cancellation/recovery and native rich-content rendering.
- **Mode 2:** the existing OpenAI-compatible API surface for SDK/API clients.

Both modes authenticate Chat2API requests with a selected Chat2API API Key and dispatch generation to the existing Worker fleet. The API Key is a Chat2API request principal; it is not a ChatGPT credential and must not be used as a Worker/runtime identity.

## 1.1 API Key routing contract

The key selected in server-side Client Configuration is a **Mode 1-only Web Chat service key**. It is a server-side credential reference, not a browser credential:

- Only an unbound server-managed API Key may be selected. A key created under a user's Account Settings is account-owned and cannot be promoted to the shared Web Chat key.
- Once selected, that key is reserved for Mode 1. Requests presenting it to the generic `/v1` API-key dependency are rejected rather than silently routed through Mode 2.
- The Web Chat browser authenticates with its HttpOnly Chat2API account session. The server resolves the configured key internally, then uses a separate per-user internal identity for request, file, conversation and billing ownership.
- API Keys created by users in Account Settings remain Mode 2 by default and continue to work with the original OpenAI-compatible `/v1` endpoints. They are not used by the Web Chat adapter.
- Clearing the Client Configuration releases the server-managed key from the Mode 1 reservation; it can then be used as a normal Mode 2 managed key again.

This selection is server-controlled. A request header, user role, browser-supplied key ID or `X-Chat2API-Mode` value cannot promote an account-owned key to Mode 1.

## 2. Correct request and generation flow

`Chat2API Web Chat → server-side Client Configuration shared API Key → authenticated Mode 1 Web Chat adapter → existing API Key authorization / model policy / quota / file ownership → existing Worker selection and reconnect handling → RequestBroker + ChatRequestService + ClientRegistry → existing Worker device → its already logged-in ChatGPT browser → existing native capture / media transfer → validated native-content delivery → Web Chat Renderer`

The exact Worker is selected by the existing Worker scheduling/routing rules (including any existing explicit Worker-routing input). Do not create a new `user_id → worker_id` binding, and do not infer Worker identity from API Key ID.

The server-resolved shared Mode 1 API Key (or SDK-supplied Mode 2 API Key) must flow through the same existing Chat2API principal checks and ownership accounting used by API requests:

- authenticate the Key and enforce its `chat` scope;
- enforce existing model entitlement and quota/billing rules;
- associate request telemetry, uploaded files and generated artifacts with the authenticated Key owner;
- prevent a user from reading another user's requests, conversations, files or native media.

The Web Chat conversation UX should reuse the interaction patterns of `app/admin_playground_chat_v69.js` (multi-turn messages, model/reasoning selection, attachments and request status), while replacing administrator-only endpoints and master-key assumptions with the signed-in account session and the server-side shared-key policy. The browser must not select, receive or store the shared API Key.

## 3. Mode 1 vs Mode 2 — what is shared and what differs

| Area | Shared behavior | Mode 1 | Mode 2 |
|---|---|---|---|
| Generation source | Existing Worker devices and their current ChatGPT browser sessions | Same | Same |
| Worker dispatch | Existing Worker registry, selection, reconnect handling and request lease | Same path | Same path |
| Request identity | Signed-in account session + per-user internal ownership identity; server-managed shared API Key authorizes Mode 1 | Resolved only by server-side Client Configuration; never sent to the browser | Supplied by SDK/API client |
| User interface | — | Chat2API multi-turn Web Chat | External SDK/API client |
| Delivery | Existing Worker terminal and native evidence authorities | Native rich content, media, files, charts/math and tool state | OpenAI-compatible response contract |
| Conversation UX | Existing authenticated ownership rules | Web Chat conversation history and reconnect experience | Client-managed message history |

Mode 1 is not a second generation backend, not a per-user browser product and not a new Worker fleet. It is a Web Chat adapter and native-content delivery experience over the existing generation resources.

## 4. Component ownership

| Component | Responsibility | Required invariant |
|---|---|---|
| Web Chat client | Multi-turn conversation UI, model selection, attachments, native rendering; authenticates with the account session | User stays in Chat2API; no API Key selector or token in browser |
| Server-side Client Configuration / shared API Key | Select an unbound server-managed API Key reserved for Web Chat Mode 1 | Never expose the token to the browser or silently substitute the administrator master key |
| Mode 1 Web Chat adapter | Validate the selected Key, establish request/conversation identity, manage Web Chat transport, cancellation and recovery | Same principal, quota and owner checks as existing API requests |
| Existing API policy layer | Key scope, model policy, quota, file and artifact ownership | No weaker authorization path for Web Chat |
| Existing Worker registry/scheduler | Select and route to an available compatible Worker | Reuse existing Worker fleet; no per-user runtime binding |
| Existing RequestBroker / ChatRequestService | Request lease and Worker `chat.request` dispatch | One Broker terminal owner; do not duplicate dispatch ownership |
| Existing Worker browser runtime | Use its already logged-in ChatGPT browser to generate | Same generation source as Mode 2 |
| Native capture and delivery | Reuse Worker native-content capture, chunk validation, `NativeContentStore`, media/file archives and request terminal evidence | Validate revisions, integrity, ownership and completeness |
| Web Chat Renderer | Render text, images, video/audio, code, files, math, charts and tool states | Safe allowlist renderer; owner-checked media/download URLs |

## 5. Implementation phases

1. **Legacy cleanup** — remove the obsolete Mode 1 Cookie-proxy test switch, its persistence/API/UI and the old mode-selection branch. Keep Mode 2 behavior unchanged.
2. **Web Chat Key/config contract** — the server-side Client Configuration selects an unbound managed API Key for all Web Chat Mode 1 requests. The browser authenticates with its HttpOnly account session and never receives or selects this Key. Keep user-created Account Settings API Keys on the original Mode 2 `/v1` path. Use existing API Key authentication, model permissions, quota and ownership checks; never expose or hardcode the administrator master key.
3. **Mode 1 Web Chat adapter** — implement the multi-turn Web Chat request path using the selected Key and the existing Worker selection, Broker, `ChatRequestService` and registry dispatch path. Keep the existing Worker scheduling authority; do not create user-to-browser bindings.
4. **Native rich-content delivery** — connect the Web Chat request to existing Worker native snapshot/chunk events, `NativeContentStore`, `NativeChunkAssembler`, `NativeDomEvidenceStore`, `FileStore` and media/generated-file archives. Ensure a partial or unverified snapshot is never presented as complete.
5. **Conversation, streaming and recovery** — persist stable user/key-owned conversation and request associations; implement streaming/progress, cancellation, reconnect and recovery without duplicate generation or a second terminal owner.
6. **Renderer completion** — render native text, images, video, audio, code, files, math, charts and tool activity/results in the Web Chat.
7. **Acceptance** — production `app.entry:app` integration, real browser E2E, API Key isolation, concurrent users, Worker reconnect, interrupted-stream recovery, media/file integrity and Mode 2 regression.

## 6. Issue #854 acceptance matrix

- Web Chat behaves like the Playground “聊天对话” experience: multi-turn, model selection, attachments and native content, using the server-configured shared Mode 1 Key without exposing a per-user API Key selector.
- The selected Key is the request principal for authorization, model access, quota, request telemetry and file/artifact ownership.
- Generation is executed by the same existing Worker devices and already logged-in ChatGPT browser sessions used by Mode 2.
- Web Chat requests reuse the existing Worker scheduling/registry, Broker and ChatRequestService dispatch path.
- No per-Web-user Chrome extension, ChatGPT login, browser pairing or `user_id → worker_id` binding is required.
- Text streams incrementally and final content is preserved.
- Images, video and audio are delivered as verified, owner-scoped media assets.
- Code and files preserve original content and authenticated download behavior.
- Math and charts use structured, validated representations.
- Tool activity and results preserve their state transitions.
- Conversations have stable owner-scoped IDs and survive refresh/reconnect.
- Cancellation and recovery target the original request; no duplicate generation is submitted.
- A user cannot observe or recover another user's request, conversation, file or media.
- Mode 2 SDK/API behavior and its Worker, request-terminal and billing owners remain unchanged.

## 7. Security and operational boundaries

- The Chat2API API Key is only a Chat2API authorization/ownership credential. It is never a ChatGPT password, cookie, browser-storage value or upstream bearer token.
- Reuse the current Worker login/runtime and supported Worker messaging contract. Do not add cookie extraction, credential export, private ChatGPT endpoint proxying, browser fingerprint spoofing or access-control bypass.
- Do not log API Key plaintext, ChatGPT page contents or raw private browser state.
- Do not let the Web Chat adapter create a second Worker lease, terminal authority, quota ledger or generated-file owner.
- Existing Phase 0 restrictions against a separate hosted Cookie/private-endpoint proxy remain in force; this design does not require such a proxy.


## 8. Implementation progress — 2026-11-20

The first Mode 1 delivery slice is implemented on the corrected shared-Worker architecture:

- The /chat user client does not expose per-user API Key configuration. It authenticates the signed-in Chat2API account and uses the API Key configured by the server-side Client Configuration as the shared Web Chat request credential. The dedicated POST /api/user/chat/completions Mode 1 ingress resolves that server-managed key, validates its active state, scopes and model entitlement, and binds the request to the signed-in user/session identity for conversation, file and telemetry ownership. It then calls the same internal Chat Completions request handler used by Mode 2; it does not call the OpenAI-compatible /v1/chat/completions route. Never silently fall back to a user-managed key or administrator master key.
- A separate same-origin, user-session-authenticated native-content read/SSE adapter is available at /api/user/chat/requests/{request_id}/native-content and /events. It checks Telemetry ownership against the signed-in user's API Keys before returning content.
- The native-content adapter calls the exact app.state.native_response_snapshot authority already used by /v1/native-content; the generation adapter reuses the same internal request handler, quota/wallet admission, Worker selection, Broker and ChatRequestService. Neither adapter creates another store, dispatch, Broker lease or terminal owner.
- The Web Chat renderer receives the full server-validated native tree (including safe SVG attributes and MathML), request-owned verified media assets and archived generated-file descriptors. It applies a second browser-side allowlist and creates DOM nodes rather than inserting captured HTML.
- The client subscribes to native revisions for the same request ID, removes provisional content if finality cannot be verified, and rehydrates the last available snapshot after refresh without re-submitting generation.

### W-01 — durable conversation persistence and cross-device synchronization

The first W-01 vertical slice is now implemented:

- Add a private SQLite-backed conversation store at data_dir/web_chat_conversations_v1.sqlite3. Rows are keyed by (authenticated user_id, conversation_id), with a per-row revision and a payload-free deletion tombstone.
- Add GET /api/user/chat/conversations, PUT /api/user/chat/conversations/{conversation_id}, and DELETE /api/user/chat/conversations/{conversation_id}?expected_revision=N. All routes require the existing HttpOnly user session, return private/no-store with Vary: Cookie, and do not accept caller-supplied user IDs.
- Validate conversation/message IDs, message roles, bounded content and attachment metadata, request ID format, the configured shared API Key's server-managed ownership and any request owner already present in Telemetry. Reject cross-account Key/request associations.
- Use optimistic revisions and return 409 with the current row on stale writes. The browser merges messages by stable message ID and retries; a tombstone prevents an offline device from resurrecting a deleted conversation.
- Bootstrap merges the existing per-user localStorage history with server rows, uploads local-only legacy conversations, and then treats the server as the cross-device source of truth while keeping localStorage as a cache/offline fallback.
- Preserve empty pending assistant placeholders so a refresh can retain the original request ID and allow the existing recovery path to reconcile the original request rather than dropping the placeholder.

### W-02 — cancellation during Worker admission

A second vertical slice closes the queued-request cancellation race:

- Create an owner-scoped cancellation signal before a Mode 1 request is recorded as queued. The existing user-session cancel API can signal that request while Worker selection/reconnect grace is still pending.
- Race Worker admission against the cancellation signal. If cancellation wins before a Broker lease exists, record a terminal cancelled admission result, release quota/wallet holds and do not dispatch to a Worker.
- If the Broker lease exists but the initial Worker dispatch has not happened, serialize the transition to running + dispatch against the cancel endpoint with one application lock. A cancellation that wins first publishes the cancellation through RequestBroker, records the cancelled request and releases the lease; if dispatch wins first, the cancel endpoint sends chat.cancel only after the Worker request has been sent.
- The cancel endpoint waits briefly for a queued cancellation to reach its terminal telemetry projection and returns whether it was queued. The browser preserves that result and waits for the cancel handshake before aborting the generation POST, avoiding a transport-abort race that could strand a queued request.
- Add tests for queued cancellation idempotency, owner-scoped queued signal delivery, admission-task interruption and the browser recovery client's queued flag.

The running Worker remains the terminal authority after dispatch; the Web Chat API never fabricates a Worker completion. The W-02 cancellation slice is separate from the W-04 durable SSE event journal described below.

### W-03 — safe citation links in native content

The native-content protocol now carries a bounded `href` only on anchor nodes. The Worker capture, server snapshot validator and browser renderer independently allow only public HTTPS DNS destinations; they reject active schemes, credentials, IP literals, local/private hostnames, non-standard ports and ChatGPT session URLs. Links open in a new tab with `noopener noreferrer nofollow` and `no-referrer`. The legacy `/v1/native-content` projection converts anchors to inert spans so its existing protocol remains unchanged.

The MathML allowlist has also been expanded for matrices, fences, phantom/space/style nodes and additional presentation structures. TeX expressions in Markdown and native math blocks are rendered with the pinned KaTeX 0.19.1 browser runtime (`trust: false`, bounded expansion/size); the production Docker image and CI vendor the JS, stylesheet and font assets locally, so runtime rendering does not depend on a third-party CDN. A source checkout that has not run `sh scripts/vendor_katex.sh` intentionally falls back to the visible TeX text. This does not imply complete parity with every ChatGPT math component or every TeX package.

### W-03 — bounded generated audio/video file playback

The existing request-scoped native-file handoff, authenticated Worker WebSocket chunk transport, staged-file verifier and durable GeneratedFileArchive now accept MP3, WAV, M4A, OGG/Opus, FLAC, MP4 and WebM files (12 MiB per file, 24 MiB per response). Both the isolated content script and page-world observer validate container signatures before bytes are staged; the server independently validates filename/MIME/signature before archive publication. The Web Chat native renderer creates controls-only audio/video players from same-origin archived file descriptors. The download endpoint supports single byte ranges for playback/seek and keeps documents/code as forced downloads. No player autostarts.

This slice covers downloadable generated media cards only. It does not claim arbitrary live audio/video elements, real-time voice streams or every embedded ChatGPT media component are captured.

### W-03 — tool/function-call activity in Web Chat

The authenticated Worker socket now forwards bounded chat.tool.call and chat.tool.result observations through the existing Broker queue. The API emits them only as a Mode 1 chat2api_tool SSE extension; Mode 2 SDK streams and the authoritative assistant text lane remain unchanged. The Web Chat correlates call/result IDs, displays running/completed/failed/cancelled state, escapes arguments and results as text, and persists the bounded activity with the owner-scoped conversation. Tool activity is presentation-only and cannot change request completion or final answer text.

The Playwright regression now opens the same owner-scoped conversation in two isolated browser profiles, verifies a persisted tool-only assistant activity card, races two writes against the same server revision and confirms one succeeds while the stale writer receives a conflict, then verifies a deletion tombstone prevents resurrection on the second profile. This covers browser-level cross-device sync and optimistic-concurrency behavior.

### W-04 — durable Worker event replay and terminal consistency

The Mode 1 event journal now persists bounded Worker deltas, snapshots, citation metadata, tool observations and terminal evidence before Broker publication. Worker-originated delta events carry stable request-scoped event IDs; SQLite enforces per-request source-event uniqueness so a replayed Worker message does not append a second copy. The event journal records UTF-16 offsets for provisional deltas so the browser can safely reconcile a replay even if its last-event cursor was not persisted before reload.

For a still-running request, the authenticated SSE recovery endpoint replays missing Worker deltas from Last-Event-ID as explicitly unverified provisional text. The browser ignores duplicate event cursors, restores provisional snapshots after cursor reset, and replaces provisional text with the canonical final delivery when it arrives. Final delivery append is an atomic prefix-resume operation: a process restart after some chunks are durable appends only the missing suffix. A completed terminal marker is written only after the exact final projection is durable; if the process stops between text persistence and terminal publication, recovery repairs the terminal cursor from matching completed Telemetry and journal state. Conflicting prefixes fail closed, and first-terminal-wins remains authoritative.

This is code-level durable SSE replay; it does not claim that a Worker event never received by the server can be reconstructed from nowhere. Full citation/reference-card parity, arbitrary live audio/video capture and complete ChatGPT component parity remain open acceptance items. KaTeX rendering is implemented for supported TeX expressions with a MathML/text fallback for unsupported or invalid input. Real Worker disconnect/reconnect and Broker/server restart behavior remain part of the end-to-end acceptance matrix.

### W-03 — safe declarative interactive controls (2026-11-26)

The first native-interaction delivery slice adds a typed control contract to the existing request-scoped native snapshot. It does not execute or transport upstream JavaScript:

- Native disclosures (details / summary) preserve their open state and remain locally expandable.
- Labeled checkbox, radio and range inputs preserve bounded state. Radio groups are re-namespaced per rendered snapshot so separate messages cannot interfere.
- Single-select controls preserve at most 64 bounded options and the selected option.
- A recognized code-block copy button is reconstructed as a local clipboard action only when it can be associated with exactly one nearby code block.
- Worker capture, the Python snapshot authority and the browser renderer each validate the same typed control schema. Unsupported input types, form submission, arbitrary button actions, event handlers, inline scripts and unknown control properties are rejected or omitted.
- The legacy /v1/native-content projection downgrades these controls to inert text so older consumers do not receive unknown interactive semantics.

Checkbox/radio/range/select state is currently a local Web Chat preview; changing it does not submit a value to the upstream ChatGPT page. The renderer labels this limitation. This slice does NOT claim remote action parity for generated apps, tabs, interactive chart legends, canvas/WebGL, iframe widgets, tool buttons or arbitrary React event handlers. Those require a separately authorized, request-scoped action bridge and remain open under Issue #854. The authoritative Markdown answer remains available if a component cannot be safely represented.
