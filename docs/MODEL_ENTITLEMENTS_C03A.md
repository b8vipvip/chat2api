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
- tenant_role/{owner|admin|member|viewer}: policy inherited from the actual
  membership role of the user who owns the explicitly bound API key.

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

- GET /v1/models and GET /v1/models/{model_id}: filter the existing live
  model catalog using the authenticated API-key context. The detail route
  requires the same `models` scope as the collection route and returns 404
  for models hidden by the caller's policy. Admin model inventory remains
  unfiltered. The legacy v13 detail handler authenticates manually, so it
  explicitly binds and restores the authenticated key context rather than
  relying on the main API dependency.
- GET /api/user/models: filter the production pricing catalog by its
  `model_id` field. Without `key_id`, apply global/user policy only; the
  browser session is not enough to infer one tenant when the user belongs to
  multiple workspaces. The Playground supplies its selected `key_id`; the
  server first verifies that the key belongs to the authenticated user, then
  applies the complete global/tenant/tenant-role/user/key intersection. A
  foreign or unowned key returns 404.
- The shared registry.resolve_client boundary rejects unauthorized requests
  with HTTP 403 before the existing Worker resolver.
- Request model context is owned by the existing routing adapters:
  - POST /v1/chat/completions uses the concrete model extracted from its request.
  - POST /v1/responses and POST /v1/completions use the existing v13 target
    context. Their legacy adapter authenticates outside the main FastAPI API-key
    dependency, so it now binds the same authenticated key around the direct
    Chat adapter call; the context is always restored after dispatch.
  - POST /v1/images/generations uses the server-owned gpt-image target. A
    client-supplied model field cannot change the entitlement target.
  - Responses `image_generation` delegates to that same Images executor.
    Because the adapter calls the route function directly, it explicitly binds
    the authenticated API key and server-owned `gpt-image` model context around
    the executor call; it does not inherit the outer Responses text model or
    rely on a FastAPI dependency that is not executed by a direct function call.
  - POST /v1/audio/speech and POST /v1/audio/conversations bind the model from
    their validated request body before the shared resolver runs.
  - WEBSOCKET /v1/audio/realtime binds the requested model from the first
    session.start frame; the requested gpt-live-mini alias is authorized before
    the existing gpt-live transport alias is applied. Authorization failures
    are returned as non-retryable model_not_entitled WebSocket errors.
- Text model aliases are normalized before entitlement decisions. Realtime
  Voice checks the requested public gpt-live-mini model before applying its
  transport-only gpt-live alias. Unknown or unresolved models are rejected
  whenever a matching policy applies.
- A failed UserAccountStore owner lookup, failed TenantMembershipStore lookup,
  a tenant-bound key without an active owner role, or a stale tenant binding
  whose recorded owner differs from the current UserAccountStore owner returns
  503. It is never treated as an unowned/unbound key and does not silently
  drop or transfer policy scope.
- Master server API key remains unrestricted for compatibility.
- Admin policy APIs use the existing administrator Session and same-origin
  ingress; mutations write to the existing AdminAuditStore.

API:
- GET /api/admin/model-entitlements
- PUT /api/admin/model-entitlements/{global|key|user|tenant|tenant_role}/{subject_id}
- DELETE /api/admin/model-entitlements/{global|key|user|tenant|tenant_role}/{subject_id}

## Explicit gaps

- Tenant policy is supported only through an explicit server-side
  API-key-to-tenant binding. The binding API is delivered in C-02a PR #815.
  The tenant policy dimension is not a substitute for resource-level tenant
  authorization: File, Conversation, History, Media and Billing ownership still
  require their own cross-tenant negative tests and explicit owner contracts.
- End-user system-role and subscription-plan policy dimensions are not
  implemented: no authoritative end-user role or subscription lifecycle owner
  exists in the current UserAccountStore/BillingStore contracts. Administrator
  RBAC roles are not customer entitlements and must not be reused as such.
- ModeResolverMiddleware marks every unmarked /v1 request as Mode 2; explicit
  `api`/`mode2` aliases remain Mode 2, while Mode 1 selection on /v1 is
  rejected with an entrypoint-conflict error. Mode 1 Cookie proxy is not an
  OpenAI API transport and remains NO-GO/not implemented, so it has no
  model-dispatch entitlement path to evaluate. A future separately approved
  Mode 1 transport must provide its own authenticated principal and model
  context before it can inherit any policy. The current Web Chat browser sends
  its selected API Key directly to Mode 2 /v1 endpoints; its cookie-authenticated
  recovery/cancel BFF only observes or signals an existing request and does not
  independently select a model or dispatch a new generation.
- No editable model metadata, aliases or fallback-routing configuration is added.
- This does not prove the model actually served; only existing Worker and
  telemetry evidence can do that.
- The current model-consumption inventory covers Chat Completions, Responses,
  legacy Completions, Images, Audio Speech/Conversations and the Realtime Voice
  WebSocket. The model-discovery inventory also includes both /v1/models and
  /v1/models/{model_id}. Embeddings are not currently exposed by the API. Any future
  model-consuming endpoint must set an authoritative model context and add
  pre-dispatch positive/negative tests before it is exposed to scoped API keys.
- Local SQLite is single-instance. Multi-instance transactional storage, backup
  and restore are D/P gates.
- Production composition is wired through app.entry:app by the single C-track
  integration owner in PR #824. That PR remains Draft pending the complete
  route-permission matrix, tenant-resource isolation, exact-head CI and
  production acceptance; module wiring is not equivalent to release approval.

## Tests

Covers default compatibility, alias normalization, global/key/user/tenant/role
intersection, deny precedence, non-concrete alias denial, production `model_id`
catalog filtering, selected-key catalog intersection and foreign-key rejection,
pre-dispatch 403, master-key compatibility, API session auth, subject
validation, audit and restart persistence. Additional tests cover identity and
tenant-store outages, orphaned tenant memberships, the server-owned gpt-image
target, Speech/Conversation request model context, Realtime session.start
model context including deny behavior, legacy v13 manual-auth key-context
binding for model detail and Completions, and Responses hosted image-tool
key/model context with a negative `gpt-image` entitlement regression. The legacy model detail route must
enforce the models scope and conceal policy-filtered model IDs; legacy
Completions must reject a denied model before Worker resolution. Exact-head full CI and production image
smoke remain required.
