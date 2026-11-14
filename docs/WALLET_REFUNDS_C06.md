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
  refund cap. Exact negative integer wallet balances remain valid under the
  documented debt policy.
- All payment-channel extensions that mutate the shared wallet order
  ledger (PayPal/USDT order creation, checkout metadata, USDT txid submission
  and administrator rejection) restore the prior in-memory row if persistence
  fails. V106 recharge amount boundaries also require strict integer cents.
- USDT duplicate-TXID detection and wallet credit are owned by
  `BillingStore.settle_usdt_order()` under one lock and one atomic ledger
  write. The V107 middleware check is only an early rejection; concurrent
  confirmations cannot both credit the same TRON transaction. Manual
  confirmation requires the order to be in `reviewing` state, while a retry
  of the already-paid same order remains idempotent. Rejection is limited to
  pending/reviewing orders and cannot downgrade a paid order or make its TXID
  reusable for another credit.
- The internal wallet ledger uses an in-process lock plus atomic
  file replacement under P-01's **single-process** runtime.
  Multi-worker or multi-host wallet transactions are not supported.
- Pricing configuration rejects non-finite exchange rates and model
  prices, validates persisted rates at startup, and restores the previous
  in-memory pricing state if a config write fails. Corrupt pricing data is
  never replaced silently with default rates while billing continues.
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
- Keep this PR Draft until exact-head CI, image smoke, SDK contract,
  admin review, stacked dependency resolution and production
  acceptance are explicitly complete.
