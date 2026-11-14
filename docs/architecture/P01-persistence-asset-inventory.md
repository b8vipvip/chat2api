# P-01 — persistence asset inventory and migration boundary

Inventory baseline: repository main at `b682e1d445008df57e4de9675e2952ae040b8027`, reviewed 2026-11-12. This is a source-code inventory, **not** a claim that the user's production data directory has been physically inspected. Before any migration, compare this list with a redacted real-volume inventory.

## Current persistent assets

| Data asset | Current owner | Format / write model | Important boundary |
|---|---|---|---|
| data_dir/api_keys.json | ApiKeyStore | JSON document, temp-file replace | API-key metadata, hashes, scopes, quota and completed usage. One in-process store; not a transactional multi-writer ledger. |
| data_dir/request_history.jsonl | TelemetryStore | Append-only JSONL | Request history; recent lookup/index is bounded and in memory. Concurrent writers and cross-process recovery are not a shared database contract. |
| data_dir/clients.json | ClientRegistry | JSON document, temp-file replace | Registered client metadata persists; active sockets, busy state, routing and Broker leases remain process-local. |
| data_dir/user_accounts.json | UserAccountStore | JSON document, atomic JSON replace | User accounts and session-related records; sensitive account data. |
| data_dir/user_pricing.json | PricingStore | JSON document | Pricing configuration. |
| data_dir/user_billing.json | BillingStore | JSON document | Wallet, charge and order records; existing billing owner must remain authoritative. |
| data_dir/user_payment_config.json | Payment configuration store | JSON document | Provider configuration may include plaintext secrets; G0/secret migration gate applies. |
| data_dir/pairing_codes.json | PairingStore | JSON document | Pairing code hashes and encrypted recoverable code ciphertext; uses the server-local data secret. |
| data_dir/data_secret.key | load_or_create_data_secret / PairingStore | Plaintext local key file, mode 0600 after creation | Key material used to encrypt recoverable pairing-code ciphertext; loss/rotation breaks recovery. This is a G0 secret asset. |
| data_dir/files/index.json + data_dir/files/* | FileStore | JSON index plus binary file payloads | File metadata/owner and bytes must migrate together; owner checks and byte hashes are required. |
| data_dir/media_archive/index.sqlite3 | MediaArchive | SQLite | Existing media metadata store. |
| data_dir/media_archive/settings.enc | MediaArchive | Encrypted settings blob | Fernet key is derived from the configured CHAT2API_API_KEY; changing that master key without a reviewed re-encryption procedure makes the settings unreadable. Key ownership/rotation/restore is a G0 gate. |
| data_dir/media_archive/* | MediaArchive | Binary media payloads | Must remain byte-identical and owner-correlated across backup/restore. |
| data_dir/mode_management.json | ModeManagementStore | Versioned JSON, private atomic replace | Mode 2 remains default; Mode 1 request flag never overrides NO-GO authorization/runtime constants. |
| data_dir/native_content/*.json | NativeContentStore | Per-request JSON snapshots, atomic write and cross-process commit lock | Request ownership, snapshot finality and rich-content provenance must be preserved. |
| data_dir/worker_probes/policies.json | WorkerProbeStore | JSON document | Probe policy state. |
| data_dir/worker_probes/events/*.jsonl | WorkerProbeStore | Append-only JSONL | Probe events and generated evidence; redact before export. |
| data_dir/test_runs.jsonl | TestRunStore | Append-only JSONL | Playground/test-run records; recovery marks interrupted runs stalled. |
| data_dir/admin-update-status.json | updater status reader | JSON document | Deployment/update state; not a substitute for immutable image digest or deployed SHA. |

## Process-local state (not made durable by copying data_dir)

- RequestBroker RequestState, queues, futures and terminal state.
- ClientRegistry live WebSocket/socket map, busy-client set, API-key routing context and current Worker connection.
- Responses v108 in-memory response store (bounded to a recent 256 records).
- AdminSessionStore session tokens and several short-lived admission/recovery indexes.
- In-flight SSE iterators, response buffers, cancellation and request supervision tasks.

A data-directory backup alone cannot restore an in-flight request, active Worker socket, pending SSE stream or in-memory response. The supported deployment remains one server process / one instance until a shared Broker/registry/lease/event architecture is designed and accepted.

## Isolated PR prototypes — not part of the current production composition root

The following stores exist on independent Open/Draft branches and must not be treated as deployed production assets:

- W-02 Web Chat conversation SQLite prototype: message bodies are plaintext; no encrypted-at-rest or cross-device migration; not installed in app.entry:app.
- C-01 ControlPlaneStore JSON and AdminAuditStore JSONL: independent RBAC/audit prototype; no central composition-root integration.
- C-02 TenantMembershipStore SQLite, C-03 ModelEntitlementStore SQLite, C-05 PluginRegistry SQLite, C-06 QuotaReservationStore SQLite: independent catalogs/journals; not yet the authoritative owner for existing users, keys, models, plugins or completed billing.
- C-04 InvitationStore SQLite: email-bound invitation metadata and SHA256-only invitation-code hashes; the email field is plaintext, and the store is not installed in app.entry:app. It remains a prototype pending G0 encryption/retention and central C integration.
- Any future migration must explicitly choose which existing owner remains authoritative and must not dual-write competing usage, wallet or request-terminal state.

## JSON → transactional database migration decision

**No target database has been selected and no migration is implemented.** Do not infer that the isolated SQLite prototypes constitute the production database decision.

A separate ADR must decide:
1. Target database and supported topology (single-host SQLite versus multi-instance PostgreSQL or another transactional service).
2. One authoritative owner and schema version for each entity; primary/foreign keys, uniqueness, idempotency, locking/isolation, retention and audit.
3. How file/blob objects are transactionally associated with metadata and how orphan detection works.
4. Whether migration is offline-only or has a formally specified dual-read/cutover phase. Unreviewed dual-write is prohibited.
5. Count/hash/relationship validation, failure injection, backup/restore and rollback to the prior image and data snapshot.
6. How secret-bearing columns/fields are encrypted, rotated and recovered under G0; no key migration is performed by this P-01 work.

## Backup / restore safety

The P-01 helper creates a manifest with relative path, size, SHA256, mode and type; verifies JSON/JSONL parseability and SQLite integrity; rejects symlinks/path traversal; and restore-checks only into a new directory. It requires an explicit plaintext-sensitive-data acknowledgement because it does not encrypt backups.

This is an offline verification aid, not approval to back up production secrets. Production backup retention, encryption, access control, off-site copy and restore RTO/RPO still require an approved G0/operations policy and a real-volume drill.
