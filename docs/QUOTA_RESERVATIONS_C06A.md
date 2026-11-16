# C-06a — in-flight token quota reservation journal

Status: **partial / Draft**. This module reserves estimated in-flight API-key
token capacity; it is not a replacement completed-usage or wallet ledger.

## Single accounting owners

- ApiKeyStore.account_completed_usage remains the only committed API-key token
  usage owner. It already de-duplicates by request_id and charges only completed
  requests.
- BillingStore.record_charge remains the existing user wallet debit authority,
  keyed by request_id and backed by the existing pricing snapshot.
- QuotaReservationStore owns only active in-flight reservation state and the
  crash-recovery handshake around the existing ApiKeyStore commit.

No second completed-usage counter or second wallet balance is introduced.

## Implemented

- SQLite reservation table with unique request_id, key_id, reserved/actual
  tokens, state, timestamps and private 0600 file.
- BEGIN IMMEDIATE serializes concurrent reservations across independent SQLite
  connections; completed tokens plus active reserved tokens are checked against
  the current API-key token_quota.
- Request IDs are idempotent while active or settled, cannot be reused across API keys, and are rejected after release/expiry so a terminal reservation cannot be mistaken for a fresh quota admission.
- State machine: reserved -> committing -> settled; failed request releases;
  only unstarted reservations expire. A committing reservation is never
  silently expired or released.
- Commit coordinator first marks actual usage as committing, then invokes the
  existing idempotent ApiKeyStore.account_completed_usage, then marks settled.
  If the process crashes between the existing usage commit and journal settle,
  reconcile() checks ApiKeyStore.charged_requests before releasing the
  reservation.
- Actual usage above the estimate increases the active reservation before
  final accounting, preventing a concurrent request from using the same
  capacity during the commit window.

Admin inspection API:
- GET /api/admin/quota-reservations: bounded, filterable read-only reservation
  journal view; returns whether request lifecycle hooks are actually integrated.
- POST /api/admin/quota-reservations/reconcile: audited recovery of interrupted
  commits, only when ApiKeyStore.charged_requests proves the charge.


## Runtime integration contract (implemented in #828)

The single C integration owner installs the runtime adapter in app.entry:
1. Chat Completions and Responses authenticate through the existing ApiKeyStore.
2. Both adapters reserve server-estimated prompt tokens plus a conservative
   4096-token output floor before Worker dispatch. Larger explicit output budgets
   increase the reservation; client values never reduce the floor.
3. Over-quota requests are rejected before Worker dispatch.
4. TelemetryStore remains the terminal projection owner. Completed requests
   commit only when total_tokens is a positive integer; error/cancelled/failed
   terminal requests release their active reservation.
5. A completed terminal with missing or malformed usage stays committing. The
   reservation is not released or marked settled without positive usage evidence.
6. Settlement verifies ApiKeyStore.charged_requests before marking the journal
   settled. Startup and the audited admin reconcile action replay persisted
   completed telemetry after a pre-charge crash, then reconcile commits already
   proven by the API-key ledger. ApiKeyStore remains the only charge authority.

This PR does **not** modify TelemetryStore, request-v6 finality, Broker, Worker
dispatch, or ApiKeyStore. It is not wired into app.entry:app; per #784 the
production composition root remains owned by one central integration PR.

## Explicit gaps

- Reservation amounts are estimates. If the caller does not reserve a
  defensible output upper bound, actual usage can exceed quota; ApiKeyStore
  will still record the true completed amount and disable the key at its
  existing quota boundary.
- This is token quota only. It does not reserve wallet cents, enforce a
  negative-balance policy, add refunds/adjustments, or replace BillingStore.
- No role/tenant quota inheritance yet; tenant quota must use explicit key
  binding and the same accounting owner.
- SQLite is single-instance. Cross-instance transactional enforcement, backup,
  restore and reconciliation belong to D/P.
- Existing in-flight requests admitted before integration must drain or be
  reconciled before reservations become mandatory.

## Tests

