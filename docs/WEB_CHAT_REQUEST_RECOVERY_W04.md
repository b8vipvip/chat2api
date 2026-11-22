# W-04 — Web Chat durable delivery and request recovery

Status: **integrated into the production Web Chat page and application installer**. This document supersedes the earlier proposal that described the recovery API and polling controller as unintegrated standalone modules.

## Production integration

- `install_web_chat_features_v1(app)` installs the owner-scoped conversation, file-download and request-recovery APIs.
- `app/chat_client_v1.js` submits one request ID for each generation and persists the pending assistant-message identity before relying on recovery.
- The browser replays the same request's durable delivery journal using `Last-Event-ID`; it does not submit a second generation when a stream disconnects.
- The server-side SQLite event journal stores ordered, owner-scoped delivery events and terminal markers. Event IDs are monotonic per request, and the first terminal event is absorbing against late Worker retries or competing terminal messages.
- The recovery API provides an owner-checked snapshot for queued/running and terminal requests. It does not expose provisional text as a completed answer.
- Cancellation is a same-request, owner-checked signal. Queued cancellation is coordinated with admission; dispatched cancellation remains under Worker/Broker terminal authority.
- Worker terminal outbox records are retained until the server sends a positive ACK. After a Broker restart, replay is bound to the same authenticated Worker, request ID, client ID and API Key owner. Recoverable chat completion runs the idempotent archive/finalization path; unsupported or incomplete recovery evidence remains provisional/stalled rather than being represented as a successful answer or charged twice.

## Recovery boundaries

This is durable server delivery replay, not a claim that the upstream ChatGPT webpage can resume a token stream from an arbitrary point after the Worker or browser process has disappeared.

- A browser/network disconnect can replay journaled delivery events from the last acknowledged event ID without dispatching a second generation.
- A Broker restart depends on the existing Worker's durable terminal outbox replay. The server does not invent a terminal result or launch a replacement generation.
- Final media archival, generated-file ownership, wallet/quota reconciliation and terminal telemetry must remain idempotent. If a particular media result cannot be verified or restored, the request must remain non-successful and must not be double charged.
- Exact real Worker + signed-in ChatGPT browser acceptance remains a deployment-level test. Local WebSocket and two-browser-profile CI does not substitute for that test.

## APIs

- `GET /api/user/chat/requests/{request_id}/recovery`
- `GET /api/user/chat/requests/{request_id}/events` (supports `Last-Event-ID`)
- `POST /api/user/chat/requests/{request_id}/cancel`

All routes require the HttpOnly user session and verify ownership through the request's API Key. Responses are private/no-store and vary by Cookie.

## Required staging acceptance

Follow [WEB_CHAT_RECOVERY_ACCEPTANCE.md](WEB_CHAT_RECOVERY_ACCEPTANCE.md). Keep the test isolated from production traffic, use a dedicated API Key and a non-sensitive prompt, and do not include credentials or private conversation content in evidence.

The staging run must confirm:

1. Browser reconnect resumes the same request from the last event ID and does not issue a second generation POST.
2. API/Broker restart leaves the real Worker and ChatGPT tab alive; the same Worker replays the terminal outbox record.
3. The server positively ACKs only after it has persisted or reconciled the terminal event.
4. The final Telemetry, archive and quota/wallet state agree; there is no false success, duplicate archive, or duplicate charge.
5. The Worker clears its durable outbox only after a positive ACK.

## Automated coverage

The repository includes focused tests for event-journal ordering and terminal absorption, owner-scoped replay, two-server Worker outbox replay, cancellation/admission races, generated-media archive recovery, and browser reconnect state. These tests establish application-level behavior; they do not prove the user's live Worker login or staging deployment behavior.
