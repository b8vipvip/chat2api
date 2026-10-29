"""Stream keepalive must not alter the user's assistant message or terminal."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_server_stream_heartbeat_is_protocol_comment_not_model_content():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    ast.parse(source)
    stream = source[source.index("    async def stream_request("):source.index("    @app.post(\"/v1/chat/completions\")")]
    assert "next_heartbeat_at = started_at + sse_heartbeat_interval(0.0)" in stream
    assert "if now >= next_heartbeat_at:" in stream
    assert 'yield ": chat2api-keepalive\\n\\n"' in stream
    assert 'yield "data: " + json.dumps(chunk_payload(' in stream
    assert 'event_type == "chat.completed"' in stream
    assert "next_heartbeat_at = now + sse_heartbeat_interval(now - started_at)" in stream
    # SSE comments are transport-only. They must never enter message.content,
    # per-request telemetry, or count as progress/terminal decisions.
    assert "sent_text += chat2api" not in stream
    assert "type: chat.completed" not in stream
