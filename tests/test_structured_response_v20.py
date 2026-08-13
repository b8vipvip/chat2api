from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_structured_overlays_load_after_existing_completion_controller_without_version_bump() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.7.5"
    assert "content_reasoning_transport_v20.js" in scripts
    assert "content_format_v20.js" in scripts
    assert scripts.index("content_completion_v6.js") < scripts.index("content_reasoning_transport_v20.js")
    assert scripts.index("content_reasoning_transport_v20.js") < scripts.index("content_format_v20.js")


def test_structured_overlay_javascript_syntax() -> None:
    for name in ["content_reasoning_transport_v20.js", "content_format_v20.js"]:
        subprocess.run(["node", "--check", str(EXTENSION / name)], check=True)


def test_structured_capture_preserves_headings_paragraphs_lists_quotes_and_code() -> None:
    source = (EXTENSION / "content_format_v20.js").read_text(encoding="utf-8")
    assert '"#".repeat(level)' in source
    assert 'tag === "P"' in source
    assert 'tag === "UL" || tag === "OL"' in source
    assert 'tag === "BLOCKQUOTE"' in source
    assert 'tag === "PRE"' in source
    assert 'blocks.join("\\n\\n")' in source
    assert 'format: "markdown"' in source
    assert 'type: "chat.snapshot"' in source


def test_visible_reasoning_is_diagnostics_only_and_not_hidden_chain_of_thought() -> None:
    transport = (EXTENSION / "content_reasoning_transport_v20.js").read_text(encoding="utf-8")
    capture = (EXTENSION / "content_format_v20.js").read_text(encoding="utf-8")
    assert 'event?.type === "chat.status"' in transport
    assert 'type: "chat.diagnostics"' in transport
    assert "visible_reasoning_status" in transport
    assert "visible-chatgpt-ui-v20" in capture
    assert "[role='status']" in capture
    assert "aria-live='polite'" in capture


def test_default_openai_chat_completions_stream_grammar_is_unchanged() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'media_type="text/event-stream"' in source
    assert '"X-Accel-Buffering": "no"' in source
    assert 'chunk_payload(response_id, model, {"content": delta})' in source
    assert 'yield "data: [DONE]\\n\\n"' in source
    assert "chat.status" not in source
