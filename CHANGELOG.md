# Changelog

## v0.22.73 — 2026-09-10

### Strict per-API Worker FIFO

- Replace the bounded v27 handoff grace with a hard per-logical-API FIFO: the same API key is pinned to Worker #1 and the next browser submission waits until the previous request reaches a completed, error, or cancelled terminal state.
- Prevent historical `::worker2` / `::worker3` spill for the same logical API while keeping different API keys independently concurrent.
- Keep queued cancellation fail-closed so a request cancelled before admission is never submitted to ChatGPT.

### Responses / FDEX compatibility

- Add Responses Tool Stream v118 to normalize only the exact qualified identity equivalence `functions.exec` == `namespace=functions, name=exec`; mismatched nested tool identities remain rejected.
- Carry request-window observability v117 and the prior v116 malformed nested custom-exec recovery into the new release.

### Worker packaging and version contract

- Publish the changed browser-worker source as Chrome Worker Bundle `0.8.29` rather than reusing `0.8.28`.
- Align the manifest, content bundle markers, runtime contracts, bounded runtime preflight, central Worker Bundle sync, and release diagnostics on `0.8.29` so stale `0.8.28` Workers are detectable and refreshed.
- Package `conversation_workers_v28.js` and `conversation_dispatch_v29.js` through the production Worker bundle entrypoint.

### Versions

- Server Runtime `0.22.73`.
- Chrome Worker Bundle `0.8.29`.
- Chrome Bridge wire protocol remains `0.8.1`.
- Python package remains `0.7.1`.

## v0.22.67 — 2026-09-07

### User console

- Add the isolated `/console` user portal with Dashboard, API Keys, Request History, Account Profile, Billing Center, Model Marketplace, Playground and Developer Documentation.
- Bind user-generated managed API Keys to exactly one user account and filter user request history and analytics by that ownership mapping.
- Keep user-facing request/history/playground surfaces free of Worker, browser-extension, device, routing and other internal runtime implementation details; sanitize proxied failures at the user boundary.
- Use HttpOnly user sessions with only session-token hashes persisted server-side and scrypt password hashes with per-user random salts.

### Pricing and billing

- Add administrator `价格配置` for per-model input, cached-input and output prices plus a configurable USD/CNY billing conversion rate.
- Seed the model price table from the current OpenAI public API pricing reference as of 2026-09-07 while keeping every value administrator-editable.
- Keep user billing disabled by default on upgrade. When explicitly enabled, completed user-owned requests create idempotent price snapshots and debit the user's wallet; legacy/admin-managed keys remain outside the user billing boundary.
- Mark token and cost values in the user console as estimated where the request telemetry itself is estimated.

### Payments

- Add administrator `支付配置` and a ZPAY integration derived from the existing GPTWork payment contract: PID, encrypted merchant key, Alipay/WeChat enablement, optional channel IDs, public callback origin and API connectivity testing.
- Add user recharge orders, ZPAY MAPI checkout, signed asynchronous callbacks, PID/status/amount/channel verification and idempotent wallet settlement.
- Store payment secrets encrypted at rest and never return the merchant key through administrator or user read APIs.

### Versions

- Server Runtime `0.22.67`.
- Chrome Worker Bundle remains `0.8.28`.
- Chrome Bridge wire protocol remains `0.8.1`.
- Python package remains `0.7.1`.

## v0.22.66 — 2026-09-07

### ChatGPT page resilience

- Recognize the new ChatGPT Health promotion modal shown over the chat composer and dismiss it only through an explicit Close/关闭 control or a tightly bounded top-right SVG X fallback.
- Never click the Health modal's `开始使用` / `Get started` action, and continue refusing to automate authentication, payment, account deletion, identity-verification or other high-impact confirmation surfaces.
- Promote UI hygiene to revision 101 and require it in both content runtime contracts and the background runtime preflight so hot-healed Worker tabs cannot silently miss the modal handler.
- Keep the compatible Chrome Worker Bundle epoch at `0.8.28`; server-side Worker auto-sync detects the changed `chrome_extension/` payload and forces the online Worker refresh for this hotfix.

