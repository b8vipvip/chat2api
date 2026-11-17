# C track control plane — first delivery slice

Status: **partial / draft**, tracking #784 in PR #801. This describes actual code boundaries, not completion of the full C track.

## Single owners and compatibility

Administrator identity remains with the existing v17 AdminSessionStore and browser Session cookie. Administrator roles and client flags belong to the new ControlPlaneStore, installed once before v17. The hash-chained AdminAuditStore remains the sole administrator audit writer, shared with the user-commerce installer. Worker model evidence/dispatch remains with the existing routing layer; token quotas remain with ApiKeyStore; user charges remain with BillingStore. The Mode 2 API default and Mode 1 NO-GO gate are unchanged.

## Built-in administrator roles

Super Admin, System Admin, User Admin, API Key Admin, Billing Admin, Plugin Admin, Support. The configured root administrator is a virtual Super Admin, not copied into a second password store. Delegated admins have unique 3–64 character ASCII usernames and 12–256 character passwords. Passwords are hashed with per-account 128-bit random salt and 310000 PBKDF2-HMAC-SHA256 iterations. Admin login is the existing POST /api/admin/auth/login. Disabled accounts lose their existing sessions; role changes apply to subsequent requests immediately. Login attempts are limited to 5 failures per username and 20 per source IP in a 15-minute window; the identity budget is shared across source IPs. The limiter is process-local and requires a shared store before multi-instance deployment.

The existing v17 same-origin mutation guard and Session authentication gate apply to **all** admin endpoints. The reviewed route matrix includes explicit tenant membership/key binding (managed only by system/root administrators until tenant-scoped admin identities exist), global/tenant/tenant-role/user/API-Key model policy, plugin manifest and quota reservation routes. API Key secret reveal, legacy payment-provider secret writes and raw request detail remain root-only; delegated user listings do not include wallet balances. Billing Admin can read redacted legacy payment settings but cannot change provider secrets until G0 DataKeyring migration and rotation/rollback evidence are accepted. Permission decisions are evaluated at server ingress, not just in the UI. Unknown admin routes are denied to delegated roles until classified. The legacy secret-reveal API remains root-only while the G0 key migration is deferred.

## Client API and enforced policy

| Endpoint | Function |
| --- | --- |
| GET /api/admin/control/roles | Built-in role catalogue |
| GET /api/admin/control/admins | Root metadata and delegated administrator list (no hashes) |
| POST /api/admin/control/admins | Create delegated administrator |
| PATCH /api/admin/control/admins/{username} | Change role, enable or disable |
| GET/PUT /api/admin/control/client | View or update policy |
| GET /api/client/config | Public non-secret flags |

Client policy defaults: registration_mode=open, web_chat_enabled=true, announcement empty. Only open and closed registration are implemented; an invite registration mode is not accepted yet. Closed registration rejects POST /api/user/register. Disabling Web Chat rejects GET /chat but does not disable standard /v1 APIs. Announcement is plain text and must be rendered as text, not HTML. Both administrator creation and policy changes require audit writes.

## Persistence and acceptance limits

The new single-instance JSON store uses atomic replace, 0600 permissions and strict schema validation. Bad/missing keys, corrupt JSON and unsupported schema are not silently reset. It is **not multi-process or multi-instance transactional storage**. All production deployments must set a strong root admin password; administrator MFA, password rotation, browser E2E and storage concurrency remain follow-ups. The quota reservation admin API is inspection/recovery only until request admission and terminal hooks are integrated. The PR requires a completed full CI and production image smoke before review or merge.

## Parallel remaining C packages

- C-02 tenants/workspaces and user/key membership, with read/write isolation; never infer tenant from a Worker identity.
- C-03 model entitlements: user/role/tenant/key allowlists enforced at admission, using existing Worker/model authority.
- C-04 client settings UI, invitation workflow, console announcements and login hardening.
- C-05 plugin registry, signed manifest, tool-level authorization, secrets and audit; do not enable executors without sandbox verification.
- C-06 quota policy and idempotent accounting over existing API Key and BillingStore authorities; multi-instance ledger transactions belong to D/P.
- C-07 admin UI integration, redaction and least-privilege full-route coverage.

G0 DataKeyring production migration remains a separate gate; this slice does not approve or execute that migration.
