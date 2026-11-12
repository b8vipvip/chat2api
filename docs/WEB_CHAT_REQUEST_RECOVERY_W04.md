# W-04 — request snapshot recovery after Web Chat disconnect

Status: **owner-scoped recovery API and client polling state machine implemented as
independent modules; not yet wired into the production page or app.entry:app**.

## Why this is snapshot recovery, not token replay

The existing Mode 2 route accepts a client-supplied `X-Chat2API-Request-ID`,
and the Broker accumulates request text in memory, but its queue is consumed by
the original SSE response iterator and is not a durable event log. Reposting
the same ID is explicitly rejected by the API admission guard. Therefore this
work does not claim that an interrupted SSE byte stream can be replayed token
for token.

Instead, it safely recovers the final persisted response for the *same* request
ID after request-v6/Broker finalization has stored it in Telemetry. While the
request is queued/running, the client polls status only. Once the authoritative
server row is `completed`, it retrieves the final `response_text`. This avoids
duplicate dispatch and avoids showing provisional text as a final answer.

## API

`GET /api/user/chat/requests/{request_id}/recovery`

- Requires the existing HttpOnly user-session cookie.
- Verifies that the request's `api_key_id` belongs to the current user.
- Returns 404 for unknown and non-owned request IDs to avoid ownership probing.
- Returns `queued` / `running` status while the original request is still
  admissible/active. It never returns in-progress assistant text.
- Returns final text only when Telemetry's authoritative status is
  `completed` and `response_text_available` is true.
- For `error`, `cancelled`, or `stalled`, returns terminal status but never
  exposes a provisional/forensic candidate as a completed assistant answer.
- A `running` row with no corresponding Broker state is reported as
  `recovery_unavailable`/unknown. The server does not recreate the Broker
  request or dispatch a second Worker operation.
- Responses use `Cache-Control: private, no-store` and `Vary: Cookie`.

## Cancellation signal

`POST /api/user/chat/requests/{request_id}/cancel`

- Requires the existing HttpOnly user-session cookie, request ownership, and a same-origin Origin/Fetch Metadata check.
- Only signals the Worker assigned to an active Broker request; it does not write Telemetry status, resolve the Broker future, or claim a terminal result.
- The Worker emits the existing `chat.cancelled` event. request-v6/Broker remain the sole terminal authority.
- Repeated cancel calls are idempotent. Terminal requests return their existing terminal status; queued/stale/unavailable Worker states return a non-terminal conflict.
- Responses and application errors are private/no-store and vary by Cookie.

## Client controller

`app/chat_stream_recovery_v1.js` exposes `Chat2APIStreamRecoveryV1`:

- Stable request ID generator matching the existing `req_...` contract.
- Polls only the recovery GET endpoint, with bounded backoff and a 15-minute
  default ceiling.
- Handles auth expiry, missing/non-owned requests, terminal failures, stale
  running state, invalid responses, and user-stopped polling.
- `cancel(record)` sends a same-request POST to the cancellation endpoint and
  reports only whether the Worker signal was accepted; it never marks the
  request terminal. Polling can continue until the Worker terminal is observed.
- The `assistant_message_id` is carried through callbacks so the later UI
  integration updates the exact message, not a list index.
- `stop()` stops polling only; it does not imply Worker cancellation.

## Boundaries and follow-up integration

- This PR does not modify `app.entry.py`, `chat_client_v1.js`, or
  `chat_client_v1.html`; those are shared integration points currently being
  changed by W-01/W-03.
- A later integration PR must add `X-Chat2API-Request-ID` to the initial
  Chat Completions POST, persist the pending request/message IDs before dispatch,
  load this controller after the shared-file owners settle, and reconcile only
  the matching assistant message.
- The existing Telemetry lookup is bounded to its recent in-memory window
  (up to 500 request rows, including the rows loaded at restart). If the target
  row is evicted, recovery returns 404; this PR does not create a second
  persistence/index owner to extend that retention window.
- Exact SSE event replay, per-event cursor/Last-Event-ID, and partial token
  continuation require a separately designed durable event journal. They are
  not implemented by this snapshot API.
- This does not change Broker terminal authority, SSE transport truth,
  native-content finality, or request-v6 cancellation.
- No G0 data migration, production deployment, or multi-instance recovery is
  claimed.

## Tests

`pytest -q tests/test_web_chat_request_recovery_v1.py`

Covers owner/non-owner boundaries, no-store, queued admission, active Broker
state, stale running state, failed provisional output suppression, same-origin
and idempotent Worker cancellation signalling without terminal takeover, and a
Node harness asserting recovery polls only the same-request GET endpoint while
cancellation uses only the dedicated same-request cancel POST (never a new
generation POST).
