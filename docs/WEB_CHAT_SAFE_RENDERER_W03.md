# W-03 — Web Chat safe renderer v2

Status: **safe renderer is loaded by the production Web Chat page; KaTeX
0.19.1 is now self-hosted for bounded TeX rendering**. The structured rich-block
API remains an allowlisted UI contract and is not yet fully bound to every
native-content sidecar.

## Implementation

- `app/chat_renderer_v2.js` contains the standalone allowlist renderer.
- This PR deliberately does not modify `chat_client_v1.js`, `chat_client_v1.html`,
  `user_console_v104_patch.py`, or `app.entry:app`: those shared integration
  points are concurrently owned by the image attachment and storage work.
- A later integration PR must load the renderer before the client under the
  existing CSP and fail closed to plain text if the asset cannot load.
- All output is built with explicit, fixed DOM constructors and `textContent`.
  Raw upstream text is never interpreted as HTML. Links allow only HTTP(S)
  without embedded credentials, use `noopener noreferrer` and disable referrers.
- Safe Markdown subset: headings, paragraphs, emphasis, inline code, fenced
  code and copy, lists, tables, blockquotes, horizontal rules and links.
- TeX math is rendered by the pinned KaTeX 0.19.1 browser engine, hosted
  locally by the Chat2API server with its own CSS and font files. No third-party
  CDN is contacted at runtime. The renderer supports inline `$...# W-03 — Web Chat safe renderer v2

Status: **safe renderer is loaded by the production Web Chat page; KaTeX
0.19.1 is now self-hosted for bounded TeX rendering**. The structured rich-block
API remains an allowlisted UI contract and is not yet fully bound to every
native-content sidecar.

## Implementation

- `app/chat_renderer_v2.js` contains the standalone allowlist renderer.
- This PR deliberately does not modify `chat_client_v1.js`, `chat_client_v1.html`,
  `user_console_v104_patch.py`, or `app.entry:app`: those shared integration
  points are concurrently owned by the image attachment and storage work.
- A later integration PR must load the renderer before the client under the
  existing CSP and fail closed to plain text if the asset cannot load.
- All output is built with explicit, fixed DOM constructors and `textContent`.
  Raw upstream text is never interpreted as HTML. Links allow only HTTP(S)
  without embedded credentials, use `noopener noreferrer` and disable referrers.
- Safe Markdown subset: headings, paragraphs, emphasis, inline code, fenced
  code and copy, lists, tables, blockquotes, horizontal rules and links.
 and
  `\\(...\\)`, plus display `$...$` and `\\[...\\]` delimiters and
  structured `math` rich blocks. KaTeX runs with `trust: false`, bounded
  expansion/size settings and a text-only fallback when its asset is absent.
  Native MathML snapshots continue through the separate allowlisted MathML
  renderer; KaTeX is not claimed to cover every LaTeX package or reproduce all
  ChatGPT-specific UI components.
- The MIT license is retained with the generated vendor distribution. Docker
  builds and CI obtain the exact pinned npm package; source checkouts can run
  `sh scripts/vendor_katex.sh` to generate the same local assets.
- Inline citation markers of the form `citeturn0search0` are resolved
  only against bounded Worker tool-reference metadata. Validated reference IDs
  survive Broker forwarding, server conversation persistence and the durable
  event journal. Matched markers become numbered HTTPS links; the same numbers
  appear on the source cards in the tool activity panel. Missing or untrusted
  references render as an unavailable-source marker rather than a guessed link.
- `renderRichBlocks(host, blocks)` supports safely named presentation types:
  text, code, math, citation, image, file, tool state, and unknown blocks.
  File blocks with a strictly validated `file_...` ID render a same-origin
  download card pointing to the W-05A owner-checked download endpoint; the
  endpoint rechecks the session and FileStore owner and returns 404 for
  non-owned IDs. Image blocks with a strictly validated `file_...` ID render
  only through W-01's same-origin image-content endpoint, which rechecks the
  session, owner key and raster MIME allowlist. Neither block accepts a
  caller-supplied URL or filesystem path; invalid IDs render inert fallbacks.
- Tool-state cards are read-only; they are neither tool execution permission
  nor proof of Worker completion. Citations are labeled unverified links.

## Excluded from this PR

Complete ChatGPT 1:1 component parity, full coverage of every LaTeX
package, arbitrary HTML, third-party runtime CDN/script loading, tool execution,
plugins, and any change to Mode 2/Broker/Worker request semantics. Native
snapshot finality and durable SSE replay remain separate release gates.

## Validation

`pytest -q tests/test_web_chat_renderer_v2.py tests/test_web_chat_client_v1.py`

The Node VM DOM harness asserts that malicious SVG/script/iframe/image markup
is never constructed, unsafe schemes cannot create active links, and math,
table, code, citation and tool-fallback data are rendered as text-only nodes.

The module is not yet loaded by the production `/chat` page. A dedicated
integration PR after #793 settles must add asset serving and the script tag,
then run real desktop/mobile browser fixtures. Image previews require W-01's image-content route and file cards require
W-05A's download route to be integrated before they can succeed in production. End-to-end native sidecar
presentation integration remains a separate release gate. Do not claim
ChatGPT 1:1 parity.
