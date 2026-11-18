# C-04 — Invite-only registration and invitation lifecycle

Status: **Production-integrated; lifecycle correction under review in PR #823.**

The invitation service, registration policy, admin API and existing Admin Console composition were integrated into the production entry through PR #824. This document describes the current mainline behavior; PR #823 adds a bounded expiry/revocation race correction and is not considered merged until its exact-head CI and review gates complete.

## Implemented behavior

- Invitation codes are high-entropy, one-time values generated with `secrets.token_urlsafe(32)`. Only SHA-256 token hashes are persisted; raw codes are returned once in the issue response.
- Each invitation is bound to a normalized email address. Email comparison is case-insensitive after trimming and case-folding.
- Expiry is bounded from 5 minutes to 30 days.
- Reservation uses SQLite `BEGIN IMMEDIATE` so concurrent registrations cannot reserve the same code.
- A registration consumes its invitation only after account creation succeeds. Failed account creation releases an active reservation; an interrupted reservation fails closed and is not automatically made reusable.
- Reservation leases expire after 30 minutes. An expired reservation becomes unavailable; an administrator must inspect the user directory and issue a replacement rather than silently reusing a potentially consumed code.
- An unreserved, unconsumed invitation can be revoked. A reservation cannot be revoked mid-registration.
- Registration policy supports `open`, `closed`, and `invite_only`; the existing `/api/user/register` route remains the registration owner.
- The User Console reads public registration policy, displays the invite-code field only in invite-only mode, and clears the one-time code on authentication transitions.
- Admin invitation listing returns bounded metadata only. Raw invitation codes, token hashes and reservation IDs are not exposed in list responses.
- Invitation admin mutations pass the existing administrator session and same-origin request guard. A request audit event is written before mutation; completion is audited after mutation. If the pre-mutation audit is unavailable, the mutation is denied. If completion audit fails after commit, the API reports the committed result with `X-Admin-Audit-Status: completion-pending` rather than falsely reporting that the mutation did not happen.
- Invitation issue/list responses and successful registration/session responses use `Cache-Control: no-store`.
- Public announcement text uses DOM `textContent`, not HTML injection.
- Delegated administrator password rotation verifies the current password, rehashes the new password and revokes that administrator's existing sessions. Root credentials remain environment-managed.

## Invitation state transitions

| From | Operation | To | Notes |
| --- | --- | --- | --- |
| `issued` | valid email-bound reservation | `reserved` | Atomic; only one concurrent request wins |
| `reserved` | registration succeeds | `consumed` | One-time code cannot be reused |
| `reserved` | registration fails and reservation is still active | `issued` | Explicit release for recoverable failures |
| `reserved` | reservation lease expires | `expired` | Fail closed; never silently reissue |
| `issued` | invitation expiry passes | `expired` | Expiry is authoritative |
| `issued` | administrator revokes | `revoked` | Reserved/consumed invitations are not revoked |

PR #823 corrects the boundary where an invitation had passed `expires_at` but no list/reserve request had yet materialized the `expired` status. Revocation now first materializes expiry and only revokes invitations whose expiry is still in the future. It samples the clock after SQLite grants the write transaction, preventing lock wait from using a stale pre-transaction timestamp. The regression tests cover both an already-expired invitation and expiry occurring at the transaction boundary.

## Verification already present in the repository

The invitation regression suite covers:
- email normalization and invalid email rejection;
- hash-only storage, email binding, one-time use and replay rejection;
- concurrent reservation;
- reservation lease expiry and fail-closed recovery;
- release, revoke and expiry state behavior;
- invite-only, closed and open registration modes;
- invitation consumption only after successful registration;
- duplicate-account failure and reservation release;
- delegated admin permission boundaries and password rotation;
- request/completion audit events and audit failure semantics;
- one-time code clearing in the User Console.

The central C-track production integration was merged through PR #824. PR #823 is limited to the invitation store, its regression tests and this documentation; no separate service or registration route is introduced.

## Remaining acceptance gates

Keep Issue #809 open until the following are verified and documented:
- complete API/browser negative tests for CSRF, cross-Origin and session revocation across the invitation routes;
- mobile viewport and accessibility review of the existing Admin Console/User Console flows;
- full operator acceptance of create, list, revoke, expiry, email binding, replay and interrupted-registration recovery;
- login failure throttling and password-rotation acceptance across all applicable admin roles;
- approved email-address retention/encryption policy. The invitation database is mode `0600`, but email addresses are not field-encrypted; G0/DataKeyring remains deferred;
- production backup/restore and multi-instance SQLite topology. The current local SQLite store is not a claim of shared multi-host transactional support.

Do not mark the full C-04 issue complete based solely on PR merge or green CI. Production integration, automated code acceptance, mobile/operator review and the deferred data-protection gate are separate states.
