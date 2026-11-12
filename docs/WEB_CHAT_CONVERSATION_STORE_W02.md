# W-02 — Server-owned Web Chat conversation projection (preview)

Status: **isolated backend prototype and contract tests implemented; not wired into production**.
Tracks #784 (parallel track W). This PR is intentionally independent of the
image-attachment work in #793 and of the Mode 2 request service. Per #784 §18.5,
W-02 integration depends on the data-storage ADR and key infrastructure; because
G0 is intentionally deferred, this PR must not expose plaintext conversation
storage from the production `app.entry:app` composition.

## Scope and ownership

- A user's existing `chat2api_user_session` cookie authenticates every call.
  The API does not accept `user_id`, API key, or model entitlement from the client
  as proof of identity.
- SQLite in `CHAT2API_DATA_DIR/web_chat_conversations.sqlite3` stores a separate
  owner-scoped conversation *projection*. It does not store Worker request
  terminal truth, rich-content verification, attachments, billing, or tool execution.
- The payload is text-only (`role=user|assistant`, `content: string`). Image file
  IDs from #793 **must not** be pushed into this API until an explicit, owner-checked
  attachment reference contract is implemented. Do not strip saved image content
  automatically when migrating from localStorage.
- No migration of existing browser history takes place in this step; old history
  stays on the device where it was created.
- SQLite supports a **single-host** deployment and same-directory restarts.
  This does not implement multi-instance consensus, transactional quota billing,
  encrypted-at-rest storage, or backup/restore tooling. G0/P gates remain open.

## API contract

Every route requires a valid user session; mutations additionally require an
exact same-origin `Origin` header and (when supplied) same-origin Fetch Metadata.

- `GET /api/user/chat/conversations?limit=30&offset=0&q=...` — paginated,
  owner-scoped summaries with bounded title/message-text search (`q` max 120
  characters; literal `%` and `_` are not SQL wildcards). The response includes
  `data`, `total`, `limit`, and `offset`; summaries omit message bodies.
- `GET /api/user/chat/conversations/{id}` — one conversation and its messages;
  returns 404 for non-owners, unknown IDs and deleted conversations.
- `PUT /api/user/chat/conversations/{id}` — create or replace a full snapshot.
  The body contains `title`, `model`, `messages`, integer
  `expected_revision`, and stable `mutation_id`.
- `DELETE /api/user/chat/conversations/{id}?expected_revision=N` —
  tombstone and remove message bodies. Retried deletes return 204 to the owner.

Example new conversation (`PUT`):

```json
{
  "title": "First chat",
  "model": "gpt-5.6-sol",
  "messages": [
    {"id": "message_0001", "role": "user", "content": "Hello", "createdAt": ""}
  ],
  "expected_revision": 0,
  "mutation_id": "mutation_0001"
}
```

Expected revision is 0 only for creation. A successful write returns revision
1, then each distinct update requires the last revision. Concurrent stale writes
return 409 rather than overwriting another device's data. An identical replay
of the *last* `mutation_id` returns the previously written revision; reuse
with changed content returns 409. Clients should fetch the latest snapshot on
409 and explicitly reconcile, not silently overwrite the remote version.

## Resource bounds

- Maximum 200 live conversations per user.
- Maximum 30 messages per conversation.
- Maximum 8,000 Unicode characters of content per message.
- Title maximum 80 characters, model ID maximum 120 characters.
- Client-generated IDs are restricted to 8–100 alphanumeric/underscore/hyphen
  characters. All SQL statements use bound parameters.

## Production gate and browser integration handoff

After #793's attachment branch lands, W-02 client integration should:

1. Load server summaries, then lazy-load full messages; do not overwrite existing
   local image drafts or API key state.
2. Explicitly migrate text-only local browser history with revision-0 PUTs,
   stable mutation IDs and confirmation to the user. Never silently upload prior
   local chats or attachment metadata to the server.
3. Keep a serial per-conversation write queue, retry only the same mutation ID,
   and reconcile 409 with a visible conflict state.
4. Sync on explicit reload/visibility refresh and after a completed turn, without
   interpreting SSE EOF or a client-side abort as Broker completion.
5. Add real browser E2E for login/logout, two devices, conflict, deletion,
   attachment references, and interrupted SSE.

## Test

`pytest -q tests/test_web_chat_server_conversations.py`

Also required before merge/release: `app.entry:app` route uniqueness and full
CI, a real browser two-device E2E, privacy/backup retention decision, G0 data
migration gate, and a release/rollback plan. No production deploy is requested.


All success and application error responses carry `Cache-Control: private, no-store` and `Vary: Cookie` (merged with other middleware variance), including authentication, CSRF, ownership, revision-conflict and malformed-ID errors.


Structured message content is currently limited to user-side text and owner-verified image file references (maximum four image references per message, PNG/JPEG/WebP/GIF MIME allowlist, 10 MiB per image and 20 MiB total per message). This is still a prototype contract; document parsing/attachment execution and browser sync are not implemented.