### Request history and diagnostics carried into this release

- Keep Request History on a single canonical renderer, remove the obsolete side detail panel, preserve explicit conversation/log actions, and prevent legacy asset routes from repainting old request rows.
- Report the canonical Server Runtime version in diagnostic bundles instead of the historical v8 patch version.
- Preserve current Worker binding truth in `pairing.log` even when the bounded Worker log window no longer contains the original pairing event.

### Versions

- Server Runtime `0.22.66`.
- Chrome Worker Bundle `0.8.28`.
- Chrome Bridge wire protocol remains `0.8.1`.
- Python package remains `0.7.1`.

## v0.22.56 — 2026-09-04

### Worker window routing

- Make v88 the final browser-window routing authority and claim the oldest ready warm/reserve window by FIFO opening time before legacy fallback wrappers can open a new window.
- Assign stable window numbers and expose loading, ready, in-use and closed lifecycle state in the administrator Window Management view.
- Register newly created Worker windows immediately as loading so management telemetry starts at browser-window creation rather than only after warm-pool readiness.

### Request lifecycle and latency

- Make a successful network completion monotonic so a later synthetic cancel/error cannot downgrade the same request and trigger route/window recycling.
- Preserve successful routed windows for the existing five-minute lease and repair a route if an older recovery layer already began resetting it.
- Replace multi-kilobyte synchronous contenteditable `execCommand("insertText")` writes with the bounded v88 direct-text insertion path while retaining request-v6 validation and submit ownership.
- Ensure programmatic content bootstrap and runtime preflight both inject and verify the v88 terminal/prompt guard, so a hot-healed or reloaded tab cannot silently lose these fixes.

### Admin console

- Add `窗口管理` with active and closed window lists, stable window number, device-code name, request ID, opened time, lifecycle state and on-demand screenshots.
- Surface the existing authoritative `req_*` request ID in request history for direct correlation with Worker windows and diagnostics.

### Versions

- Server Runtime `0.22.56`.
- Chrome Worker Bundle `0.8.26`.
- Chrome Bridge wire protocol remains `0.8.1`.
- Python package remains `0.7.1`.

## v0.22.54 — 2026-09-04

### Worker lifecycle

- Keep disabled Workers collapsed to one managed ChatGPT window by blocking every delayed reserve/warm refill after the administrator disable boundary.
- Preserve healthy completed routed conversations instead of recycling them when content-controller cleanup slightly trails the outward terminal event.
- Keep failed and cancelled routes on the existing quarantine/recycle path.

### Request latency

- Add a current-runtime fast path before expensive Worker runtime healing.
- Bound runtime-contract and tool-isolation probes so a preflight probe cannot itself create a long pre-prompt stall.

### Admin console

- Repair the request-history `提示词` column against the current dynamic row renderer.
- Keep audited final-prompt viewing and copying available after request-table repainting.

### Versions

- Server Runtime `0.22.54`.
- Chrome Worker Bundle `0.8.24`.
- Python package remains `0.7.1`.

## v0.22.38 — 2026-08-30

First formal GitHub release of the current production runtime line.

### Linux Worker

- Fix managed Worker draft recovery so stale chat2api-owned drafts are not misclassified as manual/unknown drafts.
- Keep manual or unowned ChatGPT tabs protected from automatic composer overwrite.
- Package and use the Linux generation backend probe in the Worker bundle.
- Report proxy health as four independent facets: configured, network, GPT, and latency.
- Make the proxy-health renderer independent from the legacy stable-table blocker and reconcile the proxy cell after legacy table repainting.

### Browser bridge

- Chrome Worker Bundle `0.8.12`.
- Preserve real conversation SSE progress as generation liveness evidence.
- Runtime preflight now requires the full `0.8.12` bundle contract.

### Server

- Server Runtime `0.22.38`.
- Python package remains `0.7.1` because this release changes the deployed runtime/Worker bundle, not the Python distribution compatibility surface.
- Add a release workflow that creates one GitHub Release per Server Runtime version after a validated merge to `main`.
