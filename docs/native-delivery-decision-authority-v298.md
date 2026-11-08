# Chat2API decision authority audit — native rich delivery v298

Status: enforceable architecture contract. Linked from Issue #585. This is NOT a claim that complex native 1:1 visual acceptance has passed.

## What triggered this audit (2026-11-08)

An authenticated real request ZIP for `req_e31653f3af2445acb9dc38b9692d7c2d` showed **three legitimately different statuses**:

- Browser 64 SSE frames including stop and [DONE]; Broker terminal completed; 15,322 returned Markdown characters.
- Upstream network signal aborted, `network_terminal_proof=false`, `network_stream_phase=error`; this is upstream transport evidence, *not* a second API business-terminal owner.
- Same-request assistant message-ID verified; private DOM 16 tables and 8 images (5 tiny 12x12), but final native snapshot failed `structural-code-editor-virtualized`; `native_final_capture_committed=false`, `native_snapshot_persisted=false`, finality rejected. Three image archives exist without verified DOM-to-byte identity.
- Requested `gpt-5.6-sol` was served `gpt-6`, so the model is unverified/conflicted.
- ABCD A's prior UI check falsely passed a code requirement because *any* triple-backtick fence (in fact a non-code Plain text output) counted as Python. The complete Python source is not in the 15,322-character canonical response. This is **not** a successful 1:1 delivery.

## Single writer, observer-only, conflict matrix

| Fact/decision | Exactly one owner | Evidence-only collaborators / forbidden override |
| --- | --- | --- |
| Request + Worker lease + current assistant identity | request-v6, bound request->tab/window->message ID | v291 identity parser, toolbars, media, network observer can propose facts only. Old roleless content cannot move the confirmed assistant message. |
| Worker terminal event | request-v6 emits once, Broker.publish enforces first terminal | v55 network, image/file collection, native capture and ABCD runner cannot independently emit a different request terminal; any timeout fallback must publish through the same Broker first-terminal guard. |
| Server business terminal/persisted state | Broker terminal and request finalizer | HTTP close/done, TestRunStore, logs, media archive and diagnostic failures have no write authority over this terminal; no completed-after-error reorder. |
| API SSE recipient evidence | ASGI/protocol transport facts | Browser DONE, EOF, HTTP disconnect and worker complete are different observations. No single layer may claim another observed a frame. |
| Native reply finality | `app.native_snapshot_finality_v282.native_finality_decision` **only** | Browser captures/ACK, table/image comparison, CodeMirror state, diagnostic page and frontend renderer can only supply evidence. No other module computes its own boolean finality. All native GET/SSE/export projections call this same decision. |
| Image byte identity/order | digest-qualified request-owned original mapping, archive singleflight | Count/dimension/media_index only propose matches; do not claim SHA identity, position or visual fidelity. Archive never emits request terminal and must not repeat after HTTP cancellation. |
| Model identity | actual served-model proof authority | requested model, wire slug, picker and test UI are separate observations; do not silently verify a mismatch. |
| ABCD A quality verdict | Playground content checker only | Never changes Broker request status. A valid Python executable block requires real fenced Python content; other fenced text does not pass. |

## Code audit findings, with remaining ownership risks

1. `chrome_extension/content_request_v6.js` alone creates the Worker `chat.completed`/error at request egress; `app/broker.py` has a first-terminal latch. That guard must remain the finalization authority. `app/stream_detached_terminal_v219.py` can send a timeout `chat.error`, but does so through `broker.publish` and must not overwrite a first terminal; continue strict concurrency testing.
2. `app/native_snapshot_finality_v282.py` is the only server-side publisher finality owner. It had many independent early-return predicates and two positive proof branches with no emitted reason. v298 makes **one pure reason-bearing decision**, with `native_snapshot_finality` only a compatibility projection, and request exports include this exact reason/owner. Structural native-final must never be mislabeled as pixel-perfect 1:1.
3. `chrome_extension/content_native_content_v1.js` is an evidence producer, not a terminal owner. v297 MAIN-world CodeMirror probe is bounded, but the current ZIP still reports a final virtualized-editor failure. A missing/old Worker bundle must be distinguishable by capability diagnostics, not inferred as a successful update from the server commit or extension's unchanged version string.
4. `app/native_capture_audit_v281.py` is read-only. It cannot turn image/table differences into positive identity proof; count comparisons are only meaningful on a complete same-request capture.
5. `app/native_content_v1.py.native_layout_summary` currently considers sequential `media_index` and equal counts for `media_mapping_complete`. This proves only slot coverage, **not SHA256 image identity**. It must not be used to claim actual byte identity/visual 1:1. The same repo has an unverified dimension-candidate helper; such guesses must remain presentation hints.
6. `app/admin_playground_abcd_v197.js` previously conflated presence of any fenced block with actual Python code. v298 rejects missing Python even if other Markdown fences exist. Its quality status must not be used to change the immutable Broker request terminal.
7. `app/terminal_archive_singleflight_v277.py` shares a shielded archive task bound to request, owner and response hash; `app/mixed_media_archive_v280.py` rolls back newly staged images on later file failure. Guard against duplicate archive reentry, cancellation and cross-owner contamination remains required by regression.

## Immutable post-terminal principles

- `request_id` + Worker lease + message ID must match for all optional data. Late captures or old revisions cannot replace a committed final revision.
- At most one request terminal; a correction to a *presentation* verdict never mutates it.
- A missing final native layout must stay rejected/unverified even if the canonical response is complete.
- The diagnostic export must keep **separate** `broker_terminal`, `api_delivery`, `upstream_network_evidence`, `native_finality_reason`, `media_byte_identity`, `model_verified`.
- Where evidence conflicts, fail closed for optional 1:1 display, fail open for optional observation errors, never make the global Worker completion wait for unbounded archive/editor polling.

## Acceptance gates to close Issue #585 (all still open)

- [ ] A: same request and assistant ID; real full Python code fenced in the **canonical API SSE text**, complete 16+ tables, no prompt echo, correct formula/order; native final sidecar same content and visual layout.
- [ ] B: all requested generated images have original SHA256 identities mapped one-to-one to the verified assistant DOM positions, not merely same dimensions/count.
- [ ] C: each XLSX/PPTX/JSON/ZIP real download is ready, signed byte-verified and ZIP members match originals, no duplicate archive writes.
- [ ] D: HTML source and downloadable application faithfully delivered; preview != executable file byte equality.
- [ ] Real client SSE ending, fail/success/cancel, large/rich input, native incremental and full terminal; negative tests for abort, old editor, stale snapshot and late retries.
- [ ] Only **one** module authorizes each listed state transition and a test fails if a second module begins writing it.
- [ ] Pixel/visual parity checked separately from structural finality; do not mark fully 1:1 until actually measured on deployed Linux/Windows Worker.
