# C-06 internal wallet charge corrections (Draft)

This is an **internal Chat2API wallet charge reversal**, not a ZPAY,
USDT, Alipay, WeChat or upstream provider payment refund. It cannot
reverse a third-party payment transaction or assert provider usage.

## Contract

`POST /api/admin/billing/charges/{request_id}/refund`

Requires a valid administrator session and same-origin CSRF policy;
the Control Plane maps this route to `billing:write`. Caller body:

```json
{
  "refund_id": "rf_example123456",
  "amount_cents": 5,
  "reason": "Verified billing correction"
}
```

- `request_id` must already have a completed, **debited** charge.
- `refund_id` is a caller-generated idempotency key. Repeating the
  same ID, request, amount and reason returns the existing result.
  Reusing it with different values fails; callers must never create
  a new refund ID merely because a successful response was lost.
- Only positive integer cents are accepted. Total refunds on one
  request cannot exceed its original wallet debit. Partial corrections
  are allowed.
- The request's charged `user_id` is authoritative and cannot be
  overridden by the administrator's request.
- The original `charges` row remains immutable. Corrections are
  appended by `refund_id` to `BillingStore.refunds`, and the wallet
  is credited in the same in-process locked atomic JSON save.
- On a failed wallet JSON write, the new refund row and in-memory
  wallet credit are reverted. After a failure, retry with the **same**
  `refund_id`.
- The admin audit chain records a requested and a completed event.
  A pre-audit failure denies the refund; a post-audit failure can leave
  a successfully persisted refund, so the response instructs the
  operator to retry **only the same refund ID**.
- User `/api/user/billing` exposes refund rows scoped to the session
  owner's account, aggregate `refunded_cents` and
  `net_debited_spend_cents`. The existing estimated cumulative spend
  field is retained separately and is not rewritten.

## Explicit operational gates

- This is not an automatic refund on failed API requests: failures
  release *uncommitted admission holds*. Posted charges need an
  explicit audited administrative correction.
- Startup recovery enumerates durable active wallet holds and looks up
  their exact request IDs in the persisted telemetry tail before serving
  requests. A missing/unreadable terminal record leaves the hold reserved;
  it is never released from absence of evidence.
- A completed status alone is not sufficient to settle a reserved
  wallet hold: input, output and total usage must be non-negative integral
  values, total must equal input plus output, and cached input must be a
  bounded subset. Missing or inconsistent usage leaves the hold reserved
  for later terminal replay.
- Each priced admission snapshots the complete model price catalog and the
  USD/CNY rate. Settlement selects the actual served model from that immutable
  catalog; if it was not present at admission, it falls back to the
  requested-model snapshot. It never reads a changed live price or FX rate
  for a request that already holds a reservation. Charge rows keep both
  `model_id` (served model) and `priced_model_id` (price source). Positive-rate
  requests reserve at least one cent using upward cent rounding, so tiny
  estimates still retain their admission snapshot.
- Requests admitted while wallet billing is disabled, or against an explicit
  enabled zero-rate model, persist a zero-value terminal admission marker.
  A later billing toggle or price edit cannot retroactively turn those
  completed requests into debits; the marker also prevents request-ID reuse.
  Conversely, a positive reservation proves billing was enabled at admission:
  disabling billing while the Worker is running affects new requests only and
  cannot waive settlement of an already accepted request.
- Startup validates the supported v1 ledger schema, collection/row
  structure, required charge/order/reservation/refund fields, owner IDs,
  row-key identity, integer-unit fields, finite exchange rates and
  money-moving booleans/enums. In particular, a `reserved` row without a
  positive `reserved_cny_cents` amount is rejected instead of silently
  disappearing from available-balance calculations; missing charge costs,
  recharge amounts or refund amounts also fail closed. Charge debit flags must be actual booleans;
  payment order states/channels and wallet reservation states must be known
  values. Corrupt truthy strings cannot be treated as paid/debited, and an
  unknown reservation state cannot silently disappear from available-balance
  calculations.
  Unknown schema versions, floating-point, boolean and string balances/
  amounts fail closed; the existing ledger bytes are preserved for offline
  recovery. Refund rows must reference a debited charge owned by the same
  user, match its original charge amount, and remain within the cumulative
  refund cap.
- Admission holds are estimates, not hard spending caps. Final settlement
  debits the full cost computed from the immutable admission price/FX snapshot
  and terminal usage, even when actual usage exceeds the hold. This may create
  an exact negative integer wallet balance; that balance is debt, not spendable
  credit. New paid admissions remain blocked until a top-up covers the debt
  and the next request's hold.
