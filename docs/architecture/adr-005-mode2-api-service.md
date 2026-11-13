# ADR-005: Mode 2 protocol adapters and shared Chat Request Service

- **Status:** Proposed, code implementation under review
- **Date:** 2026-11-12
- **Track:** M (independent of G0 / R / W / C / D-P)
- **Related:** #784 M-01/M-02; M-01 [PR #802](https://github.com/b8vipvip/chat2api/pull/802); M-02a [PR #806](https://github.com/b8vipvip/chat2api/pull/806); M-02b [PR #797](https://github.com/b8vipvip/chat2api/pull/797)

## Context

Two production Mode 2 entrypoints, Chat Completions and Responses, have
distinct protocol surfaces but both create `RequestBroker` leases and send
`chat.request` messages to a selected browser Worker. The platform already
has request-v6 as its single Worker terminal authority, plus separate
logging, media, native-layout finality, and SSE delivery components.
Duplicating these authorities in a new API or Web BFF would permit conflicting
terminals, double writes and inconsistent cancellation behavior.

At the same time, OpenAI SDKs expect well-defined JSON/SSE and upload
semantics. The initial `/v1/files` route accepted only JSON/base64; this
slice adds standard multipart support for `assistants`, `vision` and
`user_data`, plus standard metadata/content/delete response shapes. File
listing and batch/fine-tune/evals purposes remain unsupported. Chat usage is
estimated; echoed requested model is not a verified served-model claim. These
differences must not be hidden behind a blanket "OpenAI-compatible" promise.

## Decision

1. **Mode 2 is the stable default** for `/v1/*`. Only the existing
   `ModeResolverMiddleware` interprets the optional mode header. Mode 1
   must never be an API ingress fallback.
2. **Protocol adapters remain distinct.** Chat Completions and Responses
   preserve their respective request validation, model options, typed
   response/stream framing, HTTP error mapping and compatibility extensions.
   They do not independently create a second Worker/Broker terminal.
3. **One shared application-service seam.** The common Chat Request Service
   reserves a request through the existing `RequestBroker` and emits the
   canonical `chat.request` envelope through the existing
   `ClientRegistry`. The service does **not** persist terminal events,
   select models or Workers, execute media archives, settle SSE, bill
   requests, or own browser tab state. Adapter-specific admission
   diagnostics and failure cleanup remain in the existing adapter.
4. **Web BFF will call the shared service directly** after validating the
   user's session, API-key entitlement and model permission. It must not
   HTTP self-proxy into `/v1/chat/completions` and must not hold an
   independent Broker or retry/replay a completed generation.
5. **Machine-readable compatibility is mandatory.** The matrix
   `mode2-api-compatibility-matrix.json` distinguishes supported subsets,
   extensions, unimplemented standard functionality and estimates. Tests
   enforce route-owner uniqueness on the actual production patch chain.
   The matrix is updated before any future claim of added SDK parity.
6. **Official SDK smoke tests exercise the real API.** Both Python OpenAI
   2.x and Node OpenAI 6.x SDKs call a live Chat2API ASGI/Uvicorn server.
   The external Chrome/Worker socket is the only replaced boundary; fake
   Worker completions are published through the real Broker. These tests
   cover JSON/SSE, model retrieval, legacy Completions, Responses, standard
   multipart Files create/retrieve/content/delete and error envelopes.
   Real Chrome Worker end-to-end testing remains a separate release gate.

### Boundary table

| Domain | Single authority | Allowed collaborator |
| --- | --- | --- |
| API mode | Mode Resolver | Adapter consumes resolved mode |
| Authentication / scopes | Existing authenticated API ingress | Future BFF validates user/session before service |
| Worker selection | Existing registry/routing policies | Service receives already resolved client ID |
| Request lease | Existing RequestBroker | Shared service forwards reserve; no competing lease map |
| Browser terminal | request-v6 + existing Broker | Adapters observe/persist their own delivery state |
| SSE serialization | Chat/Responses protocol adapters respectively | Service does not send SSE |
| Media/file finality | Existing archive and request-owned integrity gates | Service only carries pre-authorized attachment specs |
| Usage / model evidence | Existing telemetry/billing/provenance policies | Compatibility matrix marks estimates and unknowns explicitly |

## Alternatives considered

- **BFF calls the local /v1 endpoint:** rejected; loops through HTTP auth,
  doubles admission/telemetry boundaries and obscures canonical request ID.
- **Replace all API handlers with one new executor:** rejected; risks
  altering mature SSE/Responses patch ownership and request-v6 finality.
- **Claim SDK parity for any accepted Pydantic extra field:** rejected;
  unknown or tolerated fields do not prove upstream tool or schema behavior.
- **Migrate to a second queue/message broker:** rejected for this slice;
  request lifecycle and Worker admission would have competing owners.

## Rollout and verification

1. M-01 independently delivers the full 20-route matrix, OpenAPI route-coverage checks, multipart Files and HTTP error normalization, plus live Python/Node SDK regressions.
2. M-02a delivers the new service with a fake registry, real Broker and
   negative tests for Worker busy collision and dispatch failure.
3. M-02b integrates one service instance into `create_app` and both real
   adapters. Its PR is stacked on M-02a and must be rebased/retargeted
   after M-02a is accepted; existing downstream handlers still manage
   SSE, media and terminal release.
4. Before calling M complete, re-run full CI and production-image boot,
   verify no extra `/v1/responses` route owner, and check Python and
   JavaScript SDKs against a real on-prem Worker with request IDs and
   redacted provenance. The latter is not part of this patch slice.
5. Future Files list, batch/fine-tune/evals purpose support, complete
   endpoint-specific error normalization and remaining standard SDK
   parameters require separately scoped compatibility tests.

## Decision status

**Proposed, not a production deployment approval.** PRs remain Draft while
CI/review/completion-lease gates are unresolved. M-02 never authorizes
changes to G0 production encryption or the R track's Worker finality rules.
