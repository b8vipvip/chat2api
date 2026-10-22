"""Regressions for v0.22.176: one generated PNG was persisted as 2.4MB of
raw data URI in request_history.jsonl despite successful media archival."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from app.request_history_v94_patch import _install_conversation_capture
from app.response_history_media_v188 import history_safe_response_text
from app.telemetry import TelemetryStore


def test_history_strips_large_inline_media_without_weakening_plain_markdown():
    payload = "iVBORw0KGgo" + "A" * 2400000 + "=="
    raw = "Here is your image.\n![Generated image 1](data:image/png;base64," + payload + ")"
    safe = history_safe_response_text(raw)
    assert "Here is your image." in safe
    assert "Generated image 1" in safe
    assert len(safe) < 200
    assert payload[:32] not in safe
    assert "data:image/" not in safe
    assert history_safe_response_text("**Markdown**\n- item\nhttps://example.com") == "**Markdown**\n- item\nhttps://example.com"
    assert history_safe_response_text("![img](https://example.com/a.png)") == "![img](https://example.com/a.png)"
    assert "[media payload stored separately]" in history_safe_response_text(
        "![x](data:image/svg+xml;base64,PHN2Zz4=)"
    )


def test_broker_terminal_capture_never_writes_raw_binary_to_request_jsonl(tmp_path: Path):
    telemetry = TelemetryStore(tmp_path)
    payload = "iVBORw0KGgo" + "B" * 2000000 + "=="
    raw = "![Generated image 1](data:image/png;base64," + payload + ")"
    req = "req_history_image_1"

    class Broker:
        def __init__(self):
            self.requests = {req: SimpleNamespace(text=raw, client_id="ext_test", timings=lambda: {})}
            self.events = []
        async def publish(self, request_id, event):
            self.events.append((request_id, event["type"]))
            return True

    broker = Broker()
    app = FastAPI()
    app.state.telemetry = telemetry
    app.state.broker = broker
    _install_conversation_capture(app)
    assert app.state.broker is broker, "no second broker or terminal controller"
    assert asyncio.run(broker.publish(req, {"type":"chat.completed", "text":raw})) is True
    assert broker.events == [(req,"chat.completed")]
    stored = telemetry.path.read_text(encoding="utf8")
    assert len(stored) < 1500, "the unarchived Base64 must not be appended to JSONL"
    assert "iVBORw0KGgo" not in stored
    assert "data:image/png;base64," not in stored
    assert "[media payload stored separately]" in stored

    owned = "![Generated image 1](https://chat2api.mv3.cn/api/media/med_example/token)"
    async def finish():
        await telemetry.upsert({
            "request_id": req, "response_text": history_safe_response_text(owned),
            "response_text_available": True, "status": "completed",
            "response_media": [{"id":"med_example","url":owned}],
        })
    asyncio.run(finish())
    assert telemetry.get(req)["response_text"] == owned
    assert len(telemetry.path.read_bytes()) < 3500
    assert broker.events == [(req,"chat.completed")], "archival telemetry cannot emit a second terminal"

    # The real API completion path must replace the browser placeholder with
    # exactly the owned-media URL, not copy the data URI a second time.
    main = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf8")
    assert 'history_safe_response_text(final_text)' in main
    assert 'if final_text or status_value == "completed" else {}' in main


def test_unverified_terminal_is_not_allowed_to_persist_inline_payload(tmp_path: Path):
    telemetry = TelemetryStore(tmp_path)
    payload = "X" * 350000
    req = "req_history_error"
    class Broker:
        requests = {}
        async def publish(self, _rid, _event): return True
    app = FastAPI()
    app.state.telemetry = telemetry
    app.state.broker = Broker()
    _install_conversation_capture(app)
    asyncio.run(app.state.broker.publish(req, {
        "type": "chat.error", "text": "data:application/pdf;base64," + payload,
    }))
    assert telemetry.path.stat().st_size < 1000
    assert payload[:256] not in telemetry.path.read_text(encoding="utf8")
