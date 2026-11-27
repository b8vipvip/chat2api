# W-03 — Web Chat safe native renderer

Status: the production /chat page loads the safe Markdown renderer, the native-content renderer, stream recovery, attachment support and locally hosted KaTeX 0.19.1 before the Web Chat client. The renderer consumes the owner-checked native-content sidecar produced by the existing Worker capture and server validation pipeline. This is substantial native-content delivery, not a claim of complete ChatGPT 1:1 component parity.

## Production path

- app/web_chat_integration_v1.py serves the versioned renderer assets and the owner-scoped native-content GET/SSE endpoints.
- app/native_content_v1.py validates the bounded native snapshot schema, media references, SVG/MathML attributes, typed control metadata and integrity/finality evidence.
- chrome_extension/content_native_content_v1.js captures only the verified assistant reply scope. It does not export raw HTML, JavaScript, arbitrary CSS, event handlers, iframe contents or untrusted media URLs.
- app/chat_native_renderer_v1.js builds fixed DOM elements from the validated schema. It does not use innerHTML for upstream content.
- app/chat_client_v1.js loads the selected request's native snapshot, subscribes to native snapshot SSE updates and falls back to safe Markdown when a snapshot is unavailable or rejected.
- KaTeX 0.19.1 is self-hosted with pinned assets and bounded rendering settings (trust: false); unsupported/invalid TeX falls back to visible text. Native MathML remains a separate allowlisted rendering path.

## Supported native delivery

- Text layout: headings, paragraphs, emphasis, lists, blockquotes, tables, code and copy-code controls.
- Verified raster images and owner-scoped archived generated files.
- Safe public HTTPS citation anchors and source cards, with title/domain/snippet previews.
- Bounded SVG geometry/metadata and MathML presentation nodes/attributes.
- Generated audio/video file cards using authenticated same-origin archive URLs and byte-range playback.
- Tool/function-call activity and results as bounded, escaped, presentation-only cards; tool cards do not grant execution authority or determine request completion.
- Typed interactive snapshots for disclosures, checkbox/radio/range/select controls and local code-copy actions.

## Interaction support boundary

The typed controls currently have deliberately limited semantics:

| Control | Web Chat behavior | Upstream ChatGPT behavior |
|---|---|---|
| details/summary disclosure | Locally expandable | State is not sent back |
| Checkbox / radio / range / select | Local preview | State is not submitted |
| Code-copy button | Local clipboard action | No upstream action |
| SVG chart metadata | Browser-native title/description | No chart-legend/action bridge |
| Arbitrary buttons, tabs, canvas/WebGL, iframe and React event handlers | Omitted or inert fallback | Not remotely executable |

The current implementation has no general remote-action bridge from a Web Chat control to the original ChatGPT DOM. Do not imply that changing a local preview changes the upstream generated app. Unsupported components retain the canonical Markdown answer or a safe inert fallback. A future action bridge must be request-scoped, owner-checked, revision-bound, allowlisted by control type and explicitly user-triggered; it must not forward arbitrary JavaScript or click arbitrary DOM nodes.

## Live media boundary

Web Chat camera input is relayed as bounded JPEG/PNG/WebP frames (maximum 5 FPS, 300 frames and 64 MiB per session) into a synthetic WebRTC camera track in the Worker ChatGPT tab. The Worker confirms sender attachment and frame receipt; a detached sender is reported as snapshot fallback rather than being mislabeled as WebRTC delivery. This is not a direct browser-to-Worker camera track, and it does not establish frame-accurate audio/video synchronization. The active Live Voice media session is also not claimed to survive a complete Worker/browser restart.

Generated MP3/WAV/M4A/OGG/Opus/FLAC and MP4/WebM files are a separate archived-file delivery path; archived media playback is not equivalent to live A/V transport.

## Security invariants

- Raw upstream HTML, scripts, event handlers, inline style text and arbitrary URLs are never inserted into the Web Chat DOM.
- Media and generated-file URLs are server-issued, same-origin and owner-checked.
- Citation anchors are restricted to validated public HTTPS destinations and open with noopener/noreferrer/nofollow and no-referrer.
- Native tool activity is presentation-only. The server-side plugin executor remains the only route for user-authorized GitHub/Gmail operations in Web Chat.
- Mode 2 public native-content projection remains a compatibility projection and downgrades unsupported native controls to inert content.

## Validation and remaining acceptance

CI covers the schema validators, safe renderer DOM harness, native snapshot hydration, file/media integrity, citation handling, tool-state persistence and cross-device conversation merge behavior. CI does not substitute for a real signed-in Worker + ChatGPT staging session.

Remaining Issue #854 work is specifically:
1. A request-scoped, safe action bridge for supported generated-app controls if product parity requires upstream state changes.
2. Frame-accurate A/V synchronization across browser capture, server relay and Worker WebRTC clocks.
3. Recovery semantics for an active Live Voice media call across a full Worker/browser restart; current text/SSE recovery must not be represented as live media continuity.
4. Real staging acceptance for Worker reconnect, Broker/server restart, media/file integrity, usage settlement and cross-device browser behavior.
5. Further component-by-component parity review; the current allowlist intentionally does not execute arbitrary app code.

Do not label already implemented citation, MathML/KaTeX, archive playback, sampled camera-frame relay or local typed-control rendering as absent. Equally, do not label the above remaining boundaries complete without implementation and evidence.
