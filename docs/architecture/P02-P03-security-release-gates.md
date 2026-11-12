# P-02 / P-03 — security, performance, end-to-end and release gates

Status: **acceptance plan / Draft**. This document consolidates evidence requirements; it does not certify the project or authorize a release.

The source of truth remains the owning workstream PR and its immutable-head checks. A test name or a green check on an older SHA is not evidence for the current SHA.

## P-02 — security and performance acceptance matrix

| Area | Current evidence / owner | Status | Remaining acceptance |
|---|---|---|---|
| SSRF / outbound network | C-05a plugin registry rejects external JSON Schema references and plugin execution is hard-disabled (tests/test_plugin_registry_v1.py, #814) | Prototype boundary only | No plugin execution is allowed. Before execution exists, add process sandbox, egress allowlist, DNS/IP rebinding controls and per-plugin secrets isolation; do not infer SSRF safety from disabled execution alone. |
| CSRF / Origin | Existing v17 admin same-origin guard; W-02 conversation mutations; W-04 cancellation; W-05 download is read-only (#801, #799, #816, #817) | Partial | Re-run same-origin, missing-Origin, cross-origin and Fetch Metadata negative cases against the integrated app.entry:app. W-01 preview/delete error cache headers still need shared-file synchronization after #786. |
| XSS / renderer | W-03 allowlisted node renderer and hostile citation/image URL fixtures (tests/test_web_chat_renderer_v2.py, #813) | Module-level | Browser E2E must verify DOM output, link navigation, image sources, CSP and no script execution after real API payloads. |
| CSP / CORS / MIME | Web Chat assets, image preview and file download response headers; global CORS policy in app.main | Partial | Validate production reverse-proxy headers, allowed origins, credentials, MIME sniffing and attachment behavior from a real browser. |
| Cookie / API-key leakage | Web Chat session revalidation and file/conversation owner checks (#793/#799/#817); admin role-aware UI (#820) | Module-level | Inspect browser storage, network traces, redirects, error telemetry and exported logs. Verify no user session token, API key or file bytes leak into localStorage, URLs or diagnostics. |
| Cross-account access | W-01 image owner tests, W-02 conversation owner tests, W-05 document download tests | Partial | Repeat with two real accounts and keys after composition-root integration; include revoked/disabled keys and session expiry. |
| RBAC / tenant isolation | C-01 explicit route permission matrix (#801); C-02 membership catalog (#815) | Partial | Route matrix must be tested against every mounted admin route. Tenant membership is not resource isolation until file, conversation, request, key and billing owners consume the membership policy at every access. |
| Plugin authorization | C-05a manifests are untrusted and execution_enabled=false (#814) | Safe non-executing prototype | No plugin trust, user grants, execution, outbound calls or secrets are implemented. Keep execution disabled. |
| Concurrency / idempotency | R Broker ownership tests; W-02 revision/mutation tests; C-06a SQLite oversubscription and terminal reservation ID tests (#799/#819) | Partial | Exercise concurrent multi-process attempts and crash points only after the supported topology and transactional store are defined. |
| Long connections / backpressure | R-03 detached SSE terminal race tests (#796); W-04 same-request snapshot recovery (#816) | Partial | Browser/proxy/ASGI 2–8 second disconnect root cause and backpressure behavior remain unverified. W-04 is snapshot recovery, not token/event replay. |
| Resource limits | W-01 image limits; W-02 bounded snapshots; W-03 renderer budgets; C-05a manifest/schema limits | Module-level | Add integrated upload, download, large response, slow client, concurrent stream and memory-pressure tests under production-like limits. |
| Failure injection | R-02/R-03/R-04/R-06 and C-06a tests | Partial | Run latest-head CI and controlled staging failures for telemetry, disk, Worker socket, database lock, archive and reverse proxy. Confirm rollback and terminal ownership. |
| Performance | Existing stage timings and regression suites | Not accepted | Establish representative p50/p95/p99 latency, throughput, memory, file IO, SSE disconnect and Worker-startup baselines. Do not claim the ~160s Worker delay is fixed until same-request real logs prove it. |

### Required P-02 execution order

1. Freeze the exact integrated commit SHA and production image digest.
2. Run the complete Python/Node contract suites against that SHA.
3. Run the security negative matrix with two accounts, two API keys and delegated admin roles.
4. Run load and failure-injection tests with the same server/Worker versions.
5. Archive redacted request IDs, check run URLs, test summaries and image digests. Never archive credentials, raw user prompts, cookies or private page DOM.
6. Keep any row marked partial/not accepted as a release blocker for the feature it protects.

## P-03 — real E2E and release checklist

### Composition and API
- [ ] app.entry:app installs only the reviewed modules, in one documented order; no test-only FastAPI composition substitutes for this.
- [ ] Official OpenAI Python and Node SDK compatibility suites pass against the exact deployed image.
- [ ] Chat Completions and Responses use the same Chat Request Service, Worker command contract and existing Broker/request-v6 terminal owner.
- [ ] Mode 2 is the default and explicit API contract. Mode 1 remains NO-GO and disabled.

### Web Chat
- [ ] Image picker, paste, drag/drop, upload, preview, removal and submit tested in real desktop browsers.
- [ ] Conversation history persistence, owner isolation, revision conflicts, reload, session expiry and migration are tested.
- [ ] Recovery updates the exact pending assistant message; cancellation signals the same assigned Worker and waits for the authoritative terminal.
- [ ] Renderer XSS/CSP, citations, tables, MathML, code, images and file downloads are tested in browser.
- [ ] Document upload UI and generated-file cards are implemented and tested; download API alone is not completion.
- [ ] Cross-device sync and retention/cleanup are verified before server-owned history is advertised as production storage.

### Worker and native rich content
- [ ] Same request ID is traced across API admission, Broker, dispatch socket, Worker extension, native capture, terminal and file download.
- [ ] Windows and Linux Worker tests include real image bytes, SHA256, tables, code, MathML, generated files and multi-block replies.
- [ ] Worker extension version, diagnostic-tool version, server runtime version and actual deployed SHA are captured separately.
- [ ] A successful diagnostic ZIP is not treated as proof of business request ownership, complete file delivery or visual 1:1.

### Persistence, deployment and rollback
- [ ] P-01 single-process guard is validated against two server processes sharing the same mounted data directory.
- [ ] JSON/SQLite schema inventory, migration plan, backup, restore and rollback evidence are reviewed before any persistence migration.
- [ ] Production data remains untouched while G0/DataKeyring is deferred by user direction.
- [ ] Docker image digest, source SHA, Server version, Worker extension version and frontend asset version are recorded.
- [ ] Health check, graceful shutdown, abrupt process recovery, backup restore and rollback are executed in staging.
- [ ] Release notes, operator runbook, user documentation and known limitations match the shipped behavior.

## Hard exclusions and non-claims

- G0 / DataKeyring / production secret and data migration remain deferred by explicit project direction.
- Mode 1 Cookie proxy remains NO-GO and must not be enabled or implemented as a hosted multi-user private web proxy.
- No horizontal multi-instance support is claimed by the P-01 lock. It only prevents multiple processes sharing one supported local data directory.
- A green PR CI, production image smoke or GPTAuto Observer is not equivalent to Completion Lease terminal status, human review, staging E2E or production release approval.
- D-01 and D-02 require their own Linux/Windows Worker evidence; this plan does not replace those physical gates.
