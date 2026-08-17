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
22.0.0
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
modelReasoningControl
openSurfaces
reasoningSlider
```

`dispatchEnter` is exposed because submit recovery already required it, but it is only called by an existing request overlay. The adapter itself never invokes it automatically.

## Phase-1 migrated consumers

The first migration intentionally targets read-heavy, low-risk paths:

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

Each migrated consumer still keeps its previous selector logic as a fallback. This makes the first migration reversible and reduces the chance that loading-order or adapter regressions disable an existing path.

## Not migrated in phase 1

The following write-heavy automation remains unchanged for now:

- model menu selection logic;
- reasoning shortcut/menu/slider keyboard automation;
- multimodal upload control manipulation;
- Images UI automation;
- Voice / GPT Live UI and WebRTC hooks.

In particular, `content_reasoning_v7.js` remains authoritative for reasoning selection. A future phase can move its passive reads to the adapter first, then move write actions only after real-browser smoke coverage exists.

## Maintenance rule

When ChatGPT changes a selector used by an already-migrated read path, update the Page Adapter first. Do not add a new selector copy to every consumer unless it is a compatibility fallback with a documented removal plan.

New page-wide observers must not be added to the adapter. If a feature needs observation, keep the observer in the feature controller and call the adapter to read current state.