Covers idempotent reservation and completion, completed usage remaining owned by
ApiKeyStore, quota checks including outstanding reservations, concurrent
oversubscription rejection, failed release, non-expiring commit state, crash
reconciliation and private persistence. Full CI and production image smoke
remain required.

## 2026-11-13 runtime integration status

The runtime adapter is installed for `POST /v1/chat/completions` and
`POST /v1/responses`. Both reserve a server-side prompt estimate plus a
conservative 4096-token output floor before Worker dispatch. Explicit output
budgets above the floor increase the reservation; they do not lower it because
the compatibility matrix does not prove those fields are enforced upstream.

TelemetryStore invokes its usage-accounting callback on queued/running updates
as well as terminal updates. The reservation adapter keeps nonterminal
reservations pending, commits only completed requests with a positive integral
`total_tokens`, and releases only error/cancelled/stalled/failed/timeout
terminal states. Missing, zero or malformed usage remains in `committing`
until usage is proven. Settlement verifies the existing ApiKeyStore request
ledger before dropping the reservation.

Startup first replays persisted terminal telemetry for every active
reservation, including both reserved and committing states. This covers the
crash window after TelemetryStore appends a terminal row but before its
accounting callback begins the committing transition. Only after replay and
ledger reconciliation does startup expire remaining stale, unstarted
reservations. The audited admin reconcile action uses the same replay and
ledger-reconciliation sequence.

Runtime coverage is explicitly reported as `/v1/chat/completions`,
`/v1/responses`, `/v1/images/generations`, `/v1/audio/speech`, and
`/v1/audio/conversations`. Responses hosted `image_generation` is covered
because it delegates to the same canonical Images executor and child request
ID; the parent Responses row remains zero-token to avoid double billing.

Images admission reserves the estimated prompt tokens plus a fixed 4096-token
per-image artifact floor before Worker dispatch. This fixed floor is an
**admission hold only**, not an assertion about image token usage, provider
price, or generation cost. The current Worker image event does not carry
provider-verified usage. On successful completion, the existing API-key ledger
therefore charges only the prompt-token estimate already emitted by
`standard_usage` (marked `estimated: true`); the unused admission hold is
released when the reservation settles. Failed, cancelled and timed-out image
requests release their hold. Over-quota image requests are rejected before
Worker dispatch.

The `/v1/audio/speech` and `/v1/audio/conversations` endpoints use the
same pre-dispatch reservation lifecycle with a fixed 4096-token audio
admission floor plus any known text input estimate (speech prompt or optional
conversation instruction). For uploaded-audio conversations, the input audio
bytes are deliberately **not** converted into fabricated token counts; the
fixed floor is admission capacity only, not a maximum input/output budget or
a claim about provider cost. Terminal usage continues to estimate the known
text input plus returned transcript text. Generated audio bytes and
provider-side audio usage are not converted into invented token counts. The
current Voice Worker event does not carry provider-verified usage. Failed,
cancelled and timed-out speech/conversation requests release their hold.
Over-quota speech/conversation requests are rejected before Worker dispatch.
If an audio conversation completes with no text instruction and no transcript,
its estimated text usage is explicitly zero: the admission hold is released
without inventing a token charge. This zero-text path does not imply that the
underlying audio operation had zero provider cost.

The realtime Voice WebSocket route remains outside this runtime reservation
coverage: it currently emits no authoritative prompt/transcript token usage
record at session termination, so there is no honest terminal amount with
which to settle a token reservation. It requires a separate metering contract
before quota enforcement can be safely enabled.

These additions close the in-flight token-quota admission gap for the listed
routes, but they do **not** implement image/audio provider cost accounting or
monetary budgets. Other usage-bearing routes still require an
endpoint-by-endpoint coverage audit. Wallet-cents reservation/refund, tenant
quota inheritance, provider-verified usage reconciliation and multi-process
ledger transactions remain outside this slice. ApiKeyStore and BillingStore
remain the only actual charge authorities. C-06 is not complete until those
explicit gaps and the remaining production acceptance gates are resolved.
