# C-02 workspace member invitations

This is a membership workflow only. It does not grant access to another member's private files, chat history, billing, media, generated files or native content. Those resources remain owner/key-scoped.

## Admin workflow

- `POST /api/admin/tenants/{tenant_id}/invitations` issues a one-time invitation bound to a normalized email address and one workspace role.
- The API returns the raw invitation code exactly once. The SQLite store persists only its SHA-256 hash; the audit log records only an email hash and non-secret metadata.
- Share the code with the invited person out of band. Automated email delivery is not configured by this slice.
- `GET /api/admin/tenants/{tenant_id}/invitations` lists metadata only.
- `DELETE /api/admin/tenants/{tenant_id}/invitations/{invitation_id}` revokes an unused invitation. Expired, accepted and revoked invitations cannot be reused.

Expiry is bounded from 5 minutes to 30 days. Issuing another active invitation for the same normalized email in the same workspace is rejected.

## User workflow

An authenticated user submits the code to `POST /api/user/tenants/invitations/accept`. Acceptance requires:
- an active user session;
- a same-origin browser request;
- exact case-insensitive email match with the account;
- an unused, unrevoked, unexpired invitation.

Membership insertion and invitation consumption occur in one SQLite `BEGIN IMMEDIATE` transaction. A code is single-use; concurrent/replayed accepts cannot add multiple memberships.

## Limits

The code is delivered out of band; no email provider or background mail queue is introduced. The SQLite store remains a single-instance local store. Multi-process/multi-host writes and backup/restore remain under the separate D/P gate. This feature does not implement tenant-shared resource ACLs or default single-tenant migration.
