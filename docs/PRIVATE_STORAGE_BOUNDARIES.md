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
  writes. Data-secret initialization is serialized across local processes.
- Administrator session hashes, encrypted Worker pairing codes and the
  hash-chained audit log use the same no-follow, single-link private-file
  boundary; pairing/session data is never written through predictable temp names.
- Payment-provider ciphertext/secrets, prompt configuration, plugin catalog and
  quota-reservation SQLite state use private files/databases with no-follow
  reads and validated SQLite sidecars.
- Telemetry, diagnostics and test-run startup readers retain the validated file
  descriptor while reading the bounded JSONL tail; they do not validate a path
  and then reopen it.
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