- All payment-channel extensions that mutate the shared wallet order
  ledger (PayPal/USDT order creation, checkout metadata, USDT txid submission
  and administrator rejection) restore the prior in-memory row if persistence
  fails. V106 recharge amount boundaries also require strict integer cents.
  Generic provider settlement accepts only pending/reviewing orders, requires
  a non-empty provider transaction number, and enforces one paid transaction
  per provider namespace under the ledger lock. Alipay and WeChat share the
  ZPAY namespace; PayPal and USDT are separate. A same-order callback retry
  with the same provider ID is idempotent, while a different ID or a transaction
  already credited to another order is rejected. Startup rejects duplicate
  paid provider transaction IDs already present in the persisted ledger.
- USDT duplicate-TXID detection and wallet credit are owned by
  `BillingStore.settle_usdt_order()` under one lock and one atomic ledger
  write. The V107 middleware check is only an early rejection; concurrent
  confirmations cannot both credit the same TRON transaction. Manual
  confirmation requires the order to be in `reviewing` state, while a retry
  of the already-paid same order remains idempotent. Rejection is limited to
  pending/reviewing orders and cannot downgrade a paid order or make its TXID
  reusable for another credit.
- Merchant callback identifiers are allocated while holding the wallet
  ledger lock and are unique across all payment channels. A random collision
  is retried before the order is persisted; startup rejects duplicate or
  malformed merchant trade numbers, and callback lookup fails closed if an
  in-memory ambiguity is detected.
- A user may submit a USDT TXID only while the order is `pending`. The first
  accepted submission atomically moves it to `reviewing` and freezes that
  TXID while an administrator verifies the proof. A user cannot replace a
  hash during review or reopen a rejected, failed, cancelled or expired
  order. The checkout UI disables the input and submit button after success;
  a rejected order requires a new recharge order and a new TXID.
- The internal wallet ledger uses an in-process lock plus atomic
  file replacement under P-01's **single-process** runtime.
  Multi-worker or multi-host wallet transactions are not supported.
- Pricing configuration rejects non-finite exchange rates and model
  prices, validates persisted rates at startup, and restores the previous
  in-memory pricing state if a config write fails. Corrupt pricing data is
  never replaced silently with default rates while billing continues.
  Disabled price rows cannot admit new paid requests; intentional free use
  requires an enabled, explicit zero-rate row.
- When wallet billing is enabled for a managed user, every dispatched
  model must have an explicit pricing row. An unpriced model is rejected
  before Worker dispatch; administrators may add an explicit zero-rate row
  only when free use is intentional. Disabling wallet billing preserves the
  existing unmetered-wallet behavior.
- Image generation and realtime Voice are not token-metered by the current
  Worker protocol. Positive text-token prices for `gpt-image`, `gpt-live`
  or `gpt-live-mini` are rejected while wallet billing is enabled; an
  explicit all-zero row is the only opt-in to intentionally free use until
  provider-verified image/audio metering is implemented.
- This does not solve payment provider callbacks, chargebacks,
  payment disputes, cash payouts, tax or reconciled provider usage.
- G0 DataKeyring migration remains deferred. Payment-channel V106
  credentials therefore keep their existing V106 cipher until an approved
  backup/readback/rollback migration exists. V106 pins the selected legacy
  payment key in a local 0600 `.chat2api_data_secret` file and fails startup
  if existing ZPAY/PayPal ciphertext cannot be decrypted; V107 must not
  silently replace that cipher with `data_secret.key`. Malformed payment
  configuration also fails closed, and admin config updates roll back their
  in-memory state if persistence fails. This is a narrow compatibility
  safeguard, not a DataKeyring migration or a claim that the legacy payment
  key has been rotated. Do not infer encrypted-at-rest wallet records or
  production migration approval.
- The separate legacy V104 ZPAY configuration (`user_payment_config.json`)
  also validates its persisted schema and decrypts any existing ciphertext at
  startup. Malformed JSON or a mismatched data key fails closed without
  overwriting the source file, and failed config writes restore the previous
  in-memory state. This path continues to use its existing `data_secret`
  cipher; it is not a DataKeyring migration or key rotation.
- Keep this PR Draft until exact-head CI, image smoke, SDK contract,
  admin review, stacked dependency resolution and production
  acceptance are explicitly complete.
