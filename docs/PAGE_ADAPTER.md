# ChatGPT Page Adapter

`chrome_extension/content_page_adapter_v22.js` is the shared browser-page compatibility layer for ChatGPT DOM reads used by chat2api.

## Why it exists

Historically, multiple content-script overlays each carried their own copy of selectors and helper logic for:

- locating the composer;
- locating the send button;
- detecting the Stop / generating state;
- reading assistant response nodes;
- reading the combined model / reasoning control;
- locating open menus, listboxes and reasoning sliders.

That made every ChatGPT DOM change a multi-file maintenance problem. A selector could be fixed in one overlay while another overlay continued using an older contract.

Page Adapter v22 introduces one shared read-oriented contract. Existing overlays may retain their old implementation as a fallback during migration, but should prefer the adapter whenever it is present.

## Runtime contract

The adapter is loaded immediately after `content.js` and before the historical content overlays.

Global API:

```text
globalThis.__CHAT2API_PAGE_ADAPTER_V22__
```

Current adapter version:

```text
22.1.0
```

The adapter deliberately has no timers, no global `MutationObserver`, no runtime message listener and no automatic clicks. Loading it must not change ChatGPT page state by itself.

## Text vs UI-label normalization

User text and UI labels use separate normalization rules.

`normalize(value)` is text-safe and only removes zero-width characters, collapses whitespace and trims. It must preserve meaningful user characters such as `✓`.

`normalizeLabel(value)` additionally removes UI selection/check marks. It is intended for model/reasoning/button labels only.

Do not use label normalization for prompt comparison or assistant response text.

## Current read API

The adapter currently owns:

```text
visible
labelOf
composerRoot
composer
composerText
sendButton
buttonReady
stopButton
isGenerating
isSendTarget
dispatchEnter
assistantNodes
assistantIdentity
assistantText
familyFromText
reasoningFromText
modelReasoningControls
modelReasoningControl
modelControl
reasoningControl
reasoningEvidence
modelFamilyEvidence
openSurfaces
reasoningSlider
```

`modelFamilyEvidence()` preserves the model detector's ambiguity rule: if visible composer evidence points to more than one model family, it returns `ambiguous-dom` rather than choosing one.

`reasoningEvidence()` only promotes the public adjustable levels `instant`, `medium` and `high`; the adapter may recognize the UI's `智能/自动` label for transition diagnostics, but it does not reinterpret that label as one of the public reasoning efforts.

`dispatchEnter` is exposed because submit recovery already required it, but it is only called by an existing request overlay. The adapter itself never invokes it automatically.

## Phase-1 migrated consumers

The first migration targeted read-heavy, low-risk paths:

- `content_request_perf_v21.js`
  - composer lookup
  - prompt text lookup
  - send-button readiness
  - Stop/generating lookup
  - send-target recognition
  - Enter fallback dispatch
- `content_completion_fast_v21.js`
  - Stop/generating lookup
  - assistant nodes
  - assistant identity
  - assistant text
- `content_model_transition_v15.js`
  - label visibility/normalization
  - composer root
  - model/reasoning control state
  - model/reasoning label parsing

Each migrated consumer keeps its previous selector logic as a fallback. This makes the migration reversible and reduces the chance that loading-order or adapter regressions disable an existing path.

## Phase-2 migrated consumers

Phase 2 moves the remaining high-value passive model/reasoning reads behind the adapter while deliberately leaving write automation unchanged:

- `content_model_v7.js`
  - visibility and label normalization
  - composer lookup
  - model-family passive evidence and ambiguity detection
  - reasoning passive evidence
  - model/reasoning label parsing
- `content_reasoning_v7.js`
  - visibility and label normalization
  - composer lookup
  - current reasoning-control lookup
  - open menu/listbox surface lookup
  - visible reasoning-slider lookup

The existing model-state observer and manual-choice cache listener remain in `content_model_v7.js`; they call the shared adapter to read state but retain ownership of cache mutation.

The existing shortcut, Enter/Space activation, slider keyboard navigation, native range updates and click fallback remain in `content_reasoning_v7.js`. The adapter only discovers the controls those operations act on.

## Still not migrated

Write-heavy browser automation remains feature-owned:

- model menu selection logic;
- reasoning shortcut/menu/slider keyboard automation;
- multimodal upload control manipulation;
- Images UI automation;
- Voice / GPT Live UI and WebRTC hooks.

Moving write operations into a shared page driver should happen only after real-browser smoke coverage can verify model/reasoning selection against current ChatGPT UI variants.

## Maintenance rule

When ChatGPT changes a selector used by an already-migrated read path, update the Page Adapter first. Do not add a new selector copy to every consumer unless it is a compatibility fallback with a documented removal plan.

New page-wide observers must not be added to the adapter. If a feature needs observation, keep the observer in the feature controller and call the adapter to read current state.
