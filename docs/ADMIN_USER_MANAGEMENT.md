# Administrator User Management

Status: first control-plane vertical slice; multi-admin RBAC and centralized database migration remain future work.

## Admin access and CSRF

All administrator mutation endpoints under /api/admin/ (and /api/clients) require an authenticated administrator session and an exact same-origin browser mutation.

The server validates:

- Origin scheme against the request scheme, or the configured reverse-proxy X-Forwarded-Proto value.
- Origin host against the request Host.
- Origin path/query/fragment are empty (or the root slash only).
- Sec-Fetch-Site, when present, is same-origin.

The guard applies to POST, PUT, PATCH and DELETE, including administrator login and logout. GET requests remain read-only and do not require Origin.

## User management API

- GET /api/admin/users?limit=100&offset=0&q=... — safe user projection, total count, key count, active session count and wallet balance.
- PATCH /api/admin/users/{user_id} with {"enabled": false} — disable a user and revoke all of their browser sessions.
- PATCH /api/admin/users/{user_id} with {"enabled": true} — re-enable the account. Revoked sessions are not restored.
- POST /api/admin/users/{user_id}/sessions/revoke — revoke all current browser sessions.
- GET /api/admin/users/{user_id}/keys — list owned API key metadata only; never returns full key material.
- PATCH /api/admin/users/{user_id}/keys/{key_id} — update a key's name, enabled state and/or token quota. `token_quota: null` means unlimited; a revoked key cannot be modified.
- DELETE /api/admin/users/{user_id}/keys/{key_id} — permanently revoke the owned key, disable it and erase its recoverable encrypted secret. The key cannot be re-enabled.
- GET /api/admin/audit?limit=100 — read recent administrator mutation audit events.

When a user is disabled, their owned API keys are blocked at the /v1 request boundary even if wallet billing is disabled. The key records are retained; re-enabling the account makes still-enabled keys usable again. This is a suspension policy, not permanent key revocation.

## Audit log

Administrator user status, session and API-key lifecycle actions are written to data/admin_audit.jsonl before and after the state mutation. Records contain actor, action, target ID, request ID, timestamp and non-secret details. The log uses append-only application semantics, mode 0600, fsync and a SHA-256 hash chain; reads verify the full chain and fail closed on detectable corruption or record modification. This is tamper-evident integrity checking, not WORM storage or cryptographic proof against an actor with filesystem write access; external anchoring/retention and rotation remain future work.

Do not place passwords, session tokens, API key values, payment secrets or user request content in audit details.

## Current limitations

- There is one configured administrator identity; there are no additional administrator accounts, roles or fine-grained RBAC grants yet.
- The user directory uses the existing JSON account store and is intended for the current single-process deployment model. Thread locks and atomic file replacement are not cross-process transactions; a multi-worker or multi-instance database migration is a separate design and migration task.
- The UI supports user search, enable/disable, session revocation, safe API key metadata inspection, key enable/disable, quota updates and permanent revocation. It never exposes key plaintext. It does not create keys on behalf of users, rotate a key in place, edit scopes, reset passwords, edit user email, delete accounts or perform refunds.
- Plugin management is not part of this vertical slice.
