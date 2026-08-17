# ChatGPT Page Driver

`chrome_extension/content_page_driver_v22.js` is the write-side orchestration and verification boundary that sits above `content_page_adapter_v22.js`.

## Why it exists

The Page Adapter is deliberately read-only. It centralizes ChatGPT DOM discovery but must not click controls, dispatch keyboard input, install page-wide observers or own feature state machines.

The Page Driver is the next boundary. Migration is intentionally incremental: it first established passive verification/error classification, then Phase 7 moves exactly one low-level write primitive—keyboard event dispatch—behind the Driver. High-level model/reasoning algorithms remain feature-owned.

Global API:

```text
globalThis.__CHAT2API_PAGE_DRIVER_V22__
```

Current internal driver version:

```text
22.3.0
```

## Verification responsibilities

The driver owns:

```text
currentState
verifyState
verifyReasoning
classifyReasoningError
attachVerification
```

It combines passive Page Adapter evidence with the existing trusted `chat2api:model-state:v2` session cache. Dirty cache values are never promoted to trusted state.

Verification returns stable machine-readable codes:

```text
ok
model_state_untrusted
model_mismatch
reasoning_state_untrusted
reasoning_mismatch
```

Reasoning failures are additionally classified as:

```text
reasoning_selection_failed
reasoning_control_not_found
reasoning_menu_open_failed
reasoning_level_unavailable
reasoning_local_verification_failed
```

The driver exposes the diagnostic runtime message:

```text
chat2api.page.verify.v22
```

## Phase 7 explicit keyboard primitive

Phase 7 adds:

```text
dispatchKey(target, name, code, extra)
```

This primitive only dispatches one `keydown` followed by one `keyup` with the same event fields the historical reasoning controller already used. It is stateless and call-only: Page Driver never invokes it autonomously, chooses no keys, opens no menus, installs no timers, and has no click logic.

`content_reasoning_v7.js` now prefers `PageDriver.dispatchKey(...)`, but retains the exact local `KeyboardEvent` implementation as a compatibility fallback when the Driver is unavailable, the target is invalid, or Driver dispatch throws. The existing Reasoning call sites are unchanged, including Ctrl+Shift+M, Enter/Space, Home/End/ArrowRight and Escape.

Phase 7 deliberately does **not** move:

- reasoning menu strategy;
- slider walking or midpoint logic;
- native `<input type="range">` mutation;
- click fallback;
- delays / retry timing;
- post-selection verification;
- model-family selection.

## Reasoning integration

`content_reasoning_v7.js` remains the owner of the Reasoning state machine and all decisions about what action to perform and when. After a successful zero-op or switch, the controller asks the Page Driver to attach an independent state snapshot. The established background `model_routing_v2.js` final probe remains the authoritative request gate.

When reasoning selection fails, the runtime response includes a stable `code` plus Page Driver verification diagnostics when available. This lets logs distinguish a missing control, menu-open failure, unavailable requested level, untrusted state and a real post-selection mismatch.

## Existing-tab bootstrap recovery

Manifest content scripts only run automatically for matching pages at their normal injection lifecycle. Existing ChatGPT tabs can therefore need dynamic recovery after an extension update or when the background explicitly re-runs `ensureContent()`.

`content_bootstrap.js` injects the compatibility layers before feature controllers in this dependency order:

```text
content_page_adapter_v22.js
content_page_driver_v22.js
content_model_v7.js
content_reasoning_v7.js
```

Both Adapter and Driver are idempotent, so this recovery injection is safe when they are already present.

## Phase 5 background diagnostics propagation

`model_routing_v2.js` preserves structured controller failures instead of reducing them to a plain error string. A failed reasoning preparation carries the controller `code`, controller diagnostics, Page Driver version and verification snapshot into the background routing error context.

The background persists that context in `lastModelDiagnostics`, emits it through `chat.diagnostics`, and adds the same `code` and `diagnostics` fields to `chat.error`. Successful reasoning preparation also records the controller identity, Page Driver version, verification result and any verification warning in the normal request diagnostics.

The routing layer additionally distinguishes its own final passive verification failures with:

```text
model_verification_failed
reasoning_verification_failed
```

These routing codes identify the separate condition where the controller completed but the authoritative post-selection `probeState()` gate still observed the wrong final model or reasoning state.

## Phase 6 smoke prerequisite

`content_page_smoke_v22.js` and `background_page_smoke_v22.js` provide the real-tab read-only smoke boundary introduced before write migration. Operators can verify Adapter → Driver → controllers → composer → final passive probe without changing the page state. Phase 7 keeps that smoke path read-only even though Driver itself now exposes the explicit `dispatchKey` primitive to feature controllers.

## Safety boundary

Page Driver still has no autonomous write behavior, no MutationObserver, no timers, no click operation and no range mutation. Its only write primitive is explicit keyboard dispatch initiated by an existing feature controller.

The final background `probeState()` family/reasoning checks remain authoritative. Additional write primitives should only migrate one at a time with the same local fallback and real-tab smoke boundary.
