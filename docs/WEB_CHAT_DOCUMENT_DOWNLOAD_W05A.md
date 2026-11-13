# W-05A — owner-scoped document download boundary

Status: **standalone download API module and tests implemented; not yet registered
in the production entry point or connected to the Web Chat UI**.

## Scope

The existing `POST /v1/files` and FileStore already accept user-owned files.
This work adds a user-session download path for files referenced by the future
Web Chat document cards:

`GET /api/user/chat/files/{file_id}/download`

- Requires the existing HttpOnly user session.
- Resolves the FileStore row and verifies its `owner_key_id` belongs to the
  authenticated account. It never accepts a user ID or filesystem path.
- Returns 404 for unknown and non-owned IDs.
- Reads bytes through the existing FileStore API; it does not create another
  archive, hash authority, file status machine, or retention owner.
- Uses `Content-Disposition: attachment`, RFC 5987 UTF-8 filename encoding,
  `nosniff`, sandbox CSP, same-origin resource policy, no-referrer and
  private no-store caching on successful downloads and application-level error responses.
- Filename is normalized with the existing FileStore `safe_name` helper.

## Explicit limits

- No upload UI, drag/drop, file card, progress or retry UX in this PR.
- No Range/partial download support; current FileStore reads the bounded stored
  object into memory (default maximum 20 MiB).
- No generated-file archive integration. Generated files keep their existing
  archive/download owner.
- No application-layer at-rest encryption, retention cleanup, backup/restore,
  or multi-instance claim; G0/P storage gates remain.
- No production installer or shared UI changes. Integration is deferred until
  the W-01/#793 and W-03 shared page owners settle.

## Tests

`pytest -q tests/test_web_chat_document_download_v1.py`

Covers exact byte delivery, session requirement, cross-account 404, missing
disk content, MIME handling, safe filename disposition and security headers.

Storage I/O failures are mapped to a generic private/no-store 503 response; raw filesystem errors are not returned to the caller.
