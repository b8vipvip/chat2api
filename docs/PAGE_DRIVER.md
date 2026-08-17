# ChatGPT Page Driver

`chrome_extension/content_page_driver_v22.js` is the write-side orchestration and verification boundary that sits above `content_page_adapter_v22.js`.

## Why it exists

The Page Adapter is deliberately read-only. It centralizes ChatGPT DOM discovery but must not click controls, dispatch keyboard input, install page-wide observers or own feature state machines.

The Page Driver is the next boundary. It is allowed to become the common orchestration layer for model/reasoning writes, but migration is intentionally incremental. Phase 3 starts with verification and error classification only; the proven reasoning keyboard/slider/click implementation remains in `content_reasoning_v7.js`.

Global API:

```text
globalThis.__CHAT2API_PAGE_DRIVER_V22__
```

Current internal driver version:

```text
22.2.0
```

## Phase 3 responsibilities

The driver currently owns:

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

## Reasoning integration

`content_reasoning_v7.js` still owns every actual reasoning write operation:

- Ctrl+Shift+M menu shortcut;
- Enter / Space activation;
- Home / End / ArrowRight slider navigation;
- native `<input type="range">` updates;
- click fallback.

After a successful zero-op or switch, the controller asks the Page Driver to attach a second independent state snapshot. This snapshot is diagnostic in phase 3; the established background `model_routing_v2.js` final probe remains the authoritative request gate.

When reasoning selection fails, the runtime response now includes a stable `code` plus Page Driver verification diagnostics when available. This makes logs distinguish a missing control, a menu-open failure, an unavailable requested level, an untrusted state and a real post-selection mismatch.

## Existing-tab bootstrap recovery

Manifest content scripts only run automatically for matching pages at their normal injection lifecycle. Existing ChatGPT tabs can therefore need dynamic recovery after an extension update or when the background explicitly re-runs `ensureContent()`.

`content_bootstrap.js` must inject the compatibility layers before feature controllers in this exact dependency order:

```text
content_page_adapter_v22.js
content_page_driver_v22.js
content_model_v7.js
content_reasoning_v7.js
```

Both Adapter and Driver are idempotent, so this recovery injection is safe when they are already present. Keeping them in the bootstrap list ensures dynamically recovered reasoning requests receive the same Page Driver diagnostics as freshly loaded tabs.

## Safety boundary

Phase 3 Page Driver itself performs no clicks, keyboard dispatch, timers or MutationObserver work. This is intentional: it establishes the contract and telemetry surface before any write mechanics move out of the historical controller.

A later phase may migrate one write primitive at a time behind the Page Driver only after real-browser smoke coverage demonstrates equivalent behavior.
