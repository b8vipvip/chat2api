# C-03a — model entitlement policy foundation

Status: **partial / Draft**. This is an authorization adapter, not a replacement
for Worker model discovery, availability, routing or served-model evidence.

## Current owner boundaries

- Existing ClientRegistry.model_catalog and live Worker picker/capability evidence
  remain the availability and model-discovery authorities.
- Existing model_capability_routing_patch remains the Worker selection authority.
- Existing model_evidence / telemetry remains the requested-vs-served model
  provenance authority.
- Existing ApiKeyStore.authenticate + v17 routing context identifies the
  authenticated API key; UserAccountStore.owner_for_key resolves user ownership.
- This module owns only entitlement policy and the allow/deny decision.

## Implemented policy scope

Supported subjects in this slice:

- global/default: global model allowlist or denylist.
- key/{key_id}: API-key-specific policy.
- user/{user_id}: policy inherited by every API key owned by that user.
- tenant/{tenant_id}: policy inherited only by API keys explicitly bound to that
  tenant through TenantMembershipStore.tenant_for_key.

A request is allowed only if:
1. no matching deny rule contains the normalized requested model; and
2. every matching allow rule contains that model.

Therefore global, key and user allowlists intersect; any explicit deny overrides
all allow rules. With no matching policy, behavior remains backward-compatible
(default allow).

Model aliases are canonicalized by the existing normalize_model_id. When
any policy applies, non-concrete aliases such as default, chatgpt-web and
auto are denied because the concrete model cannot be proven before dispatch.
This prevents a broad alias from bypassing a concrete allowlist.

## Enforcement

- GET /v1/models: filter the existing live model catalog using the current
  authenticated API-key context. Global, tenant, user and key policies apply.
  Admin model inventory remains unfiltered.
- GET /api/user/models: filter the user-console pricing/model list using the
  authenticated user plus global/user policy. A browser user session is not
  enough to infer one tenant when the user belongs to multiple workspaces.
- Existing registry.resolve_client boundary: reject unauthorized model
  requests with HTTP 403 before invoking the existing Worker resolver.
- Master server API key remains unrestricted for compatibility.
- Admin policy APIs use the existing administrator Session and same-origin
  ingress; mutations write to the existing AdminAuditStore.

API:
- GET /api/admin/model-entitlements
- PUT /api/admin/model-entitlements/{global|key|user}/{subject_id}
- DELETE /api/admin/model-entitlements/{global|key|user}/{subject_id}

## Explicit gaps

- Tenant policy is now supported only through an explicit server-side
  API-key-to-tenant binding. The binding API is delivered independently in
  C-02a PR #815. Role, subscription-plan and Mode 1/Mode 2 policy dimensions
  are not implemented here; membership count or browser-provided tenant headers
  are never used as identity.
- No editable model metadata, aliases or fallback-routing configuration is added.
- This does not prove the model actually served; only existing Worker and
  telemetry evidence can do that.
- Enforcement is attached to the shared existing resolver and model catalog.
  Any future dispatch path that bypasses that resolver must be covered before
  it is exposed to scoped API keys.
- Local SQLite is single-instance. Multi-instance transactional storage, backup
  and restore are D/P gates.
- The module is not wired into app.entry:app in this PR. Per #784, all
  production installer wiring will be owned by one C-track integration PR
  after independent module reviews; this avoids concurrent edits to the
  production composition root.

## Tests

Covers default compatibility, alias normalization, global/key/user intersection,
deny precedence, non-concrete alias denial, catalog filtering, pre-dispatch
403, master-key compatibility, API session auth, subject validation, audit and
restart persistence. Full CI and production image smoke remain required.
