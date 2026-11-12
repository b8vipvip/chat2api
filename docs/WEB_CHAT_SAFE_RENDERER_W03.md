# W-03 — Web Chat safe renderer v2

Status: **standalone renderer module and security fixtures implemented; not yet
wired into the production Web Chat page**. The structured rich-block API is an
allowlisted UI contract only (not yet bound to native-content sidecars).

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

The module is not yet loaded by the production `/chat` page. A dedicated
integration PR after #793 settles must add asset serving and the script tag,
then run real desktop/mobile browser fixtures. End-to-end native sidecar
presentation integration remains a separate release gate. Do not claim
ChatGPT 1:1 parity.
