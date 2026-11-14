# C-track production entry integration

Status: **Draft / integration slice only**.

This change provides one composition owner for the independent C-track services.
It does not replace their individual service/store owners and does not convert
partial modules into production-complete features.

## Production installation order

1. install_control_plane_authority_v1(app) runs after the base app is created
   but **before install_v17_patch(app)**. It creates the C-01 policy store and
   routes so the existing v17 administrator-session middleware can enforce
   the policy for those routes.
2. The existing v17 installer establishes the shared administrator session,
   same-origin/CSRF and server-side route authorization boundary.
3. The existing user-console installer creates the authoritative
   UserAccountStore; existing API-key and Worker/model-routing owners remain
   unchanged.
4. install_control_plane_features_v1(app) runs after the final model-routing
   and legacy alias installers, and before the final SSE transport middleware.
   It installs invitations, tenant membership, model entitlements, the inert
   plugin registry, quota journal admin APIs and the C-07 UI asset in dependency
   order.
5. The existing admin HTML remains the single console page owner. The new C-07
   JavaScript is served from one no-store asset route.

## Deliberately disabled or incomplete capabilities

- **C-02:** membership and key-binding APIs are installed, but this is not yet
  tenant-aware authorization across every Chat, File, Conversation, Request
  History, Media and Billing resource. Cross-tenant resource isolation remains
  a release blocker.
- **C-03:** entitlement evaluation is attached to the existing model catalog
  and resolver, but complete coverage of every model-consuming endpoint,
  subscription/system-role policy and real-request matrix remains open.
- **C-05:** registry is metadata-only. Plugin execution and third-party secrets
  remain disabled; this integration adds no executor or sandbox.
- **C-06:** reservation journal and admin reconciliation are installed for
  inspection only. quota_reservation_runtime_enabled must remain false until
  request admission, Broker terminal commit/release, crash recovery and billing
  reconciliation are integrated and proven.
- **G0 / DataKeyring:** remains deferred by explicit user decision. This change
  does not migrate production secrets or claim encrypted persistent storage.
- **Mode 1:** remains NO-GO. No Cookie/Session collection, replay or private
  webpage API proxy is introduced.
- **P-01:** remains single-process/single-instance. The runtime lock does not
  create distributed Broker, Worker or database semantics.

## Automated integration coverage

tests/test_control_plane_production_entry_integration.py imports the actual
app.entry:app in an isolated subprocess and verifies:

- all C-track stores are installed and the integration is marked;
- expected C-01 through C-07 route/method pairs have exactly one owner;
- the C-07 asset is referenced by the existing admin HTML;
- unauthenticated reads of admin control-plane routes return 401;
- public plugin discovery remains empty and execution-disabled; and
- quota reservation runtime hooks remain disabled.

## Remaining release gates

This integration test is not a substitute for browser E2E, a complete delegated
admin permission matrix, cross-tenant negative tests over every resource type,
real API model-entitlement coverage, quota lifecycle/ledger reconciliation,
production-image smoke, backup/restore rehearsal or deployment/rollback proof.
The PR must remain Draft until those independent gates are complete.
