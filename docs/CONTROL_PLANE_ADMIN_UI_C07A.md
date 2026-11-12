# C-07a — single-owner management console views

Status: **partial / Draft**. The new views extend the existing app/admin.py
console. This PR does not create a second admin HTML renderer or another
request-history table owner.

## Added views

- Administrator RBAC: list/create delegated admins, change roles and enable/
  disable accounts. Password is sent only at creation and never rendered again.
- Client policy: open/closed registration, Web Chat enable flag and plain-text
  announcement.
- Tenants: create/list workspaces, manage membership roles, bind/unbind
  user-owned API keys. Key binding requires an existing UserAccountStore owner
  and an existing membership.
- Model entitlements: global, tenant, user and API-key allow/deny policies.
- Product plugin registry: manifest registration and read-only metadata
  inspection. UI always shows untrusted / execution disabled.

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
- app/admin_control_plane_ui_v1_patch.py serves the cache-disabled JS asset.
- This PR does not modify app.entry:app. One central C integration PR must
  install the asset route and the C service installers together after module
  reviews, per #784. Until that integration PR lands, the HTML references an
  asset route not yet installed in production.
- The quota reservation PR is only a service module and has no admin query API
  yet; no quota dashboard is shown as if it were functional.

## Acceptance gaps

No visual browser/e2e test, mobile layout review, password rotation/MFA
workflow, cross-resource tenant authorization, or billing ledger UI is claimed
complete. The views remain Draft until CI, production image smoke, browser
verification, central entrypoint integration and security review.
