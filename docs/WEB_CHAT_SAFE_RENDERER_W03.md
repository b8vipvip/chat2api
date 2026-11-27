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
- Typed interactive snapshots for disclosures, checkbox/radio/range/select controls, accessible tablists/tabs, semantic `aria-pressed` toggle buttons and local code-copy actions. On verified-final snapshots, supported controls use a narrow request-scoped action bridge to the original Worker tab; successful actions are persisted as a new native snapshot revision.

## Interaction support boundary

The typed controls currently have deliberately limited semantics:

| Control | Web Chat behavior | Upstream ChatGPT behavior |
|---|---|---|
| details/summary disclosure | User-triggered upstream state sync | Exact captured disclosure only |
| Checkbox / radio / range / select | User-triggered upstream state sync | Exact captured control/value only |
| Accessible tablist / tab | User-triggered upstream tab selection | Exact captured `role=tab` element in its original tablist |
| `aria-pressed` toggle button | User-triggered upstream pressed-state sync | Exact captured semantic toggle; useful for supported chart-series toggles |
| Code-copy button | Local clipboard action | No upstream action |
| SVG chart metadata | Browser-native title/description | No generic SVG/chart event bridge |
| Arbitrary buttons, non-semantic chart actions, canvas/WebGL, iframe and React event handlers | Omitted or inert fallback | Not remotely executable |

The implementation has a deliberately narrow remote-action bridge for typed disclosures, checkbox/radio/range/select controls, accessible tab selection and `aria-pressed` toggle buttons. A tab action is accepted only when the captured tab is nested under its validated tablist group; the Worker clicks that exact captured `role=tab` element. Each action is same-origin and owner-checked, bound to the verified-final snapshot revision and original Worker socket/tab, ACKed by the Worker, and committed as a new persisted snapshot revision. Stale revisions, detached controls, invalid options, out-of-range values, orphan tabs and ambiguous selected states fail closed. The bridge does not accept arbitrary JavaScript, generic DOM selectors, arbitrary button clicks or model-generated commands. Unsupported components retain the canonical Markdown answer or a safe inert fallback.

## Live media boundary

Web Chat camera input is relayed as bounded JPEG/PNG/WebP frames (maximum 5 FPS, 300 frames and 64 MiB per session) into a synthetic WebRTC camera track in the Worker ChatGPT tab. The Worker confirms sender attachment and frame receipt; a detached sender is reported as snapshot fallback rather than being mislabeled as WebRTC delivery. PCM input and camera frames now share a session-relative monotonic clock: the Worker schedules microphone PCM behind a bounded 160 ms jitter buffer and, when `CanvasCaptureMediaStreamTrack.requestFrame()` is available, requests each camera frame against the same AudioContext timeline. A 5 FPS timer-driven fallback remains available. Server diagnostics record local scheduling skew (`sync_skew_ms`); this is not proof of end-to-end skew at the remote WebRTC receiver. The active Live Voice media session is not claimed to survive a complete Worker/browser restart.

Generated MP3/WAV/M4A/OGG/Opus/FLAC and MP4/WebM files are a separate archived-file delivery path; archived media playback is not equivalent to live A/V transport.

## Security invariants

- Raw upstream HTML, scripts, event handlers, inline style text and arbitrary URLs are never inserted into the Web Chat DOM.
- Media and generated-file URLs are server-issued, same-origin and owner-checked.
- Citation anchors are restricted to validated public HTTPS destinations and open with noopener/noreferrer/nofollow and no-referrer.
- Native tool activity is presentation-only. The server-side plugin executor remains the only route for user-authorized GitHub/Gmail operations in Web Chat.
- Native control actions are explicit user input, not autonomous model tools. They are limited to captured typed controls and the exact request revision; the original Worker socket and tab must still exist.
- Mode 2 public native-content projection remains a compatibility projection and downgrades unsupported native controls to inert content.

## Validation and remaining acceptance

CI covers the schema validators, safe renderer DOM harness, native snapshot hydration, file/media integrity, citation handling, tool-state persistence and cross-device conversation merge behavior. CI does not substitute for a real signed-in Worker + ChatGPT staging session.

Remaining Issue #854 work is specifically:
1. Broader component parity remains incomplete: arbitrary buttons, chart legends/actions without supported ARIA semantics, canvas/WebGL, iframe widgets and arbitrary React handlers are not remotely executable. Accessible `role=tablist` / `role=tab` selection and exact `aria-pressed` toggle buttons are supported through the typed action bridge.
2. A full Worker/browser process restart destroys the original WebRTC PeerConnection and media tracks. The current implementation can start a new Voice session with conversation context, but cannot preserve the same active media call across process death; recovery UX must state this clearly.
3. Real staging acceptance remains necessary for remote-end A/V timing, browser/Worker reconnect, Broker/server restart, media/file integrity, final usage settlement and cross-device browser behavior. Local `sync_skew_ms` is not remote receiver end-to-end evidence.
4. Further component-by-component parity review; the allowlist intentionally does not execute arbitrary app code.

Do not label already implemented citation, MathML/KaTeX, archive playback, sampled camera-frame relay or local typed-control rendering as absent. Equally, do not label the above remaining boundaries complete without implementation and evidence.
