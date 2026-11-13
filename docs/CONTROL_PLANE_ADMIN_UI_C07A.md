# C-07a — single-owner management console views

Status: **partial / Draft**. The new views extend the existing app/admin.py
console. This PR does not create a second admin HTML renderer or another
request-history table owner.

## Added views

- Administrator RBAC: list/create delegated admins, change roles and enable/
  disable accounts. Password is sent only at creation and never rendered again.
- User accounts and billing/pricing/payment configuration reuse the existing
  admin_user_commerce_v104.js view owner, now role-gated for user_admin,
  support/system read-only, and billing_admin. Delegated user listings redact
  wallet balances and audit events remain root-only. Billing Admin can edit
  pricing and view redacted payment settings, but legacy provider-secret writes
  remain root-only until the G0 DataKeyring gate is accepted.
- API Keys: the new C API-key view lists/creates/updates quota/disables/revokes;
  a newly created token is displayed once and historical secret reveal remains
  root-only. The user-commerce key subview remains the existing owner for
  per-user key operations.
- Client policy: open/invite_only/closed registration, Web Chat enable flag and plain-text
  announcement.
- Registration invitations: role-aware issue/list/revoke controls with bounded
  24-hour/7-day/30-day expiry. The one-time code is displayed only in the create
  response and can be copied; list/history never returns the code. `user_admin`
  can issue/revoke, while read-only roles can inspect metadata.
- Tenants: create/list workspaces, manage membership roles, bind/unbind
  user-owned API keys. Cross-tenant administration is system_admin/super_admin
  only until tenant-scoped delegated console identities are implemented. Key binding requires an existing UserAccountStore owner
  and an existing membership.
- Model entitlements: global, tenant, tenant membership role (owner/admin/member/viewer), user and API-key allow/deny policies.
- Product plugin registry: manifest registration and read-only metadata
  inspection. UI always shows untrusted / execution disabled.
- Quota reservation journal: bounded read-only reservation list and confirmed,
  audited reconciliation action. The UI displays runtime integration status
  and does not claim quota enforcement is active before lifecycle hooks land.

## Authentication and authorization

The existing master API-key UI remains for legacy super-admin pages. The new C
views use the existing administrator login endpoint and HttpOnly Session
cookie through same-origin fetch. Password input is cleared after login.
No administrator password or API key is written to localStorage/sessionStorage
by this feature asset.

Navigation is role-aware for usability. It is not an authorization boundary:
all reads/writes still depend on the existing v17 server Session/Origin gate
and the C-track route permission matrix. The root API key and session remain
separate authentication paths for legacy and C APIs.

User-supplied labels, IDs, announcements and plugin metadata are rendered via
textContent, not raw HTML. The UI does not display secrets or enable plugin
execution.

## Integration contract

- app/admin.py remains the one HTML composition owner.
- app/admin_control_plane_admin_v1.js owns only the new C views.
- app/admin_user_commerce_v104.js remains the existing single owner for user,
  billing and payment views; this PR adds role-aware access and action controls.
- app/admin_control_plane_ui_v1_patch.py serves the cache-disabled JS asset.
- This PR does not modify app.entry:app. One central C integration PR must
  install the asset route and the C service installers together after module
  reviews, per #784. Until that integration PR lands, the HTML references an
  asset route not yet installed in production.
- The quota reservation PR exposes bounded admin inspection and audited
  reconciliation APIs, but it does not enforce request admission/terminal
  quota accounting yet. The UI displays that runtime integration status.

## Acceptance gaps

No visual browser/e2e test, mobile layout review, MFA workflow,
cross-resource tenant authorization, or billing ledger UI is claimed complete.
The invitation view is present in this branch but cannot operate in production
until C-04 and the central C composition root are integrated. The views remain Draft until CI, production image smoke, browser
verification, central entrypoint integration and security review.
