# Web Chat production entry integration

Status: **Draft / partial product integration**.

This integration connects the existing /chat client to the W-03 safe Markdown
renderer, W-04 owner-scoped request recovery/cancellation APIs and W-05A
owner-scoped document download API. It does not enable the W-01 image upload
pipeline or the W-02 plaintext SQLite conversation prototype.

## Installed behavior

- The existing chat HTML loads the renderer and recovery controller before the
  client script. All scripts are same-origin, no-store and served by one
  production integration installer.
- Assistant Markdown is rendered by the DOM-only allowlist renderer. If the
  renderer asset is unavailable, the client falls back to textContent rather
  than unsafe HTML.
- Each Web Chat request supplies a client-generated req_ identifier using
  X-Chat2API-Request-ID. The server remains the authority for request identity
  and terminal status; the client never creates a second terminal owner.
- Pending request_id and assistant message identity are persisted in the
  existing account-scoped local browser history. On reload, the client polls
  the owner-checked recovery endpoint and never re-POSTs the generation.
- Stop sends the owner-checked cancel request to the assigned Worker, then
  stops local stream reading and continues to reconcile the original request.
- File download cards use the W-05A same-origin endpoint, which independently
  verifies the user session, API-key ownership and stored file bytes.

## Inherited security boundaries from C-03B

This PR is now stacked on the C-03B resource-ownership branch (PR #830).
The combined production tree must retain these independent server-side checks:

- Tenant membership is not an implicit grant to another user's personal API
  keys, request history, billing, files, archived media or native-content
  sidecars. Shared-workspace access requires a separately implemented and
  reviewed resource-sharing contract.
- A paired Worker attachment download is scoped to the exact live request,
  assigned Worker, API-key owner and file IDs explicitly attached to that
  request. A Worker token alone is not a global FileStore read capability.
- Administrator request-media retrieval verifies that the archived file's
  owner matches the API key recorded on the request.

The #830 test suite is inherited through the base branch and must remain in
the full production-entry CI run; do not rebase this Web Chat integration back
to the pre-C-03B base and silently drop those protections.

## Deliberately excluded

- W-01 image upload/preview is not installed. The renderer's structured image
  block is not connected to a live image endpoint in this integration.
- W-02 server conversation storage is not installed because the current
  prototype is plaintext SQLite and the DataKeyring/production storage gate
  remains deferred by user decision. Existing local browser history remains.
- This recovery API is snapshot polling, not durable token-level SSE replay.
  It can restore a final Telemetry response when available; it does not replay
  missing stream deltas or prove native 1:1 integrity.
- A recovered status never replaces Broker/request-v6 terminal ownership.

## Automated integration coverage

The app.entry subprocess test verifies production route uniqueness, owner-only
401 responses, private/no-store headers, static asset serving and script load
order. Existing W-03/W-04/W-05 tests remain included. The CI workflow runs
JavaScript syntax checks for all three client-side scripts.

## Remaining release gates

Real desktop/mobile browser E2E, live Worker cancellation, refresh recovery
under network interruption, cross-account attachment tests against production
FileStore, W-01 upload integration, W-02 encrypted server-side history,
durable event replay, production-image smoke and deployment/rollback evidence
remain required. This PR must remain Draft until these gates pass.
