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


## Integration contract (not yet installed)

A future single C integration owner must:
1. Authenticate the API key through the existing ApiKeyStore.
2. Estimate request input plus a defensible maximum output reservation before
   Worker dispatch; never trust a client-supplied estimate.
3. Call reserve_for_key before dispatch; reject over-quota requests.
4. At terminal finality, call commit_completed_usage for completed requests,
   and release only failed/cancelled/unsubmitted reservations.
5. Reconcile committing reservations on startup/recovery before accepting
   additional scoped traffic.

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
