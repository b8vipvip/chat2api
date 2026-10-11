# GPTWork → chat2api: model evidence authority parity (v0.22.118)

Sources examined: `b8vipvip/GPTWork` `extension/network-evidence.js`, `extension/model-status-history.js`, and the `extension/tests/network-evidence.test.mjs` / `v05218-turn-response-policy.test.mjs` regression contracts.

## Transferable product-level invariants

1. A served / resolved / used model in the **same response turn** may establish actual-model evidence, provided explicit candidates do not conflict.
2. A default / profile model is always **diagnostic-only**, not proof of response-model identity. A disagreement between two defaults is *not* a served-model conflict.
3. A page/Picker label, request's `model`, or a generic `message.metadata.model_slug` does not prove which model served an API request.
4. An upstream model-fallback verdict requires concrete served evidence; mismatched default-profile metadata alone is not an upstream fallback.

## Adaptation

The chat2api Server `app/model_evidence.py` and Chrome Bridge `chrome_extension/model_evidence_main_v144.js` now maintain separate conflict scopes: `model_conflict` for explicit served candidates, and `default_model_conflict` for defaults. The isolated Bridge forwards both in the original request-scoped evidence envelope. The server telemetry preserves default mismatch/conflict as separate diagnostic flags, without fabricating `model_fallback_reason`.

No GPTWork private-core code, licensing logic, native hooks, or credential flows are copied. No change to chat2api Worker identity, routing, dispatch scheduling or safety limits. This change improves evidence *interpretation*, not real-browser login or transport support.

## Regression checks

- Python tests: multiple conflicting defaults, served plus conflicting defaults, absence of speculative fallback.
- Node end-to-end MAIN-world fetch event tests: default-only disagreement, concrete served metadata, concrete served contradiction, and explicit response-header authority.

The tests are synthetic protocol fixtures. Final verification against current official ChatGPT page/traffic still requires a live Worker.
