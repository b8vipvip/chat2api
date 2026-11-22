# Private local storage boundaries

The application stores request history, diagnostics, playground test runs, Worker
probe policies/evidence, Web Chat conversations/events, uploaded files and
generated media under the configured data directory.

## Filesystem controls

- Sensitive append-only JSONL histories and the rotating application log are
  opened as single-link regular files, reject symlinks/FIFOs, and use POSIX
  mode `0600`.
- API-key ciphertext, client registry state and the server-local data secret
  use owner-private `0600` files, no-follow reads and unique exclusive atomic
  writes. Data-secret initialization is serialized with an OS-released lock file,
  so process crashes cannot leave a permanent startup-blocking directory lock.
- Administrator session hashes, encrypted Worker pairing codes and the
  hash-chained audit log use the same no-follow, single-link private-file
  boundary; pairing/session data is never written through predictable temp names.
- Payment-provider ciphertext/secrets, prompt configuration, plugin catalog,
  quota-reservation state, registration invitations, tenant membership and user
  subscriptions use private files/databases with no-follow reads and validated
  SQLite WAL/SHM/rollback-journal sidecars. Legacy payment-secret initialization is
  serialized across local API workers with an OS-released lock file, so a worker
  crash cannot leave a permanent startup-blocking directory lock or competing
  first-start encryption keys.
- Telemetry, diagnostics and test-run startup readers retain the validated file
  descriptor while reading the bounded JSONL tail; they do not validate a path
  and then reopen it.
- The redacted runtime log ring uses a `0700` directory, `0600` JSONL/rotated
  files, no-follow single-link reads/appends and a cross-process rotation lock.
- The privileged host updater keeps its shared data directory non-writable by
  group/other users, stores request/status/deployment JSON, GPTAuto hotfix proof
  state and its update lock as `0600` single-link files, and writes its `0640`
  log through validated no-follow descriptors. Its `.env` backup lives inside a
  unique `0700` temporary directory; compose backup/merge artifacts use exclusive
  private files. Systemd timeout reconciliation uses bounded no-follow readers
  and atomic writes; compose backups are restored without following links.
- Worker probe policy/evidence directories use POSIX mode `0700`; policy and
  event files use `0600` and are written through exclusive, no-follow paths.
- Web Chat conversation and event-journal SQLite databases validate the main
  database and WAL/SHM/rollback-journal sidecars as private regular files.
- The local generated-media archive uses a `0700` root, `0600` index/object
  files, encrypted provider settings, and no-follow reads/atomic writes.
- FileStore keeps its own owner-private index and content-file checks.

## Encryption boundary

Filesystem permissions protect against other local OS users and accidental
exposure through shared directories. They are **not** application-level
encryption. Conversation payloads, telemetry and diagnostic histories remain
plaintext at rest unless the deployment encrypts the underlying volume or
filesystem. Media provider credentials in `settings.enc` are separately
encrypted by the application. S3 uploads retain server-side encryption.

## Operational note

Run the application under a dedicated OS account and keep the configured data
directory on a private local volume. Do not place it in a shared web root or
grant unrelated users write access to it. Real deployment permissions should
be verified on the target operating system and container volume.
