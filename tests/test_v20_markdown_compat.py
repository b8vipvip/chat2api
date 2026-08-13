from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_extension_v076_loads_reasoning_transport_before_structured_format_capture() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.7.6"
    assert "content_reasoning_transport_v20.js" in scripts
    assert "content_format_v20.js" in scripts
    assert scripts.index("content_completion_v6.js") < scripts.index("content_reasoning_transport_v20.js")
    assert scripts.index("content_reasoning_transport_v20.js") < scripts.index("content_format_v20.js")


def test_structured_capture_preserves_block_semantics_instead_of_flattening_whitespace() -> None:
    source = (EXTENSION / "content_format_v20.js").read_text(encoding="utf-8")
    assert '"#".repeat(level)' in source
    assert 'tag === "UL" || tag === "OL"' in source
    assert 'tag === "BLOCKQUOTE"' in source
    assert 'tag === "PRE"' in source
    assert 'blocks.join("\\n\\n")' in source
    assert 'format: "markdown"' in source
    assert 'type: "chat.snapshot"' in source


def test_visible_reasoning_uses_existing_diagnostics_channel_not_default_content_delta() -> None:
    transport = (EXTENSION / "content_reasoning_transport_v20.js").read_text(encoding="utf-8")
    capture = (EXTENSION / "content_format_v20.js").read_text(encoding="utf-8")
    assert 'event?.type === "chat.status"' in transport
    assert 'type: "chat.diagnostics"' in transport
    assert "visible_reasoning_status" in transport
    assert "visible-chatgpt-ui-v20" in capture
    assert 'type: "chat.status"' in capture


def test_existing_chat_completions_stream_remains_openai_sse_shape() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'media_type="text/event-stream"' in source
    assert '"X-Accel-Buffering": "no"' in source
    assert 'chunk_payload(response_id, model, {"content": delta})' in source
    assert 'yield "data: [DONE]\\n\\n"' in source
    # v20 visible-status capture deliberately does not modify the standard server
    # streaming endpoint, so clients that do not opt into chat2api-specific features
    # retain the same OpenAI-compatible chunk grammar.
    assert "chat.status" not in source
