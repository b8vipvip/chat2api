# C-04a — invite-only registration

Status: **Draft / partial**. This is a server-side invitation service stacked on C-01 (#801). It does not change the production composition root; a later single C integration PR must install `install_registration_invitations` after the existing user-console and admin-session installers.

## Implemented

- `InvitationStore` persists only SHA256 hashes of high-entropy one-time codes in a private SQLite file.
- Invitations are bound to a normalized email address and have a bounded 5-minute to 30-day expiry.
- Atomic `BEGIN IMMEDIATE` reservation prevents concurrent reuse. Registration consumes the invitation after account/session creation succeeds; validation or account-creation failure releases the reservation.
- An interrupted registration leaves a `reserved` invitation unavailable for reuse. This deliberately fails closed; the admin can revoke/inspect and issue a replacement rather than silently reopening a possibly-used code.
- Admin APIs:
  - `GET /api/admin/control/invitations` returns bounded metadata only.
  - `POST /api/admin/control/invitations` issues a code once and returns it only in that response.
  - `POST /api/admin/control/invitations/{id}/revoke` revokes an unreserved, unconsumed invitation.
- Admin mutations require same-origin and are audited before the state change. C-01 route permissions map invitation read/write to `users:read` / `users:write`.
- Public registration policy now supports `open`, `closed`, and `invite_only`. The existing `/api/user/register` endpoint remains the single registration owner.
- User Console registration reads the public policy and displays the invite-code field only for invite-only mode. The server remains authoritative if the public config cannot load; the one-time code is cleared from the DOM after successful authentication and on logout/session expiry.
- Correct email whitespace validation and deterministic newest-first ordering for records created in the same second.
- Admin invitation responses and successful registration/session responses carry `Cache-Control: no-store`.
- Public announcement text is rendered through DOM `textContent`, not HTML injection.
- Every delegated administrator role has an explicit self-password permission and a current-password-verified rotation endpoint. Passwords are PBKDF2-rehashed and all existing sessions for that administrator are immediately revoked. Root password remains environment-managed; root can reset delegated administrator passwords through the admin API.

## Security / failure semantics

- Raw invitation codes and token hashes never appear in list responses or audit details.
- Codes are high entropy, ASCII-only, email-bound and single-use. A code for another email is rejected without reserving it.
- A concurrent second registration cannot reserve the same code.
- A reserved code cannot be revoked mid-registration; it can be consumed by the already-started request.
- A crash after reservation but before finalization leaves the code reserved and unavailable. No automatic TTL release is performed because the account may already have been created. Reissue a new invitation after checking the user directory.
- Email addresses are stored in the existing local-data security model; this SQLite file is mode 0600 but is not field-encrypted. G0/DataKeyring remains deferred.

## Not yet complete

- This PR is stacked on #801 and is not production-wired until the central C composition-root PR installs the invitation service.
- C-07 invitation issue/list/revoke controls and the `invite_only` selector are implemented on separate Draft PR #820. They are not yet wired to this service because both PRs await the central C composition-root integration.
- Root administrator password remains environment-managed; delegated admin password rotation and MFA are separate pending controls.
- Real browser/mobile E2E, production data backup/restore and multi-instance database support remain release gates.
