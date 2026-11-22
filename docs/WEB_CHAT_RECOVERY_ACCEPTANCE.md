# Web Chat recovery manual acceptance

This runbook is for the staging deployment of PR #855. It distinguishes browser reconnect replay from recovery after the API/Broker process restarts. Do not run the restart test against production traffic.

## Preconditions

- Deploy the candidate branch to an isolated staging instance with a persistent CHAT2API_DATA_DIR.
- Pair the Chrome Worker extension with that staging instance and confirm its ChatGPT tab is signed in and reports chatgpt_login_state=ready.
- Keep the Worker browser and ChatGPT tab open for the entire restart test. Restart only the API/Broker process.
- Use a dedicated test API Key and a non-sensitive prompt. Do not include API tokens, cookies, or private conversation content in test evidence.

## A. Browser/network disconnect (same Broker process)

1. Open Web Chat and submit a prompt that produces a multi-second streamed response.
2. In DevTools Network, record the X-Chat2API-Request-ID header from POST /api/user/chat/completions.
3. Confirm that delivery events have monotonically increasing SSE IDs.
4. Disconnect or close only the Web Chat tab after at least one delivery delta. Do not reload or restart the Worker.
5. Reopen Web Chat under the same user account. Confirm the client requests GET /api/user/chat/requests/{request_id}/events with the saved Last-Event-ID; it must not POST the generation request again.
6. Confirm that the final event is replayed once, the assistant message is not duplicated, and the request ID remains unchanged.

Pass criteria: owner-scoped replay resumes from the last acknowledged event ID, no new generation is dispatched, and the final delivery state matches the durable journal.

## B. API/Broker restart while the real Worker remains alive

1. Start another long-running Web Chat response and record its request ID and last observed delivery event ID.
2. After receiving at least one delivery delta, restart only the staging API/Broker process. Do not close Chrome, reload the extension, or sign out of ChatGPT.
3. Wait for the extension WebSocket to reconnect. Confirm the Worker sends its durable terminal outbox event for the original request ID, tagged with worker_transport_replayed=true and worker_transport_recovery=transport-outbox-v47.
4. Confirm the server responds with a positive chat.terminal.ack containing recovered=true. The extension must retain the terminal in chrome.storage.local until that positive ACK and clear it afterward.
5. Query the owner-scoped recovery endpoint and event replay endpoint from the same signed-in Web Chat session. Confirm that the original request is not redispatched and that the event IDs do not move backward.
6. Inspect Telemetry, event journal, media/file archive and quota reservation state. When the original request is still inside the recovery grace window and its durable admission evidence is present, `chat.completed` must run the idempotent finalization path: restore the full response, archive verified media/files, persist the completed journal terminal and reconcile usage exactly once. A duplicate replay of an already-finalized terminal must re-check durable usage settlement and delivery terminal before ACK. If finalization fails, the original identity evidence is missing, or the terminal type cannot be safely finalized, keep the request stalled/provisional, return a negative ACK, and retain the Worker outbox for retry. Never fabricate success or charge twice.

Pass criteria: the same authenticated Worker and request ID are reconciled, the durable outbox is positively acknowledged, no duplicate generation is started, and no false completed state or duplicate usage charge is recorded.

## Evidence to capture

- Request ID and the last SSE event ID before the interruption.
- Worker extension reconnect timestamp and terminal replay/ACK outcome.
- Recovery endpoint status and final event-journal snapshot.
- Telemetry status, web_chat_request_recovery, web_chat_terminal_replay_reconciled, and quota reservation state.
- Confirmation that the Worker outbox is empty only after a positive ACK.

Redact API Key material, cookies, authorization headers, email addresses, and conversation text.

## Current boundary

Automated CI covers the two-server restart/outbox replay path with a real WebSocket test client and a separate extension outbox/service-worker-context harness. It does not possess the user's live ChatGPT login or physical Chrome Worker session. The real-browser test above remains an external acceptance gate.

A successful restart-safety test does not yet mean full response restoration: after Broker state is lost, the current server intentionally keeps a replayed chat.completed response provisional/stalled until media archival and usage reconciliation can be made idempotent.