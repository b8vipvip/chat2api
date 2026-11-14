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

- GET /v1/models: filter the existing live model catalog using the current
  authenticated API-key context. Global, tenant, tenant-role, user and key policies apply.
  Admin model inventory remains unfiltered.
- GET /api/user/models: filter the user-console pricing/model list using the
  authenticated user plus global/user policy. A browser user session is not
  enough to infer one tenant when the user belongs to multiple workspaces.
- The shared registry.resolve_client boundary rejects unauthorized requests
  with HTTP 403 before the existing Worker resolver.
- Request model context is owned by the existing routing adapters:
  - POST /v1/chat/completions uses the concrete model extracted from its request.
  - POST /v1/responses and POST /v1/completions use the existing v13 target context.
  - POST /v1/images/generations uses the server-owned gpt-image target. A
    client-supplied model field cannot change the entitlement target.
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
  or tenant-bound key without an active owner role returns 503. It is never
  treated as an unowned/unbound key and does not silently drop policy scope.
- Master server API key remains unrestricted for compatibility.
- Admin policy APIs use the existing administrator Session and same-origin
  ingress; mutations write to the existing AdminAuditStore.

API:
- GET /api/admin/model-entitlements
- PUT /api/admin/model-entitlements/{global|key|user}/{subject_id}
- DELETE /api/admin/model-entitlements/{global|key|user}/{subject_id}

## Explicit gaps

- Tenant policy is supported only through an explicit server-side
  API-key-to-tenant binding. The binding API is delivered in C-02a PR #815.
  The tenant policy dimension is not a substitute for resource-level tenant
  authorization: File, Conversation, History, Media and Billing ownership still
  require their own cross-tenant negative tests and explicit owner contracts.
- Role, subscription-plan and Mode 1/Mode 2 policy dimensions beyond the
  implemented tenant membership role are not implemented here; membership
  count or browser-provided tenant headers are never used as identity.
- No editable model metadata, aliases or fallback-routing configuration is added.
- This does not prove the model actually served; only existing Worker and
  telemetry evidence can do that.
- The current model-consumption inventory covers Chat Completions, Responses,
  legacy Completions, Images, Audio Speech/Conversations and the Realtime Voice
  WebSocket. Embeddings are not currently exposed by the API. Any future
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
intersection, deny precedence, non-concrete alias denial, catalog filtering,
pre-dispatch 403, master-key compatibility, API session auth, subject
validation, audit and restart persistence. Additional tests cover identity and
tenant-store outages, orphaned tenant memberships, the server-owned gpt-image
target, Speech/Conversation request model context, and Realtime session.start
model context including deny behavior. Exact-head full CI and production image
smoke remain required.


## C-03B — tenant/resource authorization audit (2026-11-13)

Status: **implementation and regression coverage added on a stacked branch; exact-head CI and review pending**.

### Authorization invariant

Tenant membership and API-key tenant binding are organizational metadata. They do **not** implicitly grant access to another user's personal resources. Until an explicit resource-sharing ACL is designed, approved and audited, each resource remains scoped to its actual owner. This applies both across tenants and between different members of the same tenant.

### Resource boundary matrix

| Resource | Owner authority | Cross-user behavior |
|---|---|---|
| Uploaded Files | FileStore `owner_key_id` | API-key owner only; master admin remains privileged |
| Request History | UserAccountStore-owned API-key IDs | User session sees only requests from its own keys |
| Billing / charges | BillingStore `user_id` | User sees only its own wallet, charges and orders |
| Archived Media metadata | MediaArchive `owner` | `/v1/media/{id}` only returns metadata/token to the owning key |
| Generated-file metadata | GeneratedFileArchive `owner` | `/v1/generated-files/{id}` only returns metadata/token to the owning key |
| Native content sidecar | Telemetry request `api_key_id` + NativeContentStore owner | Exact owning key only |
| Worker attachment download | Active telemetry request + Worker `client_id` + `input_file_ids` + FileStore owner | Only the Worker assigned to the active request can fetch the exact attached file |
| Admin archived request media | Telemetry request owner + FileStore `owner_key_id` | Refuses a file whose owner does not match the request row |

### Worker file-read hardening

The former `/api/extensions/files/{file_id}` boundary authenticated a paired Worker but treated that Worker as a global reader. It did not bind the requested file to the active request, assigned Worker, or API-key owner.

The revised route requires `request_id` and checks all of the following before reading bytes:

1. The extension token is valid for the supplied `client_id`.
2. The request exists and is still `running`.
3. The request's persisted `client_id` equals the authenticated Worker.
4. The requested file ID is in that request's persisted `input_file_ids`.
5. The request's `api_key_id` equals the FileStore item's `owner_key_id`.

Unknown, stale, unattached, wrong-Worker and wrong-owner combinations return 404. The request ID and exact file set are persisted before Worker dispatch for Chat Completions, Images and Responses. The Chrome extension forwards the request ID with the file fetch.

### Capability URL exception

Archived Media and Generated Files also expose opaque bearer-token download URLs in API output. Possession of the exact token remains a read capability by design; these URLs are not equivalent to authenticated metadata endpoints and are not revoked merely because a user is not a tenant member. Do not describe token URLs as session-bound or tenant-bound. A future requirement for non-shareable downloads needs a separate authenticated download/session design and migration of clients.

### Regression coverage

- Cross-tenant user sessions cannot read another user's request history, billing rows or API-key secret.
- Cross-tenant API keys cannot read/delete another key's FileStore item or read Media/Generated File metadata.
- A tenant member cannot access another member's personal resources merely by sharing the same tenant.
- Native sidecar access is checked against the authoritative telemetry owner.
- Worker attachment downloads reject missing request IDs, unknown requests, wrong Workers and files not attached to the active request.
- Responses dispatch persists its request owner, Worker and exact attachment set before sending the Worker message.

The following remain outside this slice: explicit resource sharing ACLs, token-URL revocation/session binding, full W-01/W-05 browser E2E, and production deployment acceptance.

### Release compatibility gate

This branch changes the Worker attachment-fetch protocol by requiring `request_id`. The current production release remains `0.22.291`; do not deploy this route change against an older installed Worker bundle. When this stack is approved for release, bump the unified Server/Worker/Bridge release version (next candidate: `0.22.292`), update the manifest and runtime version contract together, and verify that the new Worker bundle is served before enabling the required request-scoped download parameter. This release/version migration is intentionally deferred from the feature branch.

### C-03B request-scoped Worker attachment reads

- A Worker file-read grant is bound to the exact request, assigned client ID,
  API-key owner, attached file ID, and original dispatch WebSocket. A newly
  connected socket reusing the same paired client ID cannot inherit a still
  running request's attachment grant. The Broker lease and current registry
  socket must still reference the exact socket that received the dispatch.
- Telemetry marked `running` is not sufficient by itself: a missing Broker
  lease, terminal Broker state, failed dispatch, detached socket, replacement
  socket, mismatched owner, or file absent from the request attachment list
  all fail closed.
