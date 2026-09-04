# Changelog

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
