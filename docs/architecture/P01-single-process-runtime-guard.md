# P-01 — single-process runtime boundary guard

Status: **Draft / partial**. This work makes the current deployment boundary fail closed; it does not make the server multi-instance capable.

## Why the guard exists

The current runtime keeps request state in process-local components, including the Broker, ClientRegistry/Worker socket registrations and some response/recovery indexes. Running multiple ASGI server processes against one data directory can split request ownership, route Worker messages to a process that does not own the Broker lease, and produce inconsistent recovery/accounting observations.

The repository's Docker Compose service currently starts one Uvicorn process and mounts one local `./data:/app/data` volume. This is the supported topology today.

## Implemented in this PR

- `app.entry` acquires a non-blocking OS advisory lock at `<data_dir>/.chat2api-runtime.lock` **before importing `app.main`**, because `app.main` constructs persistent stores at module import time.
- The already-acquired guard is held through the existing ASGI lifespan and released at shutdown.
- A second server process using the same data directory fails before `app.main` can initialize stores.
- A preloaded Gunicorn-style fork worker detects that the lock descriptor belongs to the parent PID and fails closed instead of inheriting the parent's lock.
- On POSIX, the lock file is mode `0600`; Windows uses the ACL inherited from the data directory, which must itself be access-controlled. The guard rejects symlinked data directories/lock files and hard-linked lock files (which could otherwise cause `fchmod` to alter another file), and retains the lock inode after shutdown. Retaining the inode is important: unlinking a locked file could let a second process create a new inode and bypass the lock.
- Known multi-worker environment variables (`WEB_CONCURRENCY`, `UVICORN_WORKERS`, `GUNICORN_WORKERS`) with numeric values greater than one are rejected.
- The lock is released during ASGI shutdown.
- Tests cover exclusive ownership, release/reacquire, multi-worker environment rejection, symlink rejection, preloaded-fork rejection and FastAPI lifespan integration.
- A subprocess-level production-entry regression starts `app.entry` in one process, proves a second process is rejected before `app.main` is imported, checks the retained lock inode, terminates the first process and verifies a third process can reacquire the directory. This exercises the actual import-time guard but is not a substitute for the separately required Docker Compose / mounted-volume smoke on the deployment host.

## Integration boundary

The guard is installed once from `app.entry:app`, before the legacy installer chain. This PR is the sole owner of this narrow production-entry change; other workstream PRs should continue to provide independent modules and must not independently edit `app.entry.py`.

## Persistence inventory

The source-level asset inventory, current storage owners, process-local state,
and the unselected JSON-to-transactional-database migration boundary are
documented in [P-01 persistence asset inventory](P01-persistence-asset-inventory.md).
It is explicitly a repository inventory, not a claim that production data has
been inspected.

## Offline backup / restore verification helper

The same PR adds an offline manifest-based verifier:

- `python -m app.data_dir_backup create --source ./data --destination /secure-backups/chat2api-YYYYMMDD --acknowledge-plaintext-sensitive-data`
- `python -m app.data_dir_backup verify --backup /secure-backups/chat2api-YYYYMMDD`
- `python -m app.data_dir_backup restore-check --backup /secure-backups/chat2api-YYYYMMDD --target /tmp/chat2api-restore-check --acknowledge-plaintext-sensitive-data`

Creation acquires the runtime data-directory lock, refuses to run while a server owns it, excludes the runtime lock inode, rejects symlinks, and writes a private manifest (POSIX mode `0600`; Windows inherited directory ACL) with relative paths, sizes, SHA256, modes and file types. Verification checks the exact file set, checksums, JSON/JSONL parseability and SQLite integrity. Restore-check writes only to a new target, verifies the restored copy, and never overwrites an existing directory.

**This helper does not encrypt or sign backups.** Its SHA256 manifest detects accidental corruption but is not an authenticity guarantee against an attacker who can modify both files and manifest. It requires an explicit plaintext-sensitive-data acknowledgement because G0/DataKeyring remains deferred. Store backups only in access-controlled locations; do not treat the helper as permission to back up production secrets to an untrusted destination.

## Explicit limitations

- This is a guard for processes sharing the same data directory and a filesystem that implements reliable advisory locks. It is **not** a distributed lock across separate volumes, hosts or filesystems with unreliable lock semantics.
- It does not add Redis/shared Broker state, distributed Worker leases, transactional API-key/billing storage, multi-instance telemetry, or response-store replication.
- Do not scale the Compose service horizontally or run multiple replicas with separate data volumes. The deployment remains single server process / single instance.
- This does not run or approve G0, DataKeyring, production secret migration, or any production data migration.
- No production deployment or multi-instance support is claimed.

## Acceptance still required

- Exact-head CI and production image smoke.
- Verify Docker Compose starts normally, a second process pointed at the same mounted data volume fails before store initialization, and a preloaded fork worker cannot inherit the parent's lock.
- Verify graceful shutdown releases the lock and abrupt process death releases the OS lock without deleting the retained lock file.
- Add a future shared-state architecture only as a separately reviewed P-01 design and migration; do not remove this guard merely to enable replica count.
