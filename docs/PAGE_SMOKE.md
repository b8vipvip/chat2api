# ChatGPT Page Smoke Test

Phase 6 adds a read-only smoke-test boundary that runs inside a real logged-in ChatGPT tab. It exists to verify the Page Adapter → Page Driver → model/reasoning controllers → final passive probe chain before any more write mechanics move into shared infrastructure.

## Real-tab scope

The content harness is loaded as:

```text
chrome_extension/content_page_smoke_v22.js
```

It runs in the same isolated-world context as the existing ChatGPT page controllers and is therefore able to inspect the actual DOM state seen by chat2api on a 真实 ChatGPT 标签页.

The harness runtime message is:

```text
chat2api.page.smoke.v22
```

The test is deliberately 只读. It checks:

- Page Adapter presence and version;
- Page Driver presence and version;
- model-state controller presence;
- reasoning controller presence;
- composer presence and visibility;
- passive model-family evidence;
- passive reasoning evidence;
- Page Driver current-state / verification snapshot.

It does not click controls, dispatch keyboard events, mutate a range input, install observers or timers, and it不会切换模型或推理强度.

## Independent final probe

`chrome_extension/background_page_smoke_v22.js` resolves the actual bound ChatGPT tab, runs the content smoke harness, then performs an independent existing:

```text
chat2api.model.probe.v7
```

when the extension has a canonical expected text model in storage. This preserves the same passive family/reasoning semantics used by request routing instead of trusting only the smoke harness itself.

The background result contains:

```text
ok
code
checks
current_state
verification
family_evidence
reasoning_evidence
final_probe
final_probe_ok
```

A structurally healthy page with a failed final probe is classified as:

```text
final_probe_mismatch
```

The last result is persisted as `lastPageSmoke` / `lastPageSmokeAt` in `chrome.storage.local` for later diagnosis.

## Existing-tab recovery

`content_bootstrap.js` injects the smoke harness after:

```text
content_page_adapter_v22.js
content_page_driver_v22.js
content_model_v7.js
content_reasoning_v7.js
```

so an already-open ChatGPT tab receives the same diagnostic contract after extension reload/update as a newly loaded tab.

## Operator entrypoint

The extension popup exposes:

```text
运行页面 Smoke Test（只读）
```

The button calls the background `popup.pageSmoke` message and displays the state of Adapter, Driver, model controller, reasoning controller, composer, expected state, Driver state and final probe.

## Safety boundary

Phase 6 still does not migrate write behavior. `content_reasoning_v7.js` continues to own shortcut, Enter/Space, slider keyboard navigation, native range updates, click fallback and post-selection verification. The Page Driver remains verification-only.

A later phase can use this real-tab smoke boundary as the prerequisite for moving one explicit write primitive at a time, while keeping the existing final `chat2api.model.probe.v7` request gate authoritative.
