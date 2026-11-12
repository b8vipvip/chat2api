# W-03 — Web Chat safe renderer v2

Status: **integrated for text Markdown**, structured rich-block API is an
allowlisted UI contract only (not yet bound to native-content sidecars).

## Implementation

- `app/chat_renderer_v2.js` owns all Chat2API assistant Markdown rendering;
  `chat_client_v1.js` delegates to it. The existing renderer implementation
  is removed from the chat-client closure to enforce one display owner.
- `/assets/chat2api-safe-renderer-v2.js` is served from the existing
  user-console installer with a no-store and `nosniff` policy.
- `/chat` loads the renderer before the existing client, under the existing
  same-origin CSP. If the renderer fails to load, the assistant reply is shown
  as plain text instead of interpreting it as HTML.
- All output is built with explicit, fixed DOM constructors and `textContent`.
  Raw upstream text is never interpreted as HTML. Links allow only HTTP(S)
  without embedded credentials, use `noopener noreferrer` and disable referrers.
- Safe Markdown subset: headings, paragraphs, emphasis, inline code, fenced
  code and copy, lists, tables, blockquotes, horizontal rules and links.
- Math uses a text-only TeX fallback until a reviewed offline typesetting engine
  is available; it is **not** mathematical formula rendering parity.
- `renderRichBlocks(host, blocks)` supports safely named presentation types:
  text, code, math, citation, image, file, tool state, and unknown blocks.
  Image/file entries deliberately show a non-clickable preview placeholder
  until a separate attachment owner/authorization contract is supplied.
- Tool-state cards are read-only; they are neither tool execution permission
  nor proof of Worker completion. Citations are labeled unverified links.

## Excluded from this PR

Image attachment upload (#793), authoritative native-rich-block attachment,
file download, native snapshot finality, SSE resume, tool execution, plugins,
full LaTeX/MathML, arbitrary HTML, third-party CDN/script loading and any
change to Mode 2/Broker/Worker requests.

## Validation

`pytest -q tests/test_web_chat_renderer_v2.py tests/test_web_chat_client_v1.py`

The Node VM DOM harness asserts that malicious SVG/script/iframe/image markup
is never constructed, unsafe schemes cannot create active links, and math,
table, code, citation and tool-fallback data are rendered as text-only nodes.

A real desktop/mobile browser fixture pass and any end-to-end native sidecar
presentation integration remain release gates. Do not claim ChatGPT 1:1 parity.
